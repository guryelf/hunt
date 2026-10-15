"""The djinni regression: the 2026-10-15 incident, replayed through the gate.

On 2026-10-15 an AI-search run whose entire spec was the two-word focus
"Offensive Security" pulled these two djinni.co postings into the old
tracker with empty location fields — and the fit scorer graded them
"medium". M1 is done when the same inputs produce rejects rows, never
job rows. This is the acceptance criterion written into DESIGN.md.
"""

from datetime import date
from pathlib import Path

from hunt import state
from hunt.gate import BOARD_DENIED, COUNTRY_UNVERIFIABLE, Candidate, evaluate
from hunt.spec import load_profile, load_track

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 16)

# Exactly what v1 recorded, empty location fields included.
INCIDENT = [
    Candidate(
        title="Junior Penetration Tester",
        company="Let's Recruit",
        url=(
            "https://djinni.co/jobs/847334-junior-penetration-tester/"
            "?utm_source=freehire.me"
        ),
        location_raw="",
    ),
    Candidate(
        title="Middle Penetration Tester",
        company="Let's Recruit",
        url=(
            "https://djinni.co/jobs/847338-middle-penetration-tester/"
            "?utm_source=freehire.me"
        ),
        location_raw="",
    ),
]


def _run_incident_through_gate(conn, constraints) -> None:
    """Feed the incident candidates through gate + state like `hunt run`."""
    profile = load_profile(ROOT / "profile.yaml")
    run_id = state.start_run(
        conn, track="cyber", intent="offensive-security", engines=["ai-search"]
    )
    seen = state.seen_keys(conn)
    accepted = flagged = rejected = duplicates = 0
    for candidate in INCIDENT:
        result = evaluate(candidate, constraints, profile, seen, today=TODAY)
        if result.verdict in ("accept", "flag"):
            state.insert_job(
                conn,
                run_id=run_id,
                track="cyber",
                intent="offensive-security",
                engine="ai-search",
                candidate=candidate,
                result=result,
            )
            seen.add(result.dedup_key)
            if result.verdict == "accept":
                accepted += 1
            else:
                flagged += 1
        else:
            state.insert_reject(
                conn,
                run_id=run_id,
                track="cyber",
                intent="offensive-security",
                engine="ai-search",
                candidate=candidate,
                result=result,
            )
            rejected += 1
            if result.reason == "duplicate":
                duplicates += 1
    state.finish_run(
        conn,
        run_id,
        status="ok",
        found=len(INCIDENT),
        accepted=accepted,
        flagged=flagged,
        rejected=rejected,
        duplicates=duplicates,
    )


def _reasons(conn) -> list[str]:
    return [
        row["reason"]
        for row in conn.execute("SELECT reason FROM rejects ORDER BY id")
    ]


def test_incident_inputs_never_become_jobs(tmp_path) -> None:
    conn = state.connect(tmp_path / "state.db")
    track = load_track(ROOT / "tracks" / "cyber.yaml")
    constraints = track.intent("offensive-security").constraints(track.defaults)

    _run_incident_through_gate(conn, constraints)

    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert _reasons(conn) == [BOARD_DENIED, BOARD_DENIED]


def test_empty_location_alone_still_rejects(tmp_path) -> None:
    """Even if djinni.co were ever trusted again, empty location cannot pass."""
    conn = state.connect(tmp_path / "state.db")
    track = load_track(ROOT / "tracks" / "cyber.yaml")
    constraints = track.intent("offensive-security").constraints(track.defaults)
    constraints = constraints.model_copy(update={"exclude_boards": []})

    _run_incident_through_gate(conn, constraints)

    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert _reasons(conn) == [COUNTRY_UNVERIFIABLE, COUNTRY_UNVERIFIABLE]
    rejects = conn.execute("SELECT detail FROM rejects ORDER BY id").fetchall()
    for row in rejects:
        assert "never a pass by default" in row["detail"]
