"""CLI behavior: hunt check, hunt add, review/digest/reset, the application funnel."""

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from hunt import state
from hunt.cli import app

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()

GOOD_CANDIDATE = {
    "title": "SOC Analisti",
    "company": "Teknoloji A.Ş.",
    "url": "https://www.kariyer.net/is-ilani/soc-analisti-9",
    "location_raw": "Ankara / Türkiye",
    "employment": "full_time",
    "seniority": "junior",
    "posted_date": "2026-10-14",
}
INCIDENT_CANDIDATE = {
    "title": "Junior Penetration Tester",
    "company": "Let's Recruit",
    "url": "https://djinni.co/jobs/847334-junior-penetration-tester/",
    "location_raw": "",
}
REMOTE_FLAG_CANDIDATE = {
    "title": "Uzaktan SOC Analisti",
    "company": "Remote A.Ş.",
    "url": "https://www.kariyer.net/is-ilani/remote-soc-1",
    "location_raw": "Ankara / Türkiye",
    "remote": True,  # cyber is remote:anchored — a flag, not a reject
    "employment": "full_time",
    "posted_date": "2026-10-14",
}

ADD_ARGS = [
    "add",
    "cyber",
    "--intent",
    "soc-analyst",
    "--today",
    "2026-10-16",
]


def _invoke_add(db_path: Path, payload: str, *extra: str):
    return RUNNER.invoke(
        app,
        [*ADD_ARGS, "--db", str(db_path), "--root", str(ROOT), *extra],
        input=payload,
    )


def test_check_passes_on_the_committed_spec() -> None:
    result = RUNNER.invoke(app, ["check", "--root", str(ROOT)])

    assert result.exit_code == 0
    assert "ok:" in result.output
    assert "profile.yaml" in result.output
    assert "cyber.yaml" in result.output


def test_check_fails_on_a_broken_spec(tmp_path) -> None:
    (tmp_path / "profile.yaml").write_text(
        "name: Broken\nbase:\n  city: X\n", encoding="utf-8"
    )
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    (tracks / "broken.yaml").write_text(
        "track: broken\ndefaults:\n  remote: sometimes\n", encoding="utf-8"
    )

    result = RUNNER.invoke(app, ["check", "--root", str(tmp_path)])

    assert result.exit_code == 1


def test_add_records_verdicts_with_provenance(tmp_path) -> None:
    db = tmp_path / "state.db"
    payload = json.dumps([GOOD_CANDIDATE, INCIDENT_CANDIDATE])

    result = _invoke_add(db, payload, "--cost", "0.31")

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert lines[0]["verdict"] == "accept"
    assert lines[1]["verdict"] == "reject"
    assert lines[1]["reason"] == "board_denied"
    summary = lines[2]
    assert summary["accepted"] == 1
    assert summary["rejected"] == 1
    assert summary["cost_usd"] == 0.31

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["engine"] == "ai-search"
    assert job["run_id"] == summary["run"]
    assert job["verdict"] == "accept"
    assert job["country"] == "TR"
    reject = conn.execute("SELECT * FROM rejects").fetchone()
    assert reject["reason"] == "board_denied"
    run = conn.execute("SELECT * FROM runs").fetchone()
    assert run["found"] == 2
    assert run["cost_usd"] == 0.31


def test_add_flags_duplicates_within_one_batch(tmp_path) -> None:
    db = tmp_path / "state.db"
    payload = json.dumps([GOOD_CANDIDATE, dict(GOOD_CANDIDATE)])

    result = _invoke_add(db, payload)

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert lines[0]["verdict"] == "accept"
    assert (lines[1]["verdict"], lines[1]["reason"]) == ("reject", "duplicate")
    assert lines[2]["duplicates"] == 1


def test_add_rejects_unknown_fields(tmp_path) -> None:
    payload = json.dumps({**GOOD_CANDIDATE, "salary": "lots"})

    result = _invoke_add(tmp_path / "state.db", payload)

    assert result.exit_code == 2


def test_add_rejects_missing_required_fields(tmp_path) -> None:
    payload = json.dumps({"company": "X", "url": "https://x.example/jobs/1"})

    result = _invoke_add(tmp_path / "state.db", payload)

    assert result.exit_code == 2


