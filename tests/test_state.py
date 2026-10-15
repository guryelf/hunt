"""SQLite state: schema, dedup view, jobs, rejects, review, digest, applications."""

import json
import sqlite3
from datetime import date

import pytest

from hunt import state
from hunt.gate import Candidate, evaluate
from hunt.spec import Constraints, Home, Profile

PROFILE = Profile(
    name="Test Hunter",
    base=Home(city="Istanbul", country="TR"),
    languages={"TR": "native", "EN": "advanced"},
)
CONSTRAINTS = Constraints(countries=["TR"])
FLAGGED_CONSTRAINTS = Constraints(countries=["TR"], remote="anchored")


def _accepted_candidate(url: str = "https://www.kariyer.net/is-ilani/soc-1") -> Candidate:
    return Candidate(
        title="SOC Analisti",
        company="Teknoloji A.Ş.",
        url=url,
        location_raw="Ankara",
        employment="full_time",
        posted_date="2026-10-10",
    )


def _flagged_candidate(
    url: str = "https://www.kariyer.net/is-ilani/remote-soc-1",
) -> Candidate:
    """Remote within an anchored policy — a judgment call, so a flag."""
    return Candidate(
        title="Uzaktan SOC Analisti",
        company="Remote A.Ş.",
        url=url,
        location_raw="Ankara",
        remote=True,
        employment="full_time",
        posted_date="2026-10-10",
    )


def _unverifiable_candidate(url: str = "https://djinni.co/jobs/1-ghost/") -> Candidate:
    return Candidate(title="Ghost Posting", url=url, location_raw="")


def test_connect_creates_schema_idempotently(tmp_path) -> None:
    db = tmp_path / "state.db"
    conn = state.connect(db)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"runs", "jobs", "rejects"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == state.SCHEMA_VERSION
    conn.close()

    state.connect(db).close()  # a second connect must neither fail nor regress


def test_inserted_job_joins_the_dedup_view(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["kariyer"]
    )
    candidate = _accepted_candidate()
    result = evaluate(candidate, CONSTRAINTS, PROFILE, today=date(2026, 10, 16))
    assert result.verdict == "accept"

    state.insert_job(
        conn,
        run_id=run_id,
        track="cyber",
        intent="soc-analyst",
        engine="kariyer",
        candidate=candidate,
        result=result,
    )

    assert result.dedup_key in state.seen_keys(conn)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["verdict"] == "accept"
    assert row["country"] == "TR"
    assert row["flags"] == "[]"


def test_duplicate_sighting_lands_in_rejects(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["kariyer"]
    )
    candidate = _accepted_candidate()
    first = evaluate(candidate, CONSTRAINTS, PROFILE, today=date(2026, 10, 16))
    state.insert_job(
        conn,
        run_id=run_id,
        track="cyber",
        intent="soc-analyst",
        engine="kariyer",
        candidate=candidate,
        result=first,
    )

    second = evaluate(
        candidate, CONSTRAINTS, PROFILE, state.seen_keys(conn), today=date(2026, 10, 16)
    )
    assert (second.verdict, second.reason) == ("reject", "duplicate")
    state.insert_reject(
        conn,
        run_id=run_id,
        track="cyber",
        intent="soc-analyst",
        engine="kariyer",
        candidate=candidate,
        result=second,
    )

    rows = state.recent_rejects(conn)
    assert len(rows) == 1
    assert rows[0]["reason"] == "duplicate"
    assert rows[0]["detail"]  # human-readable sentence present


def test_run_counts_round_trip(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["kariyer", "techcareer"]
    )
    state.finish_run(
        conn,
        run_id,
        status="ok",
        found=3,
        accepted=1,
        flagged=1,
        rejected=1,
        duplicates=0,
        cost_usd=0.42,
    )

    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "ok"
    assert row["found"] == 3
    assert row["accepted"] == 1
    assert row["rejected"] == 1
    assert row["cost_usd"] == 0.42
    assert row["finished_at"] is not None
    assert row["started_at"] <= row["finished_at"]


