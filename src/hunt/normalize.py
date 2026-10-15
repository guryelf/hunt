"""Normalization: fold Turkish text, parse locations, canonicalize URLs.

Everything here is deterministic lookup, no judgment. If a location
cannot be resolved, it resolves to nothing — the gate treats that as
unverifiable, never as a pass.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit


def fold(text: str) -> str:
    """Fold text for matching: strip accents (NFKD), then lowercase.

    ``İ`` becomes ``i`` plus a combining dot; dropping combining marks
    matches how the boards themselves ASCII-fold Turkish keywords.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold()


_TOKEN = re.compile(r"[a-z0-9]+")

_COUNTRY_ALIASES: dict[str, str] = {
    "turkiye": "TR",
    "turkey": "TR",
    "tr": "TR",
    "deutschland": "DE",
    "germany": "DE",
    "almanya": "DE",
    "ukraine": "UA",
    "ukrayna": "UA",
    "united states": "US",
    "abd": "US",
    "usa": "US",
    "amerika": "US",
    "netherlands": "NL",
    "hollanda": "NL",
    "nederland": "NL",
    "poland": "PL",
    "polonya": "PL",
    "polska": "PL",
    "czech republic": "CZ",
    "czechia": "CZ",
    "estonia": "EE",
    "estonya": "EE",
    "united kingdom": "GB",
    "ingiltere": "GB",
    "france": "FR",
    "fransa": "FR",
    "ireland": "IE",
    "irlanda": "IE",
}

_CITY_TO_COUNTRY: dict[str, str] = {
    # Türkiye
    "istanbul": "TR",
    "ankara": "TR",
    "izmir": "TR",
    "bursa": "TR",
    "antalya": "TR",
    "konya": "TR",
    "eskisehir": "TR",
    "kocaeli": "TR",
    "gebze": "TR",
    "adana": "TR",
    "kayseri": "TR",
    "gaziantep": "TR",
    "samsun": "TR",
    "trabzon": "TR",
    "denizli": "TR",
    # Germany
    "berlin": "DE",
    "munich": "DE",
    "munchen": "DE",
    "hamburg": "DE",
    "frankfurt": "DE",
    "stuttgart": "DE",
    "koln": "DE",
    "cologne": "DE",
    "munster": "DE",
    "dresden": "DE",
    "nurnberg": "DE",
    "nuremberg": "DE",
    # Netherlands
    "amsterdam": "NL",
    "rotterdam": "NL",
    "eindhoven": "NL",
    "utrecht": "NL",
    "groningen": "NL",
    "den haag": "NL",
    # Poland / Czechia / Estonia
    "warszawa": "PL",
    "warsaw": "PL",
    "krakow": "PL",
    "gdansk": "PL",
    "wroclaw": "PL",
    "poznan": "PL",
    "praha": "CZ",
    "prague": "CZ",
    "brno": "CZ",
    "tallinn": "EE",
    "tartu": "EE",
    # Ukraine
    "kyiv": "UA",
    "kiev": "UA",
    "kharkiv": "UA",
    "kharkov": "UA",
    "odesa": "UA",
    "odessa": "UA",
    "lviv": "UA",
    "dnipro": "UA",
    "zaporizhzhia": "UA",
    # Elsewhere that shows up in postings
    "new york": "US",
    "austin": "US",
    "seattle": "US",
    "san francisco": "US",
    "boston": "US",
    "chicago": "US",
    "london": "GB",
    "paris": "FR",
    "dublin": "IE",
}

_REMOTE_TOKENS = {"remote", "uzaktan"}


@dataclass(frozen=True)
class Location:
    country: str | None
    city: str | None
    remote: bool


def parse_location(text: str | None) -> Location:
    """Extract (country, city, remote) from free-text location, or Nones."""
    if not text or not text.strip():
        return Location(None, None, False)
    tokens = _TOKEN.findall(fold(text))
    if not tokens:
        return Location(None, None, False)
    grams = set(tokens)
    grams.update(f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1))
    grams.update(
        f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}" for i in range(len(tokens) - 2)
    )
    remote = any(token in _REMOTE_TOKENS for token in tokens)

    country: str | None = None
    city: str | None = None
    for gram in grams:
        if country is None and gram in _COUNTRY_ALIASES:
            country = _COUNTRY_ALIASES[gram]
        if gram in _CITY_TO_COUNTRY:
            city = city or gram
            if country is None:
                country = _CITY_TO_COUNTRY[gram]
    return Location(country, city, remote)


def normalize_url(url: str) -> str:
    """Canonical form: lowercase host, query and fragment dropped."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    return f"{parts.scheme or 'https'}://{host}{parts.path or '/'}"


def board_of(url: str) -> str:
    """The site identity of a URL: its host without a www. prefix."""
    host = urlsplit(url.strip()).netloc.lower()
    return host.removeprefix("www.")


def dedup_key(url: str) -> str:
    """Stable identity of a posting: sha1 of its canonical URL."""
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()
