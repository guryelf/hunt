# /setup — spec-layer onboarding

You are running hunt's onboarding. Your goal: make `profile.yaml` and
`tracks/*.yaml` faithfully encode the user's actual constraints, keep
the master CV current, then prove it with `hunt check`. Everything
else in hunt — scrapers, AI search, the gate, /apply — reads these
files; they are the whole contract.

Nothing here becomes prose for you to "keep in mind": every answer
lands in a structured field the gate enforces mechanically, or in the
master CV /apply grounds every draft in. Each section below states
what its fields do, so explain the consequences when the user is
choosing.

## Step 0: read before anything

- Read `profile.yaml` and every `tracks/*.yaml` that exists. They are
  the current contract. Never overwrite them — edit them.
- If `$ARGUMENTS` contains `--section <name>`, jump straight to that
  section (sections: `identity`, `languages`, `excludes`, `track`,
  `intents`, `new-track`, `materials`). Everything else is an
  update-only flow too.
- Otherwise, interview for what is missing or stale. Existing values
  are the defaults: confirm them rather than re-asking from scratch.
  Exception: if the files still hold the shipped example data (name
  `Deniz Kaya`, base Ankara), every field counts as unasked — onboard
  from scratch.
- If the user points you at a CV, LinkedIn export, or any other
  documents, read them and propose edits grounded in that material
  instead of interviewing for facts the documents already answer.
  Extract facts only — never narrative.

## Sections

### identity

`name`, `base.city`, `base.country` (ISO-3166 alpha-2, uppercase).
Gate effect: `base.country` is the default anchor — remote postings
must be anchored to it when the track's `remote` policy is `anchored`.

### languages

`languages: {CODE: level}` — two-letter codes, free-text levels
(native / advanced / intermediate / basic). Explain the language gate
honestly: a posting requiring a language NOT listed here is
auto-rejected; one requiring a listed language is a judgment call the
human reviews. Listing optimistically silences real rejects; listing
honestly surfaces flags. Ask directly, don't skip.

### excludes

`never_countries` — hard personal deal-breakers (e.g. a country you
will never work in). Gate
effect: any posting in these countries is rejected before every other
rule, even when the posting claims remote-friendly.

### track (per track file)

The `defaults:` block. For each field, explain the gate consequence:

- `countries` — on-site allowlist; outside it (and outside relocation
  scope) means reject.
- `exclude_countries` — track-level hard rejects, merged with the
  profile's `never_countries`.
- `exclude_boards` — boards proven unreliable; rejected regardless of
  what the posting claims.
- `remote` — `no` (remote postings rejected) | `anchored` (remote OK
  only when tied to an allowed country) | `anywhere` (remote OK, but
  unanchored results are flagged for the human).
- `relocation.countries` + `relocation.requires` — on-site abroad
  acceptable only in these countries; postings there without the stated
  support (e.g. `visa_support`) are flagged, not rejected.
- `employment` — allowed types (full_time, contract, …); anything
  else rejects. `internship` only if the user means it.
- `seniority` — out-of-band is flagged, never rejected.
- `max_age_days` — staler postings reject; unknown dates flag.

### intents (per track)

For each intent: `role` (one line of direction), `keywords` (3–8
concrete search titles the boards will actually match — Turkish and
English variants), `ai_search_hints` (seed phrasings; the orchestrator
varies beyond them in every run). Proactively suggest adjacent role
types the user may not have considered — that is the value of a fresh
look. Intents may carry `overrides:` for any defaults field.

### materials (the master CV)

The personal documents layer. `materials/` (git-tracked machinery:
cover.cls, fonts, skeleton, style guide) needs no onboarding; the
gitignored `documents/` tree does:

- `documents/cv/master.tex` — the master CV, the sole fact source every
  /apply draft is grounded in. If missing, offer to import it from the
  template repo (`cp ../ai-job-search/cv/main_example.tex
  documents/cv/master.tex`) or from a file the user points at. If the
  user brings a newer CV, read it and propose edits — propose, show the
  diff, let the user confirm; never overwrite wholesale.
- When the master changes, verify it still compiles and mention the
  page count: `cd documents/cv && lualatex -interaction=nonstopmode
  master.tex` (its build artifacts stay in the gitignored dir).
- Toolchain check: `lualatex --version`, `xelatex --version`,
  `pdftotext -v`. Report what is missing instead of installing
  anything — the user decides how to install it.

### new-track

When the user wants a whole new direction (e.g. a data-science track):
create `tracks/<name>.yaml` using an existing track file as the
template, one file per direction, `track:` matching the filename. Then
walk the `track` and `intents` sections for it.

## Last step: validate, always

After every edit run `uv run hunt check` and show the result. If it
fails, fix the YAML until it passes — a spec that does not load stops
the whole hunt. No exceptions.

Then summarize what changed and suggest:

- `/hunt <track>` for a full cycle (scrapers + AI search), or
  `uv run hunt run <track>` for a first scrape.
- Re-run `/setup --section <name>` whenever a constraint changes.

Privacy: the shipped `profile.yaml` and `tracks/*.yaml` hold example
data. `/setup` replaces them with personal facts — after that, commit
them only to a private remote.

## Design principles

- Read-before-write and idempotent: existing values are defaults,
  edits are proposed and confirmed, nothing is overwritten wholesale.
- Every field lands in structured YAML the gate enforces — no prose.
- The user is tuning a machine, not writing a diary: recommend values
  by their gate consequences.
- `hunt check` green before you're done. No exceptions.