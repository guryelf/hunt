"""The spec layer: profile.yaml and tracks/*.yaml are the contract.

The orchestrator plans from these models; the gate enforces them. No
constraint may live anywhere else — not in a prompt, not in code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

RemotePolicy = Literal["no", "anchored", "anywhere"]


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class Relocation(BaseModel):
    """On-site abroad is acceptable only in these countries, only with these."""

    model_config = ConfigDict(extra="forbid")

    countries: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)


class Constraints(BaseModel):
    """The gate's rule set — pure data from the spec layer."""

    model_config = ConfigDict(extra="forbid")

    countries: list[str] = Field(default_factory=list)
    exclude_countries: list[str] = Field(default_factory=list)
    exclude_boards: list[str] = Field(default_factory=list)
    remote: RemotePolicy = "no"
    relocation: Relocation = Field(default_factory=Relocation)
    employment: list[str] = Field(default_factory=lambda: ["full_time", "contract"])
    seniority: list[str] = Field(default_factory=list)
    max_age_days: int = 14


class Intent(BaseModel):
    """One search direction inside a track."""

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    keywords: list[str] = Field(default_factory=list)
    ai_search_hints: list[str] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)

    def constraints(self, defaults: Constraints) -> Constraints:
        """Merge this intent's overrides over the track defaults."""
        if not self.overrides:
            return defaults
        merged = {**defaults.model_dump()}
        merged.update({key: value for key, value in self.overrides.items() if value is not None})
        return Constraints.model_validate(merged)


class Track(BaseModel):
    """A role direction: its own intents, constraints, and results."""

    model_config = ConfigDict(extra="forbid")

    track: str
    display: str = ""
    defaults: Constraints = Field(default_factory=Constraints)
    intents: list[Intent] = Field(default_factory=list)

    def intent(self, name: str) -> Intent:
        for entry in self.intents:
            if entry.name == name:
                return entry
        known = ", ".join(entry.name for entry in self.intents) or "none"
        raise KeyError(
            f"unknown intent '{name}' in track '{self.track}' (known: {known})"
        )


class Home(BaseModel):
    """Where the hunter is based — the default anchor country."""

    model_config = ConfigDict(extra="forbid")

    city: str
    country: str


class Profile(BaseModel):
    """Person-level facts every track inherits."""

    model_config = ConfigDict(extra="forbid")

    name: str
    base: Home
    languages: dict[str, str] = Field(default_factory=dict)
    never_countries: list[str] = Field(default_factory=list)


def load_profile(path: Path) -> Profile:
    """Load profile.yaml."""
    return Profile.model_validate(_load_yaml(path))


def load_track(path: Path) -> Track:
    """Load one tracks/<name>.yaml."""
    return Track.model_validate(_load_yaml(path))