def test_add_rejects_invalid_json(tmp_path) -> None:
    result = _invoke_add(tmp_path / "state.db", "not json at all")

    assert result.exit_code == 2


def test_add_rejects_bad_today_option(tmp_path) -> None:
    result = _invoke_add(tmp_path / "state.db", "[]", "--today", "the 16th")

    assert result.exit_code == 2


def _first_flag_id(db_path: Path) -> int:
    listed = RUNNER.invoke(app, ["review", "--db", str(db_path)])
    return json.loads(listed.output.strip())["id"]


def test_review_lists_flags_and_resolve_settles(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([REMOTE_FLAG_CANDIDATE]))

    listed = RUNNER.invoke(app, ["review", "--db", str(db)])
    assert listed.exit_code == 0
    line = json.loads(listed.output.strip())
    assert line["verdict"] == "flag"
    assert line["flags"] == ["remote_anchored"]
    assert line["title"] == "Uzaktan SOC Analisti"
    job_id = line["id"]

    settled = RUNNER.invoke(
        app,
        [
            "resolve",
            str(job_id),
            "--decision",
            "keep",
            "--note",
            "anchor ok",
            "--db",
            str(db),
        ],
    )
    assert settled.exit_code == 0
    assert json.loads(settled.output)["decision"] == "keep"

    queued = RUNNER.invoke(app, ["review", "--db", str(db)])
    assert "no flags awaiting review" in queued.output

    # A settled job is never settled twice.
    again = RUNNER.invoke(
        app, ["resolve", str(job_id), "--decision", "keep", "--db", str(db)]
    )
    assert again.exit_code == 2


def test_resolve_reject_moves_job_to_rejects(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([REMOTE_FLAG_CANDIDATE]))
    job_id = _first_flag_id(db)

    result = RUNNER.invoke(
        app,
        [
            "resolve",
            str(job_id),
            "--decision",
            "reject",
            "--note",
            "no visa",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    reject = conn.execute("SELECT * FROM rejects").fetchone()
    assert reject["reason"] == "human_rejected"
    assert reject["detail"] == "no visa"
    assert json.loads(reject["raw"])["url"] == REMOTE_FLAG_CANDIDATE["url"]
    conn.close()

    shown = RUNNER.invoke(app, ["rejects", "--db", str(db)])
    assert "human_rejected" in shown.output


def test_resolve_rejects_a_bad_decision(tmp_path) -> None:
    result = RUNNER.invoke(
        app,
        ["resolve", "1", "--decision", "maybe", "--db", str(tmp_path / "state.db")],
    )

    assert result.exit_code == 2


def test_digest_advances_watermark(tmp_path, monkeypatch) -> None:
    clock = {"t": "2026-10-16T10:00:00"}
    monkeypatch.setattr(state, "now", lambda: clock["t"])
    db = tmp_path / "state.db"

    _invoke_add(db, json.dumps([GOOD_CANDIDATE, REMOTE_FLAG_CANDIDATE]))
    first = RUNNER.invoke(app, ["digest", "--db", str(db)])
    assert first.exit_code == 0
    data = json.loads(first.output)
    assert {t["verdict"] for t in data["tracks"]} == {"accept", "flag"}
    assert data["flags_outstanding"] == 1

    clock["t"] = "2026-10-16T10:00:05"
    second = RUNNER.invoke(app, ["digest", "--db", str(db)])
    assert json.loads(second.output)["tracks"] == []

    clock["t"] = "2026-10-16T10:00:10"
    _invoke_add(
        db,
        json.dumps(
            [dict(GOOD_CANDIDATE, url="https://www.kariyer.net/is-ilani/soc-10")]
        ),
    )
    third = RUNNER.invoke(app, ["digest", "--db", str(db)])
    data = json.loads(third.output)
    assert data["tracks"] == [{"track": "cyber", "verdict": "accept", "n": 1}]
    assert data["new"][0]["url"] == "https://www.kariyer.net/is-ilani/soc-10"
    assert data["flags_outstanding"] == 1  # stock, not window

    windowed = RUNNER.invoke(
        app, ["digest", "--since", "2000-01-01T00:00:00", "--db", str(db)]
    )
    data = json.loads(windowed.output)
    assert {t["verdict"] for t in data["tracks"]} == {"accept", "flag"}

    # --since reads a window; it must not move the watermark.
    conn = sqlite3.connect(db)
    watermark = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (state.LAST_DIGEST_AT,)
    ).fetchone()[0]
    conn.close()
    assert watermark == "2026-10-16T10:00:10"


def test_reset_requires_confirm_then_archives(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE]))

    refused = RUNNER.invoke(app, ["reset", "--db", str(db)])
    assert refused.exit_code == 1
    assert db.exists()  # nothing happened without --confirm

    done = RUNNER.invoke(app, ["reset", "--confirm", "--db", str(db)])
    assert done.exit_code == 0
    assert not db.exists()
    assert len(list((tmp_path / "backups").glob("state-*.db"))) == 1

    missing = RUNNER.invoke(app, ["reset", "--confirm", "--db", str(db)])
    assert missing.exit_code == 0
    assert "no state database" in missing.output


