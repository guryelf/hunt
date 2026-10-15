"""SQLite state: runs, jobs, rejects, applications — provenance for everything.

The DB is generated data, never spec (gitignored; see .gitignore). Its
schema migrates forward via PRAGMA user_version, deliberately without an
external migration framework — a handful of small tables does not need
one. Migrating is additive only: an old DB never loses rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .gate import (
    BOARD_DENIED,
    COUNTRY_DENIED,
    COUNTRY_OUT_OF_SCOPE,
    COUNTRY_UNVERIFIABLE,
    DUPLICATE,
    EMPLOYMENT_MISMATCH,
    LANGUAGE_UNDECLARED,
    REMOTE_NOT_ALLOWED,
    STALE_POSTING,
    Candidate,
    GateResult,
)

SCHEMA_VERSION = 3

# v1 base schema, kept verbatim as the migration ladder's first rung.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    track TEXT NOT NULL,
    intent TEXT NOT NULL,
    engines TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    cost_usd REAL,
    found INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    track TEXT NOT NULL,
    intent TEXT NOT NULL,
    engine TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    url TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    country TEXT,
    city TEXT,
    remote INTEGER NOT NULL DEFAULT 0,
    employment TEXT,
    seniority TEXT,
    posted_date TEXT,
    description TEXT,
    verdict TEXT NOT NULL,
    flags TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_track ON jobs(track);

CREATE TABLE IF NOT EXISTS rejects (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    track TEXT NOT NULL,
    intent TEXT NOT NULL,
    engine TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    url TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    country TEXT,
    reason TEXT NOT NULL,
    detail TEXT,
    raw TEXT,
    rejected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejects_run ON rejects(run_id);
"""

# v2: human review columns on jobs, and the meta key-value table that
# holds the digest watermark.
_MIGRATE_V2 = """
ALTER TABLE jobs ADD COLUMN reviewed_at TEXT;
ALTER TABLE jobs ADD COLUMN review_note TEXT;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# v3: the application funnel. One application per tracked job — applying
# twice to the same posting is a new cover letter, not a new row. Every
# change writes an event row, so an application keeps its own timeline
# the way a run keeps its provenance.
_MIGRATE_V3 = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    status TEXT NOT NULL,
    note TEXT,
    cv_file TEXT,
    cover_file TEXT,
    posting_file TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    event TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
"""

# The funnel, forward only. OPEN statuses can still move; FINAL statuses
# are settled history — "never backwards" includes un-settling a reject
# because the same posting resurfaced. withdrawn is reachable from any
# open status: pulling out is always the candidate's call.
SHORTLISTED = "shortlisted"
DRAFTED = "drafted"
APPLIED = "applied"
INTERVIEW = "interview"
OFFER = "offer"
HIRED = "hired"
REJECTED = "rejected"
NO_RESPONSE = "no_response"
OFFER_DECLINED = "offer_declined"
WITHDRAWN = "withdrawn"

OPEN_STATUSES = frozenset({SHORTLISTED, DRAFTED, APPLIED, INTERVIEW, OFFER})
FINAL_STATUSES = frozenset({HIRED, REJECTED, NO_RESPONSE, OFFER_DECLINED, WITHDRAWN})
ALL_STATUSES = OPEN_STATUSES | FINAL_STATUSES

_TRANSITIONS = {
    SHORTLISTED: frozenset({DRAFTED, APPLIED, WITHDRAWN}),
    DRAFTED: frozenset({APPLIED, WITHDRAWN}),
    APPLIED: frozenset({INTERVIEW, OFFER, HIRED, REJECTED, NO_RESPONSE, WITHDRAWN}),
    INTERVIEW: frozenset({OFFER, HIRED, REJECTED, NO_RESPONSE, WITHDRAWN}),
    OFFER: frozenset({HIRED, OFFER_DECLINED, WITHDRAWN}),
}

LAST_DIGEST_AT = "last_digest_at"

# Not a gate reason: the gate never emits it. It marks the postings the
# human rejected during /review — recorded with the same provenance as
# gate rejects so the audit trail stays in one table.
HUMAN_REJECTED = "human_rejected"

