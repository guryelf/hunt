"""hunt CLI: the deterministic half of the loop.

The orchestrator (an interactive session or ``claude -p``) drives these
commands; the commands never improvise. ``run`` and ``add`` are the only
paths by which new jobs enter the DB, and every job they write has
passed the gate. ``resolve`` settles flagged jobs with the human's
decision; ``shortlist`` opens an application on a tracked job and
``materials`` / ``outcome`` move it through the funnel — forward only.
``digest``, ``review``, ``applications``, ``job`` and ``pool`` only read; ``reset``
archives the DB away when the human asks for a clean slate; ``verify``
and ``layout`` check compiled application materials.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import asdict, fields
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from . import state
from .engines.kariyer import KariyerScraper
from .engines.techcareer import TechCareerScraper
from .gate import DUPLICATE, Candidate, GateResult, evaluate
from .layout import parse_pdf, report
from .spec import Profile, Track, load_profile, load_track
from .verify import VerificationError, verify_pdf

app = typer.Typer(
    help="hunt — AI-orchestrated, gate-enforced job search.",
    no_args_is_help=True,
)

_ENGINES = {"kariyer": KariyerScraper, "techcareer": TechCareerScraper}

DbOption = Annotated[Path, typer.Option(help="Path to the state database.")]
RootOption = Annotated[Path, typer.Option(help="Root holding profile.yaml and tracks/.")]


def _load_spec(root: Path, track_name: str) -> tuple[Profile, Track]:
    """Load profile.yaml plus one track, with friendly errors."""
    profile_path = root / "profile.yaml"
    track_path = root / "tracks" / f"{track_name}.yaml"
    if not profile_path.exists():
        typer.echo(f"error: {profile_path} not found", err=True)
        raise typer.Exit(code=2)
    if not track_path.exists():
        typer.echo(f"error: no track at {track_path}", err=True)
        raise typer.Exit(code=2)
    return load_profile(profile_path), load_track(track_path)


def _candidate(payload: object) -> Candidate:
    """Build a Candidate from one JSON object, rejecting bad shapes."""
    if not isinstance(payload, dict):
        typer.echo("error: a candidate must be a JSON object", err=True)
        raise typer.Exit(code=2)
    for required in ("title", "url"):
        if not str(payload.get(required) or "").strip():
            typer.echo(f"error: candidate is missing '{required}'", err=True)
            raise typer.Exit(code=2)
    allowed = {field.name for field in fields(Candidate)}
    unknown = set(payload) - allowed
    if unknown:
        typer.echo(
            f"error: unknown candidate fields: {', '.join(sorted(unknown))}",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        return Candidate(**payload)
    except TypeError as error:
        typer.echo(f"error: candidate is missing required fields: {error}", err=True)
        raise typer.Exit(code=2) from error


def _record(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    track: str,
    intent: str,
    engine: str,
    candidate: Candidate,
    result: GateResult,
    seen: set[str],
) -> None:
    """Insert one gated result as a job or a reject; every judged key is seen.

    Rejects join the dedup view too: a posting the gate has judged is
    not re-judged on a later run — first provenance wins.
    """
    if result.verdict in ("accept", "flag"):
        state.insert_job(
            conn,
            run_id=run_id,
            track=track,
            intent=intent,
            engine=engine,
            candidate=candidate,
            result=result,
        )
    else:
        state.insert_reject(
            conn,
            run_id=run_id,
            track=track,
            intent=intent,
            engine=engine,
            candidate=candidate,
            result=result,
        )
    seen.add(result.dedup_key)


@app.command()
def check(
    root: RootOption = Path("."),
) -> None:
    """Validate the spec layer: profile.yaml + every tracks/*.yaml.

    Run this after any hand edit or /setup change — the hunt only ever
    runs on a spec that loads.
    """
    ok = True
    profile_path = root / "profile.yaml"
    if not profile_path.exists():
        typer.echo(f"missing: {profile_path}", err=True)
        ok = False
    else:
        try:
            load_profile(profile_path)
            typer.echo(f"ok: {profile_path}")
        except ValidationError as error:
            typer.echo(f"invalid: {profile_path}\n{error}", err=True)
            ok = False

    tracks_dir = root / "tracks"
    track_files = sorted(tracks_dir.glob("*.yaml")) if tracks_dir.is_dir() else []
    if not track_files:
        typer.echo(f"missing: {tracks_dir}", err=True)
        ok = False
    for path in track_files:
        try:
            track = load_track(path)
            for intent in track.intents:
                intent.constraints(track.defaults)  # overrides must merge
            typer.echo(f"ok: {path}")
        except (ValidationError, KeyError) as error:
            typer.echo(f"invalid: {path}\n{error}", err=True)
            ok = False
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def run(
    track_name: Annotated[str, typer.Argument(help="Track name, e.g. 'cyber'.")],
    intent: Annotated[
        str | None, typer.Option("--intent", "-i", help="Run one intent only.")
    ] = None,
    engines: Annotated[
        str, typer.Option(help="Comma-separated engine names.")
    ] = "kariyer,techcareer",
    results_wanted: Annotated[int, typer.Option(help="Max results per keyword.")] = 20,
    remote_only: Annotated[bool, typer.Option(help="Keep remote postings only.")] = (
        False
    ),
    db: DbOption = Path("state.db"),
    root: RootOption = Path("."),
) -> None:
    """Scrape every engine for a track's intents and gate the results."""
    profile, track = _load_spec(root, track_name)
    engine_names = [name.strip() for name in engines.split(",") if name.strip()]
    unknown = [name for name in engine_names if name not in _ENGINES]
    if unknown:
        raise typer.BadParameter(f"unknown engines: {', '.join(unknown)}")
    try:
        intents = [track.intent(intent)] if intent else list(track.intents)
    except KeyError as error:
        typer.echo(f"error: {error.args[0]}", err=True)
        raise typer.Exit(code=2) from error
    if not intents:
        typer.echo(f"error: track '{track_name}' has no intents", err=True)
        raise typer.Exit(code=1)

    conn = state.connect(db)
    try:
        for entry in intents:
            constraints = entry.constraints(track.defaults)
            run_id = state.start_run(
                conn, track=track.track, intent=entry.name, engines=engine_names
            )
            seen = state.seen_keys(conn)
            found = accepted = flagged = rejected = duplicates = 0
            for engine_name in engine_names:
                engine = _ENGINES[engine_name]()
                for keyword in entry.keywords:
                    try:
                        candidates = engine.scrape(
                            keyword, results_wanted, is_remote=remote_only
                        )
                    except Exception as error:
                        # One engine failing must not kill the whole run.
                        typer.echo(
                            f"warning: {engine_name} failed on '{keyword}': {error}",
                            err=True,
                        )
                        continue
                    for warning in engine.warnings:
                        typer.echo(f"warning: {warning}", err=True)
                    for candidate in candidates:
                        found += 1
                        result = evaluate(candidate, constraints, profile, seen)
                        _record(
                            conn,
                            run_id=run_id,
                            track=track.track,
                            intent=entry.name,
                            engine=engine_name,
                            candidate=candidate,
                            result=result,
                            seen=seen,
                        )
                        if result.verdict == "accept":
                            accepted += 1
                        elif result.verdict == "flag":
                            flagged += 1
                        else:
                            rejected += 1
                            if result.reason == DUPLICATE:
                                duplicates += 1
            state.finish_run(
                conn,
                run_id,
                status="ok",
                found=found,
                accepted=accepted,
                flagged=flagged,
                rejected=rejected,
                duplicates=duplicates,
            )
            typer.echo(
                f"{track.track}/{entry.name}: found={found} accept={accepted}"
                f" flag={flagged} reject={rejected} dup={duplicates}"
            )
    finally:
        conn.close()


@app.command()
def add(
    track_name: Annotated[str, typer.Argument(help="Track name, e.g. 'cyber'.")],
    intent: Annotated[
        str, typer.Option("--intent", "-i", help="Intent the results belong to.")
    ],
    engine: Annotated[
        str, typer.Option(help="Engine label recorded as provenance.")
    ] = "ai-search",
    cost: Annotated[
        float | None, typer.Option(help="Run cost in USD, when known.")
    ] = None,
    today: Annotated[
        str | None,
        typer.Option(help="Treat today as this ISO date (replays, tests)."),
    ] = None,
    db: DbOption = Path("state.db"),
    root: RootOption = Path("."),
) -> None:
    """Gate JSON candidates from stdin and record them with provenance.

    The orchestrator's write path for AI-search results: one JSON object
    or a JSON array on stdin, one candidate per posting. Everything
    passes the gate — accepts and flags become jobs, rejects are
    recorded with their machine reasons. Echoes one JSON verdict line
    per candidate, then a run summary.
    """
    gate_today: date | None = None
    if today:
        try:
            gate_today = date.fromisoformat(today)
        except ValueError as error:
            raise typer.BadParameter(
                f"--today must be an ISO date: {error}"
            ) from error
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        typer.echo(f"error: stdin is not valid JSON: {error}", err=True)
        raise typer.Exit(code=2) from error
    items = payload if isinstance(payload, list) else [payload]
    candidates = [_candidate(item) for item in items]
    if not candidates:
        typer.echo("error: no candidates on stdin", err=True)
        raise typer.Exit(code=2)

    profile, track = _load_spec(root, track_name)
    try:
        constraints = track.intent(intent).constraints(track.defaults)
    except KeyError as error:
        typer.echo(f"error: {error.args[0]}", err=True)
        raise typer.Exit(code=2) from error

    conn = state.connect(db)
    try:
        run_id = state.start_run(
            conn, track=track.track, intent=intent, engines=[engine]
        )
        seen = state.seen_keys(conn)
        accepted = flagged = rejected = duplicates = 0
        for candidate in candidates:
            result = evaluate(candidate, constraints, profile, seen, today=gate_today)
            _record(
                conn,
                run_id=run_id,
                track=track.track,
                intent=intent,
                engine=engine,
                candidate=candidate,
                result=result,
                seen=seen,
            )
            if result.verdict == "accept":
                accepted += 1
            elif result.verdict == "flag":
                flagged += 1
            else:
                rejected += 1
                if result.reason == DUPLICATE:
                    duplicates += 1
            typer.echo(
                json.dumps(
                    {
                        "title": candidate.title,
                        "verdict": result.verdict,
                        "reason": result.reason,
                        "flags": result.flags,
                        "country": result.country,
                        "dedup_key": result.dedup_key,
                    },
                    ensure_ascii=False,
                )
            )
        state.finish_run(
            conn,
            run_id,
            status="ok",
            found=len(candidates),
            accepted=accepted,
            flagged=flagged,
            rejected=rejected,
            duplicates=duplicates,
            cost_usd=cost,
        )
        typer.echo(
            json.dumps(
                {
                    "run": run_id,
                    "track": track.track,
                    "intent": intent,
                    "engine": engine,
                    "found": len(candidates),
                    "accepted": accepted,
                    "flagged": flagged,
                    "rejected": rejected,
                    "duplicates": duplicates,
                    "cost_usd": cost,
                }
            )
        )
    finally:
        conn.close()


@app.command()
def status(
    db: DbOption = Path("state.db"),
) -> None:
    """Summarize tracked jobs per track and show the last run."""
    conn = state.connect(db)
    try:
        rows = state.summary(conn)
        if not rows:
            typer.echo("no jobs tracked yet")
        for row in rows:
            typer.echo(f"{row['track']:<10} {row['verdict']:<7} {row['n']}")
        last = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            typer.echo(
                f"last run #{last['id']}: {last['track']}/{last['intent']}"
                f" {last['status']} found={last['found']} accept={last['accepted']}"
                f" flag={last['flagged']} reject={last['rejected']}"
                f" dup={last['duplicates']}"
            )
    finally:
        conn.close()


@app.command()
def rejects(
    track: Annotated[str | None, typer.Option(help="Filter by track.")] = None,
    limit: Annotated[int, typer.Option(help="How many rows to show.")] = 20,
    db: DbOption = Path("state.db"),
) -> None:
    """Show the latest gate rejects with their machine reasons."""
    conn = state.connect(db)
    try:
        rows = state.recent_rejects(conn, track=track, limit=limit)
        if not rows:
            typer.echo("no matching rejects")
        for row in rows:
            typer.echo(
                f"[{row['reason']}] {row['title']} — {row['detail']}\n    {row['url']}"
            )
    finally:
        conn.close()


@app.command()
def gate(
    track_name: Annotated[str, typer.Argument(help="Track name, e.g. 'cyber'.")],
    intent: Annotated[
        str, typer.Option("--intent", "-i", help="Intent whose constraints apply.")
    ],
    db: DbOption = Path("state.db"),
    root: RootOption = Path("."),
) -> None:
    """Read one JSON candidate from stdin; print its gate verdict as JSON.

    A read-only preview of what ``hunt add`` would record — useful for
    dry runs before committing a batch.
    """
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        typer.echo(f"error: stdin is not valid JSON: {error}", err=True)
        raise typer.Exit(code=2) from error
    candidate = _candidate(payload)
    profile, track = _load_spec(root, track_name)
    try:
        constraints = track.intent(intent).constraints(track.defaults)
    except KeyError as error:
        typer.echo(f"error: {error.args[0]}", err=True)
        raise typer.Exit(code=2) from error
    conn = state.connect(db)
    try:
        result = evaluate(
            candidate, constraints, profile, state.seen_keys(conn)
        )
    finally:
        conn.close()
    typer.echo(json.dumps(asdict(result), ensure_ascii=False))


@app.command()
def review(
    track: Annotated[str | None, typer.Option(help="Filter by track.")] = None,
    limit: Annotated[int, typer.Option(help="Max jobs to list.")] = 25,
    db: DbOption = Path("state.db"),
) -> None:
    """List flagged jobs awaiting a human decision, one JSON line each.

    The review queue: gate flags are judgment calls only the human can
    settle. Settle each with ``hunt resolve`` — skipping leaves a job
    here for next time.
    """
    conn = state.connect(db)
    try:
        rows = state.flagged_jobs(conn, track=track, limit=limit)
        if not rows:
            typer.echo("no flags awaiting review")
        for row in rows:
            payload = dict(row)
            payload["flags"] = json.loads(row["flags"])
            if payload["description"]:
                payload["description"] = payload["description"][:400]
            typer.echo(json.dumps(payload, ensure_ascii=False))
    finally:
        conn.close()


@app.command()
def resolve(
    job_id: Annotated[int, typer.Argument(help="Job id, as shown by hunt review.")],
    decision: Annotated[
        str, typer.Option("--decision", "-d", help="keep or reject.")
    ],
    note: Annotated[
        str | None, typer.Option(help="Why — recorded with the decision.")
    ] = None,
    db: DbOption = Path("state.db"),
) -> None:
    """Settle one tracked job: keep it, or reject it by hand.

    The only write path for human decisions. A reject preserves the
    original run's provenance and the job's fields; the gate's own
    verdicts are never rewritten.
    """
    if decision not in ("keep", "reject"):
        raise typer.BadParameter("decision must be 'keep' or 'reject'")
    conn = state.connect(db)
    try:
        try:
            settled = state.resolve_job(conn, job_id, decision=decision, note=note)
        except ValueError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(code=2) from error
        if not settled:
            typer.echo(
                f"error: no outstanding job #{job_id} — it is missing"
                " or already reviewed",
                err=True,
            )
            raise typer.Exit(code=2)
        typer.echo(
            json.dumps(
                {"id": job_id, "decision": decision, "note": note},
                ensure_ascii=False,
            )
        )
    finally:
        conn.close()


@app.command()
def job(
    job_id: Annotated[int, typer.Argument(help="Tracked job id, as hunt shows it.")],
    db: DbOption = Path("state.db"),
) -> None:
    """Show one tracked job as JSON, with its application if it has one.

    The reader for /apply: the posting the gate actually saw (title,
    company, url, description) plus where the funnel stands on it.
    """
    conn = state.connect(db)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            typer.echo(f"error: no tracked job #{job_id}", err=True)
            raise typer.Exit(code=2)
        payload = dict(row)
        payload["flags"] = json.loads(row["flags"])
        application = conn.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if application is not None:
            payload["application"] = dict(application)
        typer.echo(json.dumps(payload, ensure_ascii=False))
    finally:
        conn.close()


@app.command()
def shortlist(
    job_id: Annotated[int, typer.Argument(help="Tracked job id, as hunt shows it.")],
    note: Annotated[
        str | None, typer.Option(help="Why this one — recorded with the application.")
    ] = None,
    db: DbOption = Path("state.db"),
) -> None:
    """Open an application for a tracked job (status: shortlisted).

    The funnel's first step: this job is one you intend to apply to.
    ``hunt materials`` and ``hunt outcome`` are the only ways forward
    from here.
    """
    conn = state.connect(db)
    try:
        try:
            application_id = state.shortlist_job(conn, job_id, note=note)
        except ValueError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(code=2) from error
        if application_id is None:
            typer.echo(f"error: no tracked job #{job_id}", err=True)
            raise typer.Exit(code=2)
        typer.echo(
            json.dumps(
                {
                    "application": application_id,
                    "job": job_id,
                    "status": state.SHORTLISTED,
                    "note": note,
                },
                ensure_ascii=False,
            )
        )
    finally:
        conn.close()


@app.command()
def materials(
    application_id: Annotated[
        int, typer.Argument(help="Application id, as shown by hunt applications.")
    ],
    cv: Annotated[Path | None, typer.Option(help="Path to the tailored CV PDF.")] = None,
    cover: Annotated[Path | None, typer.Option(help="Path to the cover letter PDF.")] = None,
    posting: Annotated[
        Path | None, typer.Option(help="Path to the archived posting.")
    ] = None,
    db: DbOption = Path("state.db"),
) -> None:
    """Attach drafted files to an application; shortlisted becomes drafted.

    Every path must exist — a recorded material you cannot open is a
    lie to your future self. Re-recording replaces paths without ever
    moving the status backwards.
    """
    if cv is None and cover is None and posting is None:
        raise typer.BadParameter("nothing to record: pass --cv, --cover, or --posting")
    missing = [str(p) for p in (cv, cover, posting) if p is not None and not p.exists()]
    if missing:
        typer.echo(f"error: no such file(s): {', '.join(missing)}", err=True)
        raise typer.Exit(code=2)
    conn = state.connect(db)
    try:
        row = state.record_materials(
            conn,
            application_id,
            cv=str(cv) if cv else None,
            cover=str(cover) if cover else None,
            posting=str(posting) if posting else None,
        )
        if row is None:
            typer.echo(f"error: no application #{application_id}", err=True)
            raise typer.Exit(code=2)
        typer.echo(
            json.dumps(
                {
                    "application": application_id,
                    "status": row["status"],
                    "cv": row["cv_file"],
                    "cover": row["cover_file"],
                    "posting": row["posting_file"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        conn.close()


@app.command()
def outcome(
    application_id: Annotated[
        int, typer.Argument(help="Application id, as shown by hunt applications.")
    ],
    status: Annotated[
        str | None,
        typer.Option(
            help="New status: applied, interview, offer, hired, rejected,"
            " no_response, offer_declined, withdrawn."
        ),
    ] = None,
    note: Annotated[
        str | None, typer.Option(help="Feedback, a date, or why — recorded on the timeline.")
    ] = None,
    db: DbOption = Path("state.db"),
) -> None:
    """Record what happened: a funnel move, a note, or both.

    The funnel only moves forward — shortlisted → drafted → applied →
    interview → offer → hired, with rejected / no_response /
    offer_declined / withdrawn as the settled ends (withdrawn is legal
    from any open status). A note with no status records feedback
    without moving anything.
    """
    if status is None and note is None:
        raise typer.BadParameter("nothing to record: pass --status or --note")
    if status is not None and status not in state.ALL_STATUSES:
        raise typer.BadParameter(
            f"unknown status '{status}'; known: {', '.join(sorted(state.ALL_STATUSES))}"
        )
    conn = state.connect(db)
    try:
        try:
            row = state.record_outcome(conn, application_id, status=status, note=note)
        except ValueError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(code=2) from error
        if row is None:
            typer.echo(f"error: no application #{application_id}", err=True)
            raise typer.Exit(code=2)
        typer.echo(
            json.dumps(
                {
                    "application": application_id,
                    "status": row["status"],
                    "note": row["note"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        conn.close()


@app.command()
def applications(
    status: Annotated[str | None, typer.Option(help="Filter by funnel status.")] = None,
    track: Annotated[str | None, typer.Option(help="Filter by track.")] = None,
    db: DbOption = Path("state.db"),
) -> None:
    """List applications with their jobs' context, one JSON line each.

    Newest first: the top of the list is what needs action now, the
    bottom is settled history.
    """
    if status is not None and status not in state.ALL_STATUSES:
        raise typer.BadParameter(
            f"unknown status '{status}'; known: {', '.join(sorted(state.ALL_STATUSES))}"
        )
    conn = state.connect(db)
    try:
        rows = state.list_applications(conn, status=status, track=track)
        if not rows:
            typer.echo("no matching applications")
        for row in rows:
            typer.echo(json.dumps(dict(row), ensure_ascii=False))
    finally:
        conn.close()


@app.command()
def pool(
    track: Annotated[str | None, typer.Option(help="Filter by track.")] = None,
    limit: Annotated[int, typer.Option(help="Max jobs to list.")] = 25,
    db: DbOption = Path("state.db"),
) -> None:
    """List tracked jobs with no application yet, one JSON line each.

    The shortlist pool: what the gate accepted or the human kept, minus
    everything an application has already opened on. Newest first —
    the top of the list is what the funnel should act on next.
    """
    conn = state.connect(db)
    try:
        rows = state.pool_jobs(conn, track=track, limit=limit)
        if not rows:
            typer.echo("no tracked jobs awaiting shortlist")
        for row in rows:
            payload = dict(row)
            payload["flags"] = json.loads(row["flags"])
            typer.echo(json.dumps(payload, ensure_ascii=False))
    finally:
        conn.close()


@app.command()
def verify(
    pdf: Annotated[Path, typer.Argument(help="Compiled PDF to check.")],
    pages: Annotated[int | None, typer.Option(help="Required exact page count.")] = None,
    min_chars: Annotated[
        int, typer.Option(help="Minimum text-layer characters (default 1).")
    ] = 1,
    contains: Annotated[
        list[str] | None,
        typer.Option(
            "--contains",
            help="Text that must appear in the text layer (repeatable);"
            " both sides fold for whitespace, NFC, and LaTeX's typographic"
            " substitutions.",
        ),
    ] = None,
    dump_text: Annotated[
        Path | None, typer.Option(help="Write the extracted text layer to this path.")
    ] = None,
    ascii_dates: Annotated[
        bool,
        typer.Option(
            help="Fail on date ranges joined by a Unicode dash (what LaTeX"
            " makes from --); ATS date parsers drop them."
        ),
    ] = False,
) -> None:
    """Verify a compiled PDF: page count and an ATS-readable text layer.

    The deterministic half of /apply's compile-and-inspect loop — this
    checks what a parser can actually read, not what the rendered page
    looks like (that is ``hunt layout``'s job).
    """
    try:
        extractor, _text, actual = verify_pdf(
            pdf,
            expected_pages=pages,
            min_chars=min_chars,
            required_text=tuple(contains or ()),
            dump_text=dump_text,
            ascii_dates=ascii_dates,
        )
    except VerificationError as error:
        typer.echo(f"error: {pdf}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"verified {pdf} (extractor: {extractor}, pages: {actual})")


@app.command()
def layout(
    pdf: Annotated[Path, typer.Argument(help="Compiled PDF to measure.")],
) -> None:
    """Measure a compiled PDF's layout: holes, orphans, thin pages.

    What eyeballing misses: an unbreakable \\cventry ejected to the next
    page leaves a hole behind — with a clean compile and a correct page
    count on top. Needs Poppler's pdftotext; without it the check says
    skipped rather than inventing a verdict.
    """
    if not pdf.exists():
        typer.echo(f"error: {pdf} not found", err=True)
        raise typer.Exit(code=2)
    try:
        pages = parse_pdf(pdf)
    except RuntimeError as error:
        typer.echo(f"skipped: {error}", err=True)
        raise typer.Exit(code=2) from error
    problems = report(pdf, pages)
    if problems:
        raise typer.Exit(code=1)


@app.command()
def digest(
    track: Annotated[str | None, typer.Option(help="Filter by track.")] = None,
    since: Annotated[
        str | None,
        typer.Option(
            help="ISO timestamp window instead of the watermark (does not advance it)."
        ),
    ] = None,
    db: DbOption = Path("state.db"),
) -> None:
    """Summarize what changed since the last digest, as one JSON object.

    The watermark (meta ``last_digest_at``) makes each digest cover
    exactly what the previous one didn't; the first digest is all-time.
    An explicit --since reads that window but leaves the watermark
    alone.
    """
    conn = state.connect(db)
    try:
        window = since or state.get_meta(conn, state.LAST_DIGEST_AT)
        data = state.digest(conn, track=track, since=window)
        typer.echo(json.dumps(data, ensure_ascii=False))
        if since is None:
            state.set_meta(conn, state.LAST_DIGEST_AT, state.now())
    finally:
        conn.close()


@app.command()
def reset(
    confirm: Annotated[
        bool, typer.Option(help="Actually do it — required, no exceptions.")
    ] = False,
    db: DbOption = Path("state.db"),
) -> None:
    """Archive the state DB into backups/ and start fresh.

    The archive keeps every job, reject, and run — restoring is copying
    the file back, so reset is reversible, unlike a plain delete. The
    spec layer is untouched: it lives in git, not in the DB.
    """
    if not db.exists():
        typer.echo(f"no state database at {db}")
        return
    if not confirm:
        typer.echo(
            f"refusing to reset {db} without --confirm — it would be"
            " archived to backups/ and then deleted: every tracked job,"
            " reject, and run.",
            err=True,
        )
        raise typer.Exit(code=1)
    backups = db.parent / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = backups / f"{db.stem}-{stamp}{db.suffix}"
    db.replace(archive)
    typer.echo(f"archived: {archive}")
    typer.echo(f"deleted: {db} — run a hunt cycle to start fresh")


if __name__ == "__main__":
    app()
