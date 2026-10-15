"""The gate: every rule, in order, against fixed inputs.

The constraints mirror tracks/cyber.yaml so the matrix doubles as a
check that the track's lessons (djinni excluded, empty location never
passes) are enforced by code, not just written down.
"""

from datetime import date

from hunt.gate import (
    BOARD_DENIED,
    COUNTRY_DENIED,
    COUNTRY_OUT_OF_SCOPE,
    COUNTRY_UNVERIFIABLE,
    DUPLICATE,
    EMPLOYMENT_MISMATCH,
    FLAG_DATE_UNKNOWN,
    FLAG_EMPLOYMENT_UNKNOWN,
    FLAG_RELOCATION,
    FLAG_RELOCATION_NO_SUPPORT,
    FLAG_REMOTE_ANCHORED,
    FLAG_REMOTE_ANYWHERE,
    FLAG_SENIORITY_OUT_OF_BAND,
    LANGUAGE_UNDECLARED,
    REMOTE_NOT_ALLOWED,
    STALE_POSTING,
    Candidate,
    evaluate,
)
from hunt.spec import Constraints, Home, Profile, Relocation

PROFILE = Profile(
    name="Test Hunter",
    base=Home(city="Istanbul", country="TR"),
    languages={"TR": "native", "EN": "advanced", "DE": "intermediate"},
    never_countries=["US"],
)

CONSTRAINTS = Constraints(
    countries=["TR"],
    exclude_countries=["US", "UA", "RU"],
    exclude_boards=["djinni.co"],
    remote="anchored",
    relocation=Relocation(countries=["DE"], requires=["visa_support"]),
    employment=["full_time", "contract"],
    seniority=["junior", "mid"],
    max_age_days=14,
)

TODAY = date(2026, 10, 16)


def _candidate(**overrides) -> Candidate:
    fields = {
        "title": "SOC Analisti",
        "url": "https://www.kariyer.net/is-ilani/soc-analisti-123",
        "company": "Teknoloji A.Ş.",
        "location_raw": "Ankara / Türkiye",
        "employment": "full_time",
        "seniority": "mid",
        "posted_date": "2026-10-10",
    }
    fields.update(overrides)
    return Candidate(**fields)


def _gate(
    candidate: Candidate,
    constraints: Constraints = CONSTRAINTS,
    seen: set[str] | None = None,
):
    return evaluate(candidate, constraints, PROFILE, seen, today=TODAY)


def test_clean_candidate_accepts() -> None:
    result = _gate(_candidate())
    assert result.verdict == "accept"
    assert result.reason is None
    assert result.flags == []
    assert result.country == "TR"
    assert result.city == "ankara"
    assert result.dedup_key


def test_excluded_board_denies_before_anything_else() -> None:
    result = _gate(_candidate(url="https://djinni.co/jobs/847334-offsec/"))
    assert (result.verdict, result.reason) == ("reject", BOARD_DENIED)


def test_never_country_rejects() -> None:
    result = _gate(_candidate(location_raw="New York, US"))
    assert (result.verdict, result.reason) == ("reject", COUNTRY_DENIED)


def test_ukraine_rejects_even_before_scope_rules() -> None:
    result = _gate(_candidate(location_raw="Kyiv, Ukraine"))
    assert (result.verdict, result.reason) == ("reject", COUNTRY_DENIED)


def test_empty_location_never_passes() -> None:
    result = _gate(_candidate(location_raw=None))
    assert (result.verdict, result.reason) == ("reject", COUNTRY_UNVERIFIABLE)


def test_remote_anchored_to_allowed_country_flags() -> None:
    result = _gate(_candidate(location_raw="Remote — İstanbul", remote=True))
    assert result.verdict == "flag"
    assert result.flags == [FLAG_REMOTE_ANCHORED]


def test_remote_without_country_rejects_under_anchored_policy() -> None:
    result = _gate(_candidate(location_raw="Remote", remote=True))
    assert (result.verdict, result.reason) == ("reject", COUNTRY_UNVERIFIABLE)


def test_remote_rejected_when_policy_is_no() -> None:
    constraints = CONSTRAINTS.model_copy(update={"remote": "no"})
    result = _gate(_candidate(remote=True), constraints)
    assert (result.verdict, result.reason) == ("reject", REMOTE_NOT_ALLOWED)


def test_remote_anywhere_policy_flags_unknown_country() -> None:
    constraints = CONSTRAINTS.model_copy(update={"remote": "anywhere"})
    result = _gate(_candidate(location_raw="Remote", remote=True), constraints)
    assert result.verdict == "flag"
    assert FLAG_REMOTE_ANYWHERE in result.flags


def test_remote_anywhere_policy_still_rejects_out_of_scope() -> None:
    constraints = CONSTRAINTS.model_copy(update={"remote": "anywhere"})
    result = _gate(_candidate(location_raw="Remote — New York", remote=True), constraints)
    assert (result.verdict, result.reason) == ("reject", COUNTRY_DENIED)


def test_relocation_flags_depend_on_visa_support() -> None:
    no_visa = _gate(_candidate(location_raw="Berlin, Deutschland"))
    assert no_visa.verdict == "flag"
    assert no_visa.flags == [FLAG_RELOCATION_NO_SUPPORT]

    with_visa = _gate(
        _candidate(location_raw="Berlin, Deutschland", visa_support=True)
    )
    assert with_visa.verdict == "flag"
    assert with_visa.flags == [FLAG_RELOCATION]


def test_out_of_scope_country_rejects() -> None:
    result = _gate(_candidate(location_raw="Paris, France"))
    assert (result.verdict, result.reason) == ("reject", COUNTRY_OUT_OF_SCOPE)


def test_employment_mismatch_rejects() -> None:
    result = _gate(_candidate(employment="internship"))
    assert (result.verdict, result.reason) == ("reject", EMPLOYMENT_MISMATCH)


def test_unknown_employment_flags() -> None:
    result = _gate(_candidate(employment=None))
    assert result.verdict == "flag"
    assert FLAG_EMPLOYMENT_UNKNOWN in result.flags


def test_seniority_out_of_band_flags() -> None:
    result = _gate(_candidate(seniority="senior"))
    assert result.verdict == "flag"
    assert result.flags == [FLAG_SENIORITY_OUT_OF_BAND]


def test_stale_posting_rejects() -> None:
    result = _gate(_candidate(posted_date="2026-09-01"))
    assert (result.verdict, result.reason) == ("reject", STALE_POSTING)


def test_unknown_date_flags() -> None:
    result = _gate(_candidate(posted_date=None))
    assert result.verdict == "flag"
    assert FLAG_DATE_UNKNOWN in result.flags


def test_undeclared_language_rejects() -> None:
    result = _gate(_candidate(languages_required=["FR"]))
    assert (result.verdict, result.reason) == ("reject", LANGUAGE_UNDECLARED)


def test_declared_language_passes() -> None:
    result = _gate(_candidate(languages_required=["EN"]))
    assert result.verdict == "accept"


def test_duplicate_rejects() -> None:
    candidate = _candidate()
    seen = {evaluate(candidate, CONSTRAINTS, PROFILE, today=TODAY).dedup_key}

    result = _gate(candidate, seen=seen)

    assert (result.verdict, result.reason) == ("reject", DUPLICATE)
