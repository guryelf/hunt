"""The gate: ordered deterministic rules; the first rejection decides.

The gate is the only path from "found" to "tracked". It reads constraints
from the spec layer (never prompts, never its own opinions) and returns
one of three verdicts:

- ``accept`` — passes every rule, no judgment calls
- ``flag``   — in scope, but carries reasons for the human (see /review)
- ``reject`` — recorded with a machine reason; never becomes a job
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from .normalize import board_of, dedup_key, parse_location
from .spec import Constraints, Profile

Verdict = Literal["accept", "flag", "reject"]

# Reject reasons — machine vocabulary, persisted in the state.
BOARD_DENIED = "board_denied"
COUNTRY_DENIED = "country_denied"
COUNTRY_OUT_OF_SCOPE = "country_out_of_scope"
COUNTRY_UNVERIFIABLE = "country_unverifiable"
REMOTE_NOT_ALLOWED = "remote_not_allowed"
EMPLOYMENT_MISMATCH = "employment_mismatch"
STALE_POSTING = "stale_posting"
LANGUAGE_UNDECLARED = "language_undeclared"
DUPLICATE = "duplicate"

# Flag reasons — judgment calls surfaced for the human in /review.
FLAG_REMOTE_ANCHORED = "remote_anchored"
FLAG_REMOTE_ANYWHERE = "remote_anywhere"
FLAG_RELOCATION = "relocation"
FLAG_RELOCATION_NO_SUPPORT = "relocation_no_support"
FLAG_DATE_UNKNOWN = "date_unknown"
FLAG_EMPLOYMENT_UNKNOWN = "employment_unknown"
FLAG_SENIORITY_OUT_OF_BAND = "seniority_out_of_band"

REJECT_REASONS = frozenset(
    {
        BOARD_DENIED,
        COUNTRY_DENIED,
        COUNTRY_OUT_OF_SCOPE,
        COUNTRY_UNVERIFIABLE,
        REMOTE_NOT_ALLOWED,
        EMPLOYMENT_MISMATCH,
        STALE_POSTING,
        LANGUAGE_UNDECLARED,
        DUPLICATE,
    }
)


@dataclass
class Candidate:
    """A posting as a discovery engine reports it — before any judgment."""

    title: str
    url: str
    company: str | None = None
    location_raw: str | None = None
    country: str | None = None  # engine-extracted ISO-3166 alpha-2, if known
    city: str | None = None
    remote: bool | None = None
    employment: str | None = None
    seniority: str | None = None
    posted_date: str | None = None  # ISO date (YYYY-MM-DD) when known
    languages_required: list[str] | None = None
    visa_support: bool | None = None
    description: str | None = None


@dataclass
class GateResult:
    verdict: Verdict
    reason: str | None = None
    flags: list[str] = field(default_factory=list)
    country: str | None = None
    city: str | None = None
    remote: bool = False
    dedup_key: str = ""


def _parse_posted(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def evaluate(
    candidate: Candidate,
    constraints: Constraints,
    profile: Profile,
    seen_keys: set[str] | None = None,
    *,
    today: date | None = None,
) -> GateResult:
    """Judge one candidate against the spec. The first rejection wins."""
    board = board_of(candidate.url)
    key = dedup_key(candidate.url)

    parsed = parse_location(candidate.location_raw)
    country = candidate.country or parsed.country
    city = candidate.city or parsed.city
    remote = candidate.remote if candidate.remote is not None else parsed.remote

    result = GateResult(
        verdict="accept",
        country=country,
        city=city,
        remote=remote,
        dedup_key=key,
    )
    flags = result.flags

    def reject(reason: str) -> GateResult:
        result.verdict = "reject"
        result.reason = reason
        return result

    # 1. Unreliable boards are rejected regardless of what they claim.
    if board in constraints.exclude_boards:
        return reject(BOARD_DENIED)

    # 2. Hard personal and track-level country exclusions.
    excluded = set(constraints.exclude_countries) | set(profile.never_countries)
    if country is not None and country.upper() in excluded:
        return reject(COUNTRY_DENIED)

    # 3. Country scope. An unverifiable location is a reject, never a pass.
    anchors = (
        set(constraints.countries)
        | set(constraints.relocation.countries)
        | {profile.base.country}
    )
    if remote:
        if constraints.remote == "no":
            return reject(REMOTE_NOT_ALLOWED)
        if country is None:
            if constraints.remote == "anywhere":
                flags.append(FLAG_REMOTE_ANYWHERE)
            else:
                return reject(COUNTRY_UNVERIFIABLE)
        elif country not in anchors:
            return reject(COUNTRY_OUT_OF_SCOPE)
        else:
            flags.append(FLAG_REMOTE_ANCHORED)
    else:
        if country is None:
            return reject(COUNTRY_UNVERIFIABLE)
        if country in constraints.countries:
            pass  # core scope
        elif country in constraints.relocation.countries:
            if candidate.visa_support:
                flags.append(FLAG_RELOCATION)
            else:
                flags.append(FLAG_RELOCATION_NO_SUPPORT)
        else:
            return reject(COUNTRY_OUT_OF_SCOPE)

    # 4. Employment type.
    if candidate.employment is None:
        flags.append(FLAG_EMPLOYMENT_UNKNOWN)
    elif candidate.employment not in constraints.employment:
        return reject(EMPLOYMENT_MISMATCH)

    # 5. Seniority band — a judgment call, not a scope violation.
    if (
        candidate.seniority is not None
        and constraints.seniority
        and candidate.seniority not in constraints.seniority
    ):
        flags.append(FLAG_SENIORITY_OUT_OF_BAND)

    # 6. Posting age. Unknown dates are flagged, not rejected.
    posted = _parse_posted(candidate.posted_date)
    if posted is None:
        flags.append(FLAG_DATE_UNKNOWN)
    else:
        today = today or datetime.now(UTC).date()
        if (today - posted).days > constraints.max_age_days:
            return reject(STALE_POSTING)
        if posted > today:
            flags.append(FLAG_DATE_UNKNOWN)

    # 7. Language gate: an undeclared required language is a deal-breaker.
    for code in candidate.languages_required or []:
        if code.upper() not in profile.languages:
            return reject(LANGUAGE_UNDECLARED)

    # 8. Duplicate sighting of an already-tracked job.
    if seen_keys and key in seen_keys:
        return reject(DUPLICATE)

    if flags:
        result.verdict = "flag"
    return result
