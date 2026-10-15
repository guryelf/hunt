"""The spec layer must load: profile.yaml and every committed track."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hunt.spec import Constraints, Intent, load_profile, load_track

ROOT = Path(__file__).resolve().parents[1]


def test_committed_profile_and_tracks_load() -> None:
    profile = load_profile(ROOT / "profile.yaml")
    assert profile.base.country == "TR"
    assert set(profile.languages) == {"TR", "EN"}
    assert profile.never_countries == []

    track_files = sorted((ROOT / "tracks").glob("*.yaml"))
    assert track_files, "tracks/ must hold at least one track"
    names = []
    for path in track_files:
        track = load_track(path)
        names.append(track.track)
        assert track.intents, f"{path.name} has no intents"
        for intent in track.intents:
            # Every intent's overrides must merge cleanly at load time.
            intent.constraints(track.defaults)
    assert {"cyber", "web-dev"} <= set(names)


def test_cyber_defaults_match_the_incident_lessons() -> None:
    track = load_track(ROOT / "tracks" / "cyber.yaml")
    defaults = track.defaults

    assert defaults.countries == ["TR"]
    assert defaults.exclude_countries == []
    assert defaults.exclude_boards == ["djinni.co"]
    assert defaults.remote == "anchored"
    assert defaults.employment == ["full_time", "contract"]
    assert defaults.seniority == ["junior", "mid"]
    assert defaults.max_age_days == 14
    assert track.intent("offensive-security") is not None


def test_intent_overrides_merge_over_defaults() -> None:
    defaults = Constraints(countries=["TR"], remote="no")
    intent = Intent(
        name="remote-heavy", role="anything", overrides={"remote": "anywhere"}
    )

    merged = intent.constraints(defaults)

    assert merged.countries == ["TR"]  # untouched field survives
    assert merged.remote == "anywhere"  # override wins


def test_unknown_override_key_is_rejected() -> None:
    defaults = Constraints()
    intent = Intent(name="typo", role="anything", overrides={"countrys": ["TR"]})

    with pytest.raises(ValidationError):
        intent.constraints(defaults)


def test_unknown_intent_name_names_the_known_ones() -> None:
    track = load_track(ROOT / "tracks" / "cyber.yaml")

    with pytest.raises(KeyError) as error:
        track.intent("no-such-intent")
    assert "soc-analyst" in str(error.value)