def _insert(conn, run_id: int, candidate: Candidate, result, *, engine: str = "ai-search") -> None:
    if result.verdict in ("accept", "flag"):
        state.insert_job(
            conn,
            run_id=run_id,
            track="cyber",
            intent="soc-analyst",
            engine=engine,
            candidate=candidate,
            result=result,
        )
    else:
        state.insert_reject(
            conn,
            run_id=run_id,
            track="cyber",
            intent="soc-analyst",
            engine=engine,
            candidate=candidate,
            result=result,
        )


def test_v1_database_migrates_to_v2_without_losing_rows(tmp_path) -> None:
    db = tmp_path / "state.db"
    legacy = sqlite3.connect(db)
    legacy.executescript(state._SCHEMA)  # the v1 shape, as M2 installed it
    legacy.execute(
        "INSERT INTO runs (track, intent, engines, started_at, status)"
        " VALUES ('cyber', 'soc-analyst', '[]', '2026-10-01T00:00:00', 'ok')"
    )
    legacy.execute(
        "INSERT INTO jobs (run_id, track, intent, engine, title, url,"
        " dedup_key, verdict, flags, first_seen_at)"
        " VALUES (1, 'cyber', 'soc-analyst', 'kariyer', 'Old Job',"
        " 'https://x.example/jobs/1', 'k1', 'accept', '[]',"
        " '2026-10-01T00:00:00')"
    )
    legacy.execute("PRAGMA user_version = 1")
    legacy.commit()
    legacy.close()

    conn = state.connect(db)  # must migrate the existing DB, not recreate it
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert {"reviewed_at", "review_note"} <= columns
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "meta" in tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == state.SCHEMA_VERSION

    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["title"] == "Old Job"
    assert row["reviewed_at"] is None  # the added columns start empty
    assert "k1" in state.seen_keys(conn)
    conn.close()


