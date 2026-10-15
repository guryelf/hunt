# materials — the application-documents machinery

Git-tracked machinery. The personal data it works on lives in the
gitignored `documents/` tree, and the workflow that drives it all is
`/apply` (`.claude/commands/apply.md`).

```
materials/
  cover.cls            cover letter class (XeLaTeX, article-based)
  cover-template.tex   the structural skeleton every cover letter starts from
  OpenFonts/           Lato + Raleway, loaded by path from cover.cls
  style-guide.md       writing rules for CVs and letters

documents/                      gitignored — personal
  cv/master.tex                 the master CV: sole fact source for every draft
  applications/<co>_<role>/     one directory per application:
                                cv.tex, cover.tex, job_posting.md,
                                the compiled PDFs
```

## Compile contracts

The CV is self-contained moderncv (no external font files) — compile it
in the application directory:

    cd documents/applications/<dir>
    lualatex -interaction=nonstopmode cv.tex

The cover letter loads OpenFonts and `cover.cls` by path, and fontspec
paths resolve against the working directory — so cover letters compile
**from `materials/`**, with the output directed into the application
directory:

    cd materials
    xelatex -interaction=nonstopmode \
      -output-directory=../documents/applications/<dir> \
      ../documents/applications/<dir>/cover.tex

Never compile a cover letter from anywhere else; the font paths only
resolve from `materials/`.

## The verification pair

    uv run hunt verify <pdf> --pages N [--contains "..."]... [--ascii-dates] [--dump-text out.txt]
    uv run hunt layout <pdf>

`verify` checks what an ATS parser sees: exact page count, a real text
layer, required strings (folded for LaTeX's typographic substitutions),
and ASCII date ranges. `layout` checks what the eye misses: holes an
ejected `\cventry` leaves behind, orphaned headers, thin final pages,
footer collisions. Both must pass, and both are in addition to — never
instead of — reading the rendered PDF.

## LaTeX rules that bite (ported from the assistant template)

**Escaping.** `&` fails loudly; `%` fails *silently* as a comment —
always `\%`. Also `\$ \# \_ \textasciitilde{} \textasciicircum{}`.
Brace risky item text: `\item {[text]}`.

**Date fields must be ASCII ranges.** Write `2024-2025` with a single
hyphen. LaTeX ligates `--` to an en-dash (U+2013), and ATS parsers that
split ranges on the ASCII hyphen drop the whole date (a real Workday
import did). `hunt verify --ascii-dates` catches this.

**moderncv specifics.**

- The banking/blue template needs its color-override preamble block
  (`\renewcommand*{\namefont}` + the three `\colorlet` lines); the
  defaults render wrong without it. It is in `master.tex` — keep it.
- `\address{}{}{}` must be *deleted* if empty, not left blank.
- No `\vspace` between itemize items — moderncv's list spacing is
  already tight; adding more makes it collide.
- `\needspace{5\baselineskip}` before an entry that keeps orphaning
  (never before `\section`).
- `\enlargethispage{2-3\baselineskip}` rescues a *near-miss* overflow;
  it is not a substitute for cutting content.
- Section headings must match the language of the posting.
- lualatex for the CV (fontawesome5 errors under pdflatex on some
  installs); xelatex for cover.cls (fontspec).

**cover.cls specifics.**

- An itemize goes *outside* `\lettercontent{}` (its trailing `\\`
  errors on `\end{itemize}`), wrapped in Raleway-Medium so the bullet
  font matches the body — see `cover-template.tex` for the pattern.
- No trailing `\\` inside `\closing{}` — cover.cls appends its own, and
  a doubled break triggers "There's no line here to end."
- Exactly 1 page, 250–300 words.

**Cutting for length is relevance-weighted.** Cut the
lowest-relevance entry first regardless of which section it is in;
uniqueness and narrative load count; padding never survives. A final
page left thin by over-cutting is its own defect (`hunt layout` reports
it) — restore the highest-relevance content previously cut.

## Deep references

The template repos stay checked out next door as read-only reference.
For the full machinery lore (with the failures that produced each rule):

- `../ai-job-search/.claude/skills/job-application-assistant/05-cv-templates.md`
- `../ai-job-search/.claude/skills/job-application-assistant/06-cover-letter-templates.md`