"""Deterministic material checks: text-layer folds, date hygiene, layout geometry.

Pure-function tests only — they need neither Poppler nor a compiled
PDF. The real pipeline gets exercised by the /apply smoke (a live
lualatex/xelatex compile through hunt verify + hunt layout).
"""

import pytest

from hunt.layout import Line, Page, find_orphans
from hunt.verify import (
    VerificationError,
    find_non_ascii_date_ranges,
    normalize_text,
    parse_page_count,
    verify_pdf,
)


def test_normalize_text_folds_latex_typography() -> None:
    # The ligatures LaTeX makes from plain source, folded back for --contains.
    assert (
        normalize_text("Master’s “ship” 2016–2024 remote role")
        == "Master's \"ship\" 2016-2024 remote role"
    )
    # NFC: a pdflatex-style decomposed accent folds to the precomposed one.
    assert normalize_text("résumé") == normalize_text("résumé")
    assert normalize_text("a\n\n b \t c") == "a b c"


def test_ascii_date_ranges_are_caught_and_non_dates_are_not() -> None:
    # Years joined to a Unicode dash — what LaTeX makes from `--`.
    hits = find_non_ascii_date_ranges("Worked 2024–2025 at Teknoloji A.S.\n")
    assert len(hits) == 1
    assert hits[0][1] == "–"
    assert "2024" in hits[0][0]

    # A dash before the year counts too ("2016 – Present" in either order).
    assert find_non_ascii_date_ranges("Since – 2024:\n") != []

    clean = (
        "Good: 2024-2025 (ASCII)\n"
        "Budget: EUR 600k–1M (no year)\n"
        "Throughput: 120000–130000 (digits inside a number)\n"
        "Wrapped: 2016\n– a bullet on the next line (vertical gap, not a join)\n"
    )
    assert find_non_ascii_date_ranges(clean) == []


def test_parse_page_count_reads_pdfinfo() -> None:
    assert parse_page_count("Title: x\nPages:  2\nPage size: A4\n") == 2
    with pytest.raises(VerificationError):
        parse_page_count("no page count here")


def test_verify_pdf_refuses_missing_files(tmp_path) -> None:
    with pytest.raises(VerificationError):
        verify_pdf(tmp_path / "never-compiled.pdf", expected_pages=1)


def _line(top: float, text: str, height: float = 10.0, left: float = 50.0) -> Line:
    return Line(top=top, bottom=top + height, left=left, height=height, text=text)


def test_find_orphans_reports_a_stranded_heading() -> None:
    body = [_line(top, f"experience {i}") for i, top in enumerate(range(100, 700, 20))]
    heading = Line(top=700, bottom=714, left=50.0, height=14.0, text="EXPERIENCE")
    page1 = Page(800, [*body, heading])
    page2 = Page(800, [_line(top, "entry") for top in range(100, 700, 20)])

    problems = find_orphans([page1, page2])

    assert problems
    assert "EXPERIENCE" in problems[0]
    assert "p1" in problems[0]


def test_find_orphans_reports_a_split_entry_header() -> None:
    # The page ends on an entry header; the next page resumes indented.
    body = [_line(top, f"role {i}") for i, top in enumerate(range(100, 680, 20))]
    page1 = Page(800, body)
    page2 = Page(800, [_line(top, "bullet", left=70.0) for top in range(100, 700, 20)])

    problems = find_orphans([page1, page2])

    assert problems
    assert "orphaned from its bullets" in problems[0]


def test_clean_pages_report_no_orphans() -> None:
    page1 = Page(800, [_line(top, f"t{i}") for i, top in enumerate(range(100, 680, 20))])
    page2 = Page(800, [_line(top, f"t{i}") for i, top in enumerate(range(100, 680, 20))])

    assert find_orphans([page1, page2]) == []