_DETAILS = {
    BOARD_DENIED: "posting board is excluded as unreliable",
    COUNTRY_DENIED: "country is a hard exclusion",
    COUNTRY_OUT_OF_SCOPE: "country is outside every allowed scope",
    COUNTRY_UNVERIFIABLE: "location could not be verified — never a pass by default",
    REMOTE_NOT_ALLOWED: "remote work is not allowed for this track",
    EMPLOYMENT_MISMATCH: "employment type is not allowed",
    STALE_POSTING: "posting is older than max_age_days",
    LANGUAGE_UNDECLARED: "posting requires an undeclared language",
    DUPLICATE: "already tracked — recorded as a duplicate sighting",
    HUMAN_REJECTED: "human rejected it during review",
}


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the state DB, creating or migrating its schema if needed."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA user_version = 1")
    if version < 2:
        conn.executescript(_MIGRATE_V2)
        conn.execute("PRAGMA user_version = 2")
    if version < 3:
        conn.executescript(_MIGRATE_V3)
        conn.execute("PRAGMA user_version = 3")
    conn.commit()
    return conn


def now() -> str:
    """UTC timestamp for row bookkeeping — ISO seconds, comparable as text."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Read one meta value; None when the key was never written."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def start_run(
    conn: sqlite3.Connection, *, track: str, intent: str, engines: list[str]
) -> int:
    """Record the start of an orchestration run; returns its id."""
    cursor = conn.execute(
        "INSERT INTO runs (track, intent, engines, started_at, status)"
        " VALUES (?, ?, ?, ?, 'running')",
        (track, intent, json.dumps(engines), now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    found: int,
    accepted: int,
    flagged: int,
    rejected: int,
    duplicates: int,
    cost_usd: float | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, found = ?, accepted = ?,"
        " flagged = ?, rejected = ?, duplicates = ?, cost_usd = ?"
        " WHERE id = ?",
        (now(), status, found, accepted, flagged, rejected, duplicates, cost_usd, run_id),
    )
    conn.commit()


def seen_keys(conn: sqlite3.Connection) -> set[str]:
    """Dedup keys of every judged posting — tracked or rejected.

    First provenance wins: a posting the gate has already judged is not
    re-judged on later runs, so rejects never pile up as duplicate rows
    for the same URL, and a reject cannot silently become a job later
    when the spec changes. Re-clearing history is `hunt reset`'s job.
    """
    return {
        row[0]
        for row in conn.execute(
            "SELECT dedup_key FROM jobs UNION SELECT dedup_key FROM rejects"
        )
    }


def insert_job(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    track: str,
    intent: str,
    engine: str,
    candidate: Candidate,
    result: GateResult,
) -> None:
    conn.execute(
        "INSERT INTO jobs (run_id, track, intent, engine, title, company, url,"
        " dedup_key, country, city, remote, employment, seniority, posted_date,"
        " description, verdict, flags, first_seen_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            track,
            intent,
            engine,
            candidate.title,
            candidate.company,
            candidate.url,
            result.dedup_key,
            result.country,
            result.city,
            int(result.remote),
            candidate.employment,
            candidate.seniority,
            candidate.posted_date,
            candidate.description,
            result.verdict,
            json.dumps(result.flags),
            now(),
        ),
    )
    conn.commit()


def insert_reject(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    track: str,
    intent: str,
    engine: str,
    candidate: Candidate,
    result: GateResult,
) -> None:
    raw = {
        "title": candidate.title,
        "company": candidate.company,
        "url": candidate.url,
        "location_raw": candidate.location_raw,
        "remote": candidate.remote,
        "employment": candidate.employment,
        "seniority": candidate.seniority,
        "posted_date": candidate.posted_date,
    }
    detail = _DETAILS.get(result.reason or "", result.reason)
    if result.country:
        detail = f"{result.country}: {detail}"
    conn.execute(
        "INSERT INTO rejects (run_id, track, intent, engine, title, company, url,"
        " dedup_key, country, reason, detail, raw, rejected_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            track,
            intent,
            engine,
            candidate.title,
            candidate.company,
            candidate.url,
            result.dedup_key,
            result.country,
            result.reason,
            detail,
            json.dumps(raw, ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()


def recent_rejects(
    conn: sqlite3.Connection, *, track: str | None = None, limit: int = 20
) -> list[sqlite3.Row]:
    """Latest gate rejects with their machine reasons."""
    if track:
        return conn.execute(
            "SELECT * FROM rejects WHERE track = ?"
            " ORDER BY id DESC LIMIT ?",
            (track, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM rejects ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def flagged_jobs(
    conn: sqlite3.Connection, *, track: str | None = None, limit: int = 25
) -> list[sqlite3.Row]:
    """Flagged jobs still awaiting a human decision, oldest first.

    Keeps and hand-rejects remove a job from this queue; a skip leaves
    it for the next /review — skipped is deferred, not discarded.
    """
    if track:
        return conn.execute(
            "SELECT * FROM jobs WHERE verdict = 'flag' AND reviewed_at IS NULL"
            " AND track = ? ORDER BY id LIMIT ?",
            (track, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM jobs WHERE verdict = 'flag' AND reviewed_at IS NULL"
        " ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()


def resolve_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    decision: str,
    note: str | None = None,
) -> bool:
    """Record the human's decision on one tracked job.

    keep: stamp reviewed_at — the job stays tracked.
    reject: move it to rejects with reason ``human_rejected``; the row's
    fields survive in the reject's ``raw`` payload and the original run
    keeps its provenance. The gate's own verdicts are never rewritten.
    Returns False when the job is missing or already reviewed.
    """
    if decision not in ("keep", "reject"):
        raise ValueError(f"unknown decision: {decision}")
    if decision == "reject":
        # A job with an application is still in the funnel; deleting it
        # would orphan the application and its timeline, and the FK
        # forbids it. Funnel history is settled through hunt outcome,
        # never by rejecting the job out from under it.
        existing = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"job #{job_id} has application #{existing['id']} — settle"
                " the pursuit with hunt outcome (withdrawn if it is over);"
                " its funnel history is never deleted"
            )
    job = conn.execute(
        "SELECT * FROM jobs WHERE id = ? AND reviewed_at IS NULL", (job_id,)
    ).fetchone()
    if job is None:
        return False
    if decision == "keep":
        conn.execute(
            "UPDATE jobs SET reviewed_at = ?, review_note = ? WHERE id = ?",
            (now(), note, job_id),
        )
    else:
        row = dict(job)  # a sqlite3.Row iterates values; a dict iterates keys
        raw = {key: row[key] for key in row if key not in ("id", "run_id")}
        conn.execute(
            "INSERT INTO rejects (run_id, track, intent, engine, title, company,"
            " url, dedup_key, country, reason, detail, raw, rejected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job["run_id"],
                job["track"],
                job["intent"],
                job["engine"],
                job["title"],
                job["company"],
                job["url"],
                job["dedup_key"],
                job["country"],
                HUMAN_REJECTED,
                note or _DETAILS[HUMAN_REJECTED],
                json.dumps(raw, ensure_ascii=False),
                now(),
            ),
        )
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    return True


def validate_transition(current: str, target: str) -> None:
    """Raise ValueError unless current → target is a legal funnel move."""
    if target not in ALL_STATUSES:
        raise ValueError(f"unknown status: {target}")
    if target == current:
        raise ValueError(f"application is already {current}")
    if current in FINAL_STATUSES:
        raise ValueError(f"{current} is a final status — the funnel never leaves it")
    allowed = _TRANSITIONS[current]
    if target not in allowed:
        raise ValueError(
            f"{current} → {target} is not a funnel transition"
            f" (from {current}: {', '.join(sorted(allowed))})"
        )


def _add_event(
    conn: sqlite3.Connection, application_id: int, event: str, detail: str | None
) -> None:
    conn.execute(
        "INSERT INTO application_events (application_id, event, detail, at)"
        " VALUES (?, ?, ?, ?)",
        (application_id, event, detail, now()),
    )


def _application(
    conn: sqlite3.Connection, application_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()


def shortlist_job(
    conn: sqlite3.Connection, job_id: int, *, note: str | None = None
) -> int | None:
    """Open an application for a tracked job; returns its id.

    None when the job does not exist; ValueError when it already has an
    application — one posting, one funnel row, however many drafts.
    """
    job = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        return None
    existing = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if existing is not None:
        raise ValueError(
            f"job #{job_id} already has application #{existing['id']}"
            " — record new drafts with hunt materials, not a second application"
        )
    stamp = now()
    cursor = conn.execute(
        "INSERT INTO applications (job_id, status, note, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (job_id, SHORTLISTED, note, stamp, stamp),
    )
    application_id = int(cursor.lastrowid)
    _add_event(conn, application_id, SHORTLISTED, note)
    conn.commit()
    return application_id


def record_materials(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    cv: str | None = None,
    cover: str | None = None,
    posting: str | None = None,
) -> sqlite3.Row | None:
    """Attach drafted files; shortlisted becomes drafted.

    Paths are recorded exactly as given (the CLI checks they exist), so
    an application points at real artifacts, not intentions. A path
    given as None keeps what is already recorded. Re-recording never
    regresses: an application already past drafting keeps its status and
    just points at the newer files. Returns the updated row, or None
    when the application is missing.
    """
    app = _application(conn, application_id)
    if app is None:
        return None
    detail = "; ".join(
        f"{label}={path}"
        for label, path in (("cv", cv), ("cover", cover), ("posting", posting))
        if path
    )
    status = app["status"]
    event = "materials"
    if status == SHORTLISTED:
        status = DRAFTED
        event = DRAFTED
    conn.execute(
        "UPDATE applications SET cv_file = COALESCE(?, cv_file),"
        " cover_file = COALESCE(?, cover_file),"
        " posting_file = COALESCE(?, posting_file),"
        " status = ?, updated_at = ? WHERE id = ?",
        (cv, cover, posting, status, now(), application_id),
    )
    _add_event(conn, application_id, event, detail or None)
    conn.commit()
    return _application(conn, application_id)


def record_outcome(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    status: str | None = None,
    note: str | None = None,
) -> sqlite3.Row | None:
    """Move an application through the funnel, or attach a note.

    A status move must be a legal forward transition (ValueError
    otherwise). A note with no status records feedback — a date, a
    recruiter's promise — on the timeline without moving anything.
    Returns the updated row, or None when the application is missing.
    """
    app = _application(conn, application_id)
    if app is None:
        return None
    if status is None and note is None:
        raise ValueError("nothing to record: pass a status or a note")
    if status is not None:
        validate_transition(app["status"], status)
    conn.execute(
        "UPDATE applications SET status = COALESCE(?, status),"
        " note = COALESCE(?, note), updated_at = ? WHERE id = ?",
        (status, note, now(), application_id),
    )
    _add_event(conn, application_id, status or "note", note)
    conn.commit()
    return _application(conn, application_id)


def list_applications(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    track: str | None = None,
) -> list[sqlite3.Row]:
    """Applications with their job's context, newest first.

    The top of the list is what needs action now; the bottom is settled
    history. Track lives on the job, so filtering by it joins.
    """
    query = (
        "SELECT a.id, a.job_id, a.status, a.note, a.cv_file, a.cover_file,"
        " a.posting_file, a.created_at, a.updated_at,"
        " j.track, j.title, j.company, j.url"
        " FROM applications a JOIN jobs j ON j.id = a.job_id"
    )
    where: list[str] = []
    params: list[object] = []
    if status is not None:
        where.append("a.status = ?")
        params.append(status)
    if track is not None:
        where.append("j.track = ?")
        params.append(track)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY a.id DESC"
    return conn.execute(query, params).fetchall()


def application_events(
    conn: sqlite3.Connection, application_id: int
) -> list[sqlite3.Row]:
    """One application's timeline, in the order it happened."""
    return conn.execute(
        "SELECT event, detail, at FROM application_events"
        " WHERE application_id = ? ORDER BY id",
        (application_id,),
    ).fetchall()