def test_meta_round_trip(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")

    assert state.get_meta(conn, state.LAST_DIGEST_AT) is None
    state.set_meta(conn, state.LAST_DIGEST_AT, "2026-10-16T09:00:00")
    assert state.get_meta(conn, state.LAST_DIGEST_AT) == "2026-10-16T09:00:00"
    state.set_meta(conn, state.LAST_DIGEST_AT, "2026-10-16T12:00:00")
    assert state.get_meta(conn, state.LAST_DIGEST_AT) == "2026-10-16T12:00:00"
    conn.close()


def test_seen_keys_span_jobs_and_rejects(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    accepted = evaluate(
        _accepted_candidate(), CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _accepted_candidate(), accepted)
    rejected = evaluate(
        _unverifiable_candidate(), CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _unverifiable_candidate(), rejected)

    # First provenance wins: both judged postings join the dedup view.
    assert state.seen_keys(conn) == {accepted.dedup_key, rejected.dedup_key}
    conn.close()


def test_resolve_keep_stamps_the_job_reviewed(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    candidate = _flagged_candidate()
    result = evaluate(
        candidate, FLAGGED_CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    assert result.verdict == "flag"
    _insert(conn, run_id, candidate, result)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    assert len(state.flagged_jobs(conn)) == 1

    assert state.resolve_job(conn, job_id, decision="keep", note="anchor ok") is True

    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["reviewed_at"] is not None
    assert row["review_note"] == "anchor ok"
    assert state.flagged_jobs(conn) == []
    # Settling twice is a refusal, not a second write.
    assert state.resolve_job(conn, job_id, decision="keep") is False
    conn.close()


def test_resolve_reject_moves_the_job_to_rejects(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    candidate = _flagged_candidate()
    result = evaluate(
        candidate, FLAGGED_CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, candidate, result)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

    assert state.resolve_job(conn, job_id, decision="reject", note="no visa") is True

    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    reject = conn.execute("SELECT * FROM rejects").fetchone()
    assert reject["run_id"] == run_id  # provenance survives the move
    assert reject["reason"] == state.HUMAN_REJECTED
    assert reject["detail"] == "no visa"
    assert json.loads(reject["raw"])["url"] == candidate.url
    assert state.resolve_job(conn, job_id, decision="reject") is False
    assert state.seen_keys(conn) == {reject["dedup_key"]}

    # A reject without a note gets the plain human-readable sentence.
    second = _flagged_candidate(url="https://www.kariyer.net/is-ilani/remote-soc-2")
    second_result = evaluate(
        second, FLAGGED_CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, second, second_result)
    second_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    assert state.resolve_job(conn, second_id, decision="reject") is True
    detail = conn.execute(
        "SELECT detail FROM rejects WHERE url = ?", (second.url,)
    ).fetchone()[0]
    assert detail == "human rejected it during review"
    conn.close()


def _tracked_job(conn: sqlite3.Connection) -> int:
    """A gate-accepted job row; returns its id."""
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    candidate = _accepted_candidate()
    result = evaluate(candidate, CONSTRAINTS, PROFILE, today=date(2026, 10, 16))
    _insert(conn, run_id, candidate, result)
    return conn.execute("SELECT id FROM jobs").fetchone()[0]


def _shortlisted_app(conn: sqlite3.Connection) -> int:
    """An open application on a tracked job; returns the application id."""
    return state.shortlist_job(conn, _tracked_job(conn), note="strong SOC fit")


def test_v2_database_migrates_to_v3_without_losing_rows(tmp_path) -> None:
    db = tmp_path / "state.db"
    legacy = sqlite3.connect(db)
    legacy.executescript(state._SCHEMA)  # v1 shape…
    legacy.executescript(state._MIGRATE_V2)  # …as M3 installed it
    legacy.execute(
        "INSERT INTO runs (track, intent, engines, started_at, status)"
        " VALUES ('cyber', 'soc-analyst', '[]', '2026-10-01T00:00:00', 'ok')"
    )
    legacy.execute(
        "INSERT INTO jobs (run_id, track, intent, engine, title, url,"
        " dedup_key, verdict, flags, first_seen_at)"
        " VALUES (1, 'cyber', 'soc-analyst', 'kariyer', 'Old Job',"
        " 'https://x.example/jobs/1', 'k1', 'accept', '[]',"
        " '2026-10-01T00:00:00')"
    )
    legacy.execute("PRAGMA user_version = 2")
    legacy.commit()
    legacy.close()

    conn = state.connect(db)  # must migrate the existing DB, not recreate it
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"applications", "application_events"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == state.SCHEMA_VERSION

    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["title"] == "Old Job"
    assert "k1" in state.seen_keys(conn)
    assert state.list_applications(conn) == []
    conn.close()


def test_shortlist_opens_one_application_per_job(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    job_id = _tracked_job(conn)

    app_id = state.shortlist_job(conn, job_id, note="strong SOC fit")

    assert app_id == 1
    row = conn.execute("SELECT * FROM applications").fetchone()
    assert row["job_id"] == job_id
    assert row["status"] == state.SHORTLISTED
    assert row["note"] == "strong SOC fit"
    events = state.application_events(conn, app_id)
    assert [(e["event"], e["detail"]) for e in events] == [
        ("shortlisted", "strong SOC fit")
    ]

    # One posting, one application — however many drafts follow.
    with pytest.raises(ValueError, match="already has application"):
        state.shortlist_job(conn, job_id)
    # A job that was never tracked has nothing to shortlist.
    assert state.shortlist_job(conn, 999) is None
    conn.close()


def test_materials_moves_shortlisted_to_drafted_and_never_regresses(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    app_id = _shortlisted_app(conn)

    row = state.record_materials(
        conn,
        app_id,
        cv="documents/applications/x/cv.pdf",
        cover="documents/applications/x/cover.pdf",
        posting="documents/applications/x/job_posting.md",
    )

    assert row["status"] == state.DRAFTED
    assert row["cv_file"] == "documents/applications/x/cv.pdf"
    assert row["posting_file"] == "documents/applications/x/job_posting.md"
    events = state.application_events(conn, app_id)
    assert events[-1]["event"] == "drafted"
    assert "cv=documents/applications/x/cv.pdf" in events[-1]["detail"]

    state.record_outcome(conn, app_id, status=state.APPLIED, note="sent")

    # Re-recording on an applied application updates paths, not status.
    row = state.record_materials(conn, app_id, cv="documents/applications/x/cv2.pdf")
    assert row["status"] == state.APPLIED
    assert row["cv_file"] == "documents/applications/x/cv2.pdf"
    assert row["cover_file"] == "documents/applications/x/cover.pdf"  # kept
    assert state.application_events(conn, app_id)[-1]["event"] == "materials"

    assert state.record_materials(conn, 99, cv="x.pdf") is None
    conn.close()


def test_funnel_transitions_enforce_forward_only(tmp_path) -> None:
    # The happy path walks strictly forward.
    for current, target in [
        (state.SHORTLISTED, state.DRAFTED),
        (state.DRAFTED, state.APPLIED),
        (state.APPLIED, state.INTERVIEW),
        (state.INTERVIEW, state.OFFER),
        (state.OFFER, state.HIRED),
        (state.SHORTLISTED, state.APPLIED),  # applying without drafting is legal
    ]:
        state.validate_transition(current, target)

    # Withdrawal is the candidate's call from any open stage.
    for open_status in state.OPEN_STATUSES:
        state.validate_transition(open_status, state.WITHDRAWN)

    # Nothing leaves a final status, moves backwards, or repeats itself.
    for current, target in [
        (state.HIRED, state.APPLIED),
        (state.REJECTED, state.INTERVIEW),
        (state.OFFER, state.REJECTED),  # an offer is settled by declining it
        (state.APPLIED, state.DRAFTED),  # backwards
        (state.APPLIED, state.APPLIED),  # a no-op move
        (state.SHORTLISTED, "ghosted"),  # unknown vocabulary
    ]:
        with pytest.raises(ValueError):
            state.validate_transition(current, target)


def test_record_outcome_moves_the_funnel_and_takes_notes(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    app_id = _shortlisted_app(conn)

    row = state.record_outcome(conn, app_id, status=state.APPLIED, note="sent")
    assert row["status"] == state.APPLIED
    assert row["note"] == "sent"

    # A note with no status records feedback without moving anything.
    row = state.record_outcome(conn, app_id, note="recruiter promised Friday")
    assert row["status"] == state.APPLIED
    assert row["note"] == "recruiter promised Friday"
    events = state.application_events(conn, app_id)
    assert [(e["event"], e["detail"]) for e in events] == [
        ("shortlisted", "strong SOC fit"),
        ("applied", "sent"),
        ("note", "recruiter promised Friday"),
    ]

    with pytest.raises(ValueError, match="nothing to record"):
        state.record_outcome(conn, app_id)
    assert state.record_outcome(conn, 99, note="nothing to move") is None
    conn.close()


def test_list_applications_joins_jobs_and_filters(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    accepted = evaluate(
        _accepted_candidate(), CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _accepted_candidate(), accepted)
    flagged = evaluate(
        _flagged_candidate(), FLAGGED_CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _flagged_candidate(), flagged)
    ids = [row[0] for row in conn.execute("SELECT id FROM jobs ORDER BY id")]
    first = state.shortlist_job(conn, ids[0])
    second = state.shortlist_job(conn, ids[1])
    state.record_materials(conn, second, cv="documents/applications/y/cv.pdf")

    rows = state.list_applications(conn)
    assert [row["id"] for row in rows] == [second, first]  # newest first
    assert {"track", "title", "company", "url"} <= set(rows[0].keys())
    assert rows[0]["title"] == "Uzaktan SOC Analisti"  # joined from jobs

    assert [row["id"] for row in state.list_applications(conn, status=state.DRAFTED)] == [
        second
    ]
    assert state.list_applications(conn, track="web-dev") == []
    conn.close()


def test_pool_jobs_is_the_shortlist_pool(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    accepted = evaluate(
        _accepted_candidate(), CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _accepted_candidate(), accepted)
    flagged = evaluate(
        _flagged_candidate(), FLAGGED_CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _flagged_candidate(), flagged)
    ids = {row["title"]: row["id"] for row in conn.execute("SELECT id, title FROM jobs")}

    # An accepted job is in the pool; an unreviewed flag is still the
    # review queue's business, not the pool's.
    assert [row["id"] for row in state.pool_jobs(conn)] == [ids["SOC Analisti"]]

    # A kept flag joins the pool; shortlisting removes a job from it.
    state.resolve_job(conn, ids["Uzaktan SOC Analisti"], decision="keep")
    state.shortlist_job(conn, ids["SOC Analisti"])
    assert [row["id"] for row in state.pool_jobs(conn)] == [
        ids["Uzaktan SOC Analisti"]
    ]

    assert state.pool_jobs(conn, track="web-dev") == []
    conn.close()


def test_resolve_reject_refuses_a_job_in_the_funnel(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    job_id = _tracked_job(conn)
    app_id = state.shortlist_job(conn, job_id)

    # Deleting the job would orphan the application and its timeline.
    with pytest.raises(ValueError, match="has application"):
        state.resolve_job(conn, job_id, decision="reject")
    # A keep still works — it stamps the job, nothing is deleted.
    assert state.resolve_job(conn, job_id, decision="keep") is True
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    assert app_id
    conn.close()


def test_digest_includes_the_funnel_snapshot(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    app_id = _shortlisted_app(conn)
    state.record_materials(conn, app_id, cv="documents/applications/x/cv.pdf")

    assert state.digest(conn)["applications"] == [{"status": "drafted", "n": 1}]
    # The snapshot is a stock count, and it filters by track through the job.
    assert state.digest(conn, track="web-dev")["applications"] == []
    # A windowed read still shows the funnel — it is a stock, not a flow.
    assert state.digest(conn, since="2999-01-01T00:00:00")["applications"] == [
        {"status": "drafted", "n": 1}
    ]
    conn.close()


def test_digest_windows_and_outstanding_flags(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    run_id = state.start_run(
        conn, track="cyber", intent="soc-analyst", engines=["ai-search"]
    )
    accepted = evaluate(
        _accepted_candidate(), CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _accepted_candidate(), accepted)
    flagged = evaluate(
        _flagged_candidate(), FLAGGED_CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _flagged_candidate(), flagged)
    rejected = evaluate(
        _unverifiable_candidate(), CONSTRAINTS, PROFILE, today=date(2026, 10, 16)
    )
    _insert(conn, run_id, _unverifiable_candidate(), rejected)
    state.finish_run(
        conn,
        run_id,
        status="ok",
        found=3,
        accepted=1,
        flagged=1,
        rejected=1,
        duplicates=0,
        cost_usd=0.84,
    )
    flag_id = conn.execute(
        "SELECT id FROM jobs WHERE verdict = 'flag'"
    ).fetchone()[0]

    data = state.digest(conn)
    assert data["tracks"] == [
        {"track": "cyber", "verdict": "accept", "n": 1},
        {"track": "cyber", "verdict": "flag", "n": 1},
    ]
    assert data["new"][0]["title"] == "Uzaktan SOC Analisti"  # newest first
    assert data["flags_outstanding"] == 1
    assert data["reject_reasons"] == {"country_unverifiable": 1}
    assert data["runs"] == 1
    assert data["cost_usd"] == 0.84

    future = state.digest(conn, since="2999-01-01T00:00:00")
    assert future["tracks"] == []
    assert future["new"] == []
    assert future["reject_reasons"] == {}
    assert future["runs"] == 0
    assert future["flags_outstanding"] == 1  # stock, not window

    state.resolve_job(conn, flag_id, decision="keep")
    assert state.digest(conn)["flags_outstanding"] == 0
    conn.close()