def _first_job_id(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    job_id = conn.execute("SELECT id FROM jobs ORDER BY id").fetchone()[0]
    conn.close()
    return job_id


def test_shortlist_materials_outcome_applications_walk_the_funnel(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE]))
    job_id = _first_job_id(db)

    opened = RUNNER.invoke(
        app, ["shortlist", str(job_id), "--note", "smoke", "--db", str(db)]
    )
    assert opened.exit_code == 0
    app_id = json.loads(opened.output)["application"]

    shown = RUNNER.invoke(app, ["job", str(job_id), "--db", str(db)])
    payload = json.loads(shown.output)
    assert payload["application"]["status"] == "shortlisted"
    assert payload["title"] == "SOC Analisti"

    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-smoke")
    cover = tmp_path / "cover.pdf"
    cover.write_bytes(b"%PDF-smoke")
    posting = tmp_path / "job_posting.md"
    posting.write_text("# the posting, verbatim")
    recorded = RUNNER.invoke(
        app,
        [
            "materials",
            str(app_id),
            "--cv",
            str(cv),
            "--cover",
            str(cover),
            "--posting",
            str(posting),
            "--db",
            str(db),
        ],
    )
    assert recorded.exit_code == 0
    data = json.loads(recorded.output)
    assert data["status"] == "drafted"
    assert data["cv"].endswith("cv.pdf")

    listed = RUNNER.invoke(app, ["applications", "--db", str(db)])
    line = json.loads(listed.output)
    assert line["title"] == "SOC Analisti"
    assert line["status"] == "drafted"

    sent = RUNNER.invoke(
        app, ["outcome", str(app_id), "--status", "applied", "--db", str(db)]
    )
    assert json.loads(sent.output)["status"] == "applied"
    stage = RUNNER.invoke(
        app, ["outcome", str(app_id), "--status", "interview", "--db", str(db)]
    )
    assert json.loads(stage.output)["status"] == "interview"
    hired = RUNNER.invoke(
        app, ["outcome", str(app_id), "--status", "hired", "--db", str(db)]
    )
    assert json.loads(hired.output)["status"] == "hired"

    filtered = RUNNER.invoke(
        app, ["applications", "--status", "hired", "--db", str(db)]
    )
    assert json.loads(filtered.output)["status"] == "hired"

    # The funnel never moves backwards: hired is settled history.
    back = RUNNER.invoke(
        app, ["outcome", str(app_id), "--status", "applied", "--db", str(db)]
    )
    assert back.exit_code == 2
    assert "final status" in back.output


def test_shortlist_refuses_missing_jobs_and_duplicates(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE]))
    job_id = _first_job_id(db)

    missing = RUNNER.invoke(app, ["shortlist", "99", "--db", str(db)])
    assert missing.exit_code == 2
    assert "no tracked job" in missing.output

    assert RUNNER.invoke(
        app, ["shortlist", str(job_id), "--db", str(db)]
    ).exit_code == 0
    twice = RUNNER.invoke(app, ["shortlist", str(job_id), "--db", str(db)])
    assert twice.exit_code == 2
    assert "already has application" in twice.output