def pool_jobs(
    conn: sqlite3.Connection, *, track: str | None = None, limit: int = 25
) -> list[sqlite3.Row]:
    """The shortlist pool: tracked jobs with no application yet, newest first.

    Gate accepts and human-kept flags qualify; an unreviewed flag is
    still the review queue's business, not the pool's. A job leaves
    the pool the moment an application opens on it.
    """
    query = (
        "SELECT j.id, j.track, j.verdict, j.flags, j.title, j.company,"
        " j.country, j.city, j.remote, j.employment, j.posted_date, j.url,"
        " j.first_seen_at, j.reviewed_at"
        " FROM jobs j LEFT JOIN applications a ON a.job_id = j.id"
        " WHERE a.id IS NULL AND (j.verdict = 'accept' OR j.reviewed_at IS NOT NULL)"
    )
    params: list[object] = []
    if track:
        query += " AND j.track = ?"
        params.append(track)
    query += " ORDER BY j.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def digest(
    conn: sqlite3.Connection,
    *,
    track: str | None = None,
    since: str | None = None,
) -> dict:
    """Per-track rollup of what changed since a watermark.

    ``since`` compares against ISO-second timestamps (first_seen_at,
    rejected_at, started_at) as text; None means all time. Outstanding
    flags are a stock number, not a window number — they are what
    awaits the human regardless of when they were found.
    """

    def clauses(*columns: str) -> tuple[str, list[object]]:
        where: list[str] = []
        params: list[object] = []
        if track:
            where.append("track = ?")
            params.append(track)
        for column in columns:
            if since:
                where.append(f"{column} > ?")
                params.append(since)
        return " AND ".join(where) or "1=1", params

    jobs_where, jobs_params = clauses("first_seen_at")
    tracks = [
        {"track": row["track"], "verdict": row["verdict"], "n": row["n"]}
        for row in conn.execute(
            "SELECT track, verdict, COUNT(*) AS n FROM jobs"
            f" WHERE {jobs_where} GROUP BY track, verdict"
            " ORDER BY track, verdict",
            jobs_params,
        )
    ]
    newest = [
        {
            "track": row["track"],
            "verdict": row["verdict"],
            "title": row["title"],
            "company": row["company"],
            "country": row["country"],
            "url": row["url"],
        }
        for row in conn.execute(
            "SELECT track, verdict, title, company, country, url FROM jobs"
            f" WHERE {jobs_where} ORDER BY id DESC LIMIT 20",
            jobs_params,
        )
    ]
    flags_where, flags_params = clauses()
    outstanding = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE verdict = 'flag' AND reviewed_at IS NULL"
        f" AND {flags_where}",
        flags_params,
    ).fetchone()[0]
    rejects_where, rejects_params = clauses("rejected_at")
    reasons = {
        row["reason"]: row["n"]
        for row in conn.execute(
            "SELECT reason, COUNT(*) AS n FROM rejects"
            f" WHERE {rejects_where} GROUP BY reason ORDER BY n DESC",
            rejects_params,
        )
    }
    runs_where, runs_params = clauses("started_at")
    run_row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost"
        f" FROM runs WHERE {runs_where}",
        runs_params,
    ).fetchone()
    # The funnel is a stock snapshot like outstanding flags, not a
    # window: application rows mutate as they advance, so a window
    # would count the same application once per stage it passed.
    funnel_query = (
        "SELECT a.status, COUNT(*) AS n FROM applications a"
        " JOIN jobs j ON j.id = a.job_id"
    )
    funnel_params: list[object] = []
    if track:
        funnel_query += " WHERE j.track = ?"
        funnel_params.append(track)
    funnel_query += " GROUP BY a.status ORDER BY a.status"
    funnel = [
        {"status": row["status"], "n": row["n"]}
        for row in conn.execute(funnel_query, funnel_params)
    ]
    return {
        "since": since,
        "tracks": tracks,
        "new": newest,
        "flags_outstanding": outstanding,
        "reject_reasons": reasons,
        "runs": run_row["n"],
        "cost_usd": run_row["cost"],
        "applications": funnel,
    }


def summary(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs per track and verdict, plus the latest run for context."""
    return conn.execute(
        "SELECT track, verdict, COUNT(*) AS n FROM jobs GROUP BY track, verdict"
        " ORDER BY track, verdict"
    ).fetchall()