def test_materials_demands_real_files_and_a_real_application(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE]))
    job_id = _first_job_id(db)
    app_id = json.loads(
        RUNNER.invoke(
            app, ["shortlist", str(job_id), "--db", str(db)]
        ).output
    )["application"]

    ghost = RUNNER.invoke(
        app, ["materials", str(app_id), "--cv", "nope.pdf", "--db", str(db)]
    )
    assert ghost.exit_code == 2
    assert "no such file" in ghost.output

    bare = RUNNER.invoke(app, ["materials", str(app_id), "--db", str(db)])
    assert bare.exit_code == 2

    # An existing file can be attached to a missing application —
    # the file check passes, the application lookup must refuse.
    real = tmp_path / "real.pdf"
    real.write_bytes(b"%PDF-smoke")
    absent = RUNNER.invoke(
        app, ["materials", "99", "--cv", str(real), "--db", str(db)]
    )
    assert absent.exit_code == 2
    assert "no application" in absent.output

    # The resolve guard: a job in the funnel is never deleted out from
    # under its application.
    reject = RUNNER.invoke(
        app, ["resolve", str(job_id), "--decision", "reject", "--db", str(db)]
    )
    assert reject.exit_code == 2
    assert "has application" in reject.output


def test_outcome_validates_status_and_takes_notes(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE]))
    app_id = json.loads(
        RUNNER.invoke(
            app, ["shortlist", str(_first_job_id(db)), "--db", str(db)]
        ).output
    )["application"]

    unknown = RUNNER.invoke(
        app, ["outcome", str(app_id), "--status", "ghosted", "--db", str(db)]
    )
    assert unknown.exit_code == 2
    assert "unknown status" in unknown.output

    # A shortlisted application cannot jump straight to hired.
    jump = RUNNER.invoke(
        app, ["outcome", str(app_id), "--status", "hired", "--db", str(db)]
    )
    assert jump.exit_code == 2
    assert "not a funnel transition" in jump.output

    bare = RUNNER.invoke(app, ["outcome", str(app_id), "--db", str(db)])
    assert bare.exit_code == 2

    noted = RUNNER.invoke(
        app, ["outcome", str(app_id), "--note", "waiting on referral", "--db", str(db)]
    )
    assert noted.exit_code == 0
    data = json.loads(noted.output)
    assert data["status"] == "shortlisted"  # a note moves nothing
    assert data["note"] == "waiting on referral"

    bogus_filter = RUNNER.invoke(
        app, ["applications", "--status", "bogus", "--db", str(db)]
    )
    assert bogus_filter.exit_code == 2

    empty = RUNNER.invoke(app, ["applications", "--track", "web-dev", "--db", str(db)])
    assert empty.exit_code == 0
    assert "no matching applications" in empty.output


def test_pool_lists_what_awaits_shortlisting(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE, REMOTE_FLAG_CANDIDATE]))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ids = {row["title"]: row["id"] for row in conn.execute("SELECT id, title FROM jobs")}
    conn.close()

    listed = RUNNER.invoke(app, ["pool", "--db", str(db)])
    line = json.loads(listed.output.strip())  # the unreviewed flag is absent
    assert line["title"] == "SOC Analisti"
    assert line["flags"] == []

    # Keeping the flag adds it to the pool; shortlisting empties it of the other.
    RUNNER.invoke(
        app,
        [
            "resolve",
            str(ids["Uzaktan SOC Analisti"]),
            "--decision",
            "keep",
            "--db",
            str(db),
        ],
    )
    RUNNER.invoke(app, ["shortlist", str(ids["SOC Analisti"]), "--db", str(db)])
    listed = RUNNER.invoke(app, ["pool", "--db", str(db)])
    line = json.loads(listed.output.strip())
    assert line["title"] == "Uzaktan SOC Analisti"
    assert line["flags"] == ["remote_anchored"]

    empty = RUNNER.invoke(app, ["pool", "--track", "web-dev", "--db", str(db)])
    assert empty.exit_code == 0
    assert "no tracked jobs awaiting shortlist" in empty.output


def test_digest_reports_the_funnel_snapshot(tmp_path) -> None:
    db = tmp_path / "state.db"
    _invoke_add(db, json.dumps([GOOD_CANDIDATE]))
    app_id = json.loads(
        RUNNER.invoke(
            app, ["shortlist", str(_first_job_id(db)), "--db", str(db)]
        ).output
    )["application"]
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-smoke")
    RUNNER.invoke(
        app, ["materials", str(app_id), "--cv", str(cv), "--db", str(db)]
    )

    data = json.loads(RUNNER.invoke(app, ["digest", "--db", str(db)]).output)
    assert data["applications"] == [{"status": "drafted", "n": 1}]
