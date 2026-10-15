# hunt

A local-first job hunt, orchestrated by Claude Code and enforced by code.

You describe what you're hunting in `tracks/*.yaml` (structured
constraints: countries, remote policy, employment types, posting age)
and `profile.yaml` (facts and deal-breakers). An orchestrator turns those
into searches — deterministic portal scrapers *and* varied AI web
searches — and everything found must pass a deterministic gate before it
becomes a tracked job. Nothing lands with an empty location or an
out-of-scope country. Every job carries provenance: which track, which
intent, which run, which engine, what it cost.

The session is the interface: no web app, no server, no cloud. Claude
Code (or `claude -p`, headless) drives deterministic CLI verbs; the
verbs enforce the rules; SQLite keeps the state; git keeps the spec.

## Why this exists

Hunt was rebuilt from two earlier projects after their agent runs went
messy: shared mutable files across parallel sessions, state split
between three stores with no provenance, and — the incident that set
the design — a run whose entire spec was a two-word focus pulled two
postings from a foreign job board with **empty location fields**, and
a fit scorer graded them "medium". The location rule existed, but only
as prose the agent was asked to apply.

Hunt's answer: a job is not "found" until it passes code. Empty
location is a machine reject (`country_unverifiable`), never a
judgment call — and the exact incident inputs live on as a regression
test.

## The loop

```
profile.yaml + tracks/*.yaml     spec layer — you edit, git tracks
        │
  /hunt cyber                   you ↔ Claude Code (or `claude -p` headless)
        │
  orchestrator plans            per intent: portal queries + varied AI-search phrasings
        │
  discovery engines             deterministic scrapers AND AI web search,
        │                       both extract structured fields
  the gate (code)               country allowlist · employment type ·
        │                       date window · language gate · dedup
   ACCEPT / FLAG / REJECT
        │
  state.db (SQLite)             jobs · runs · rejects — all with provenance
        │
  /review + /digest             Claude walks you through flags & rejects;
        │                       per-track digest of what's new
        │
  /apply + /outcome              materials drafted from the master CV,
                                compiled, verified, recorded; the funnel
        │
  /pipeline                     the whole loop above as one pass
```

`/pipeline <track>` sequences the stages — find, review, digest,
shortlist, apply, outcome — each delegating to its own playbook and
keeping every human decision point.

## Principles

1. **Intent is data, not prose.** Constraints live in YAML — versioned,
   diffable, edited by the human. Never in a prompt, never in code.
2. **AI improvises search; code enforces acceptance.** Variance is why
   AI search finds posts scrapers never will. What a job must
   *satisfy* is checked by a deterministic gate, not by discipline.
3. **A job is not "found" until it passes the gate.** A result without
   extractable, in-scope fields is a reject with a reason — never a
   silent import.
4. **Provenance or it didn't happen.** Every job traces to
   track → intent → run → engine → cost. Rejects are recorded, not
   discarded.
5. **Tracks are first-class.** Cyber and web-dev are separate hunts:
   own intents, own constraints, own digest.

## Quickstart

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), and —
only for the materials half — a TeX Live with `lualatex` + `xelatex`
plus Poppler's `pdftotext` (see [materials/README.md](materials/README.md)).

```console
$ git clone https://github.com/guryelf/hunt.git
$ cd hunt
$ uv sync                      # pydantic · typer · pyyaml · requests · bs4
$ uv run hunt check            # validate the spec layer
```

Then open Claude Code in the repo — the playbooks do the rest:

```console
/setup                # onboard into profile.yaml + tracks/*.yaml, ends green
/pipeline cyber       # the whole loop, one pass
```

Every stage is also a standalone command: `/hunt cyber` for a single
search cycle, `/review` when flags are waiting, `/apply 12` when a
job is worth pursuing. Headless cycles work too:
`claude -p "/hunt cyber"` — the deterministic stages run; judgment
calls stay with the human.

The committed `profile.yaml` and `tracks/*.yaml` ship **example data**
— a fictional persona, so the repo carries no personal facts. `/setup`
replaces them with yours; after onboarding, commit your real spec only
to a private remote.

## The slash commands

| command | what it does |
|---|---|
| `/setup` | onboard into or update the spec layer; always ends with `hunt check` green |
| `/hunt <track>` | one full cycle: portal scrapers + varied AI web search, everything through the gate |
| `/review [track]` | walk the gate's flags (and recent rejects) with the human; decisions persist via `hunt resolve` |
| `/digest [track]` | what's new per track since the last digest |
| `/apply <job id>` | draft the CV and cover letter for one tracked job: honest match/gap readout, compile, verify, record |
| `/outcome` | record what happened to your applications: stage moves, settled ends, notes |
| `/pipeline <track>` | the whole loop in one pass, each stage delegating to its playbook; `--from <stage>` resumes mid-pipeline |

## The CLI

The deterministic half of the loop — AI orchestrates, these verbs are
the only write paths.

**Gated writes (the only way a job enters):**

```console
uv run hunt run cyber                    # scrape portals per intent + gate
uv run hunt add cyber --intent soc-analyst < batch.json
                                         # gate AI-search results + record them
```

**Human decisions and the funnel:**

```console
uv run hunt resolve 12 --decision reject --note "no visa support"
uv run hunt shortlist 12 --note "strong fit"   # open the funnel on a job
uv run hunt materials 3 --cv x/cv.pdf --cover x/cover.pdf --posting x/job_posting.md
uv run hunt outcome 3 --status interview --note "HR call Friday"
```

**Reads:**

```console
uv run hunt check                       # validate the spec layer
uv run hunt status                      # per-track counts + last run
uv run hunt review                      # flagged jobs awaiting your call
uv run hunt rejects                     # latest gate rejects, with reasons
uv run hunt job 12                      # one tracked job + its application
uv run hunt applications --status applied
uv run hunt pool                        # tracked jobs with no application yet
uv run hunt digest                      # what's new since the last digest
uv run hunt gate cyber --intent soc-analyst < one.json   # read-only verdict
```

**Materials QA and housekeeping:**

```console
uv run hunt verify cv.pdf --pages 2 --ascii-dates   # ATS text layer, page count
uv run hunt layout cv.pdf             # holes and orphans the eye misses
uv run hunt reset --confirm           # archive state.db to backups/, start fresh
```

## The gate

Deterministic, ordered rules; the first failure decides.

- **ACCEPT** — all rules pass.
- **FLAG** — passes the hard rules but carries a judgment call only
  the human can settle: `remote_anchored`, `remote_anywhere`,
  `relocation`, `relocation_no_support`, `date_unknown`,
  `employment_unknown`, `seniority_out_of_band`.
- **REJECT** — recorded with a machine reason: `country_denied`,
  `country_unverifiable`, `board_denied`, `employment_mismatch`,
  `stale_posting`, `language_undeclared`, `duplicate`.

Ground rules learned the hard way: **empty location = reject**, never
pass-by-default. An excluded job board is a reject regardless of what
the posting claims. Dedup is global across runs and engines — the
same job found by a scraper and by AI search is one job; first
provenance wins.

## The application funnel

One application per tracked job, and it only moves forward:

```
shortlisted → drafted → applied → interview → offer → hired
                    ↘ any open stage → withdrawn
finals: hired · rejected · no_response · offer_declined · withdrawn
```

Every change — a status move or a bare note ("recruiter promised a
reply by Friday") — lands on that application's event timeline, the
same way runs keep provenance. The funnel is written only through
`hunt shortlist`, `hunt materials`, and `hunt outcome`; a refusal from
the command is the design working.

## Materials

The LaTeX machinery is git-tracked in `materials/` (cover class,
OpenFonts, template skeleton, style guide, and the compile rules in
[materials/README.md](materials/README.md)). The personal layer — the
master CV (the sole fact source; /apply tailors, it never invents),
per-application drafts, archived postings — is gitignored under
`documents/`.

Every draft is compiled and inspected before anything is recorded:
`hunt verify` checks what an ATS can actually read (text layer, page
count, ASCII date ranges), `hunt layout` measures the geometry the eye
misses (holes, orphaned entry headers, thin pages), and both PDFs get
a visual read.

## State model

One SQLite file, additive migrations, everything inspectable:

- `runs` — one per orchestration: intent, engines, counts, cost.
- `jobs` — gated jobs only, with provenance and the human's review marks.
- `rejects` — gate failures with reasons; also where /review rejections
  land (`human_rejected`), fields preserved.
- `applications` + `application_events` — the funnel and its timelines.
- `meta` — the digest watermark: each digest covers exactly what the
  previous one didn't.

The DB is generated state (gitignored, never hand-edited); the spec is
git. See [DESIGN.md](DESIGN.md) for the full contract.

## Repo layout

```
├── profile.yaml          # example facts (name, base, languages) — /setup makes it yours
├── tracks/*.yaml         # one YAML per hunt (constraints + intents)
├── materials/            # LaTeX machinery: class, fonts, templates, rules
├── documents/            # gitignored: master CV, drafts, archived postings
├── src/hunt/
│   ├── spec.py           # the YAML contract (pydantic)
│   ├── normalize.py      # location → country/city, dedup keys
│   ├── gate.py           # the deterministic gate
│   ├── state.py          # SQLite: schema, migrations, funnel
│   ├── verify.py         # ATS text-layer QA for compiled PDFs
│   ├── layout.py         # PDF geometry QA
│   ├── cli.py            # the verbs
│   └── engines/         # portal scrapers
├── .claude/commands/     # the playbooks (setup, hunt, review, digest,
│                         #  apply, outcome, pipeline)
└── tests/                # 92 tests
```

## Non-goals

- No web UI (a thin report page is possible later, not day one).
- No multi-user, no accounts, no cloud.
- No always-on service until the interactive loop is proven.
- No LLM inside the gate. Judgment is the orchestrator's; the gate is code.

## Milestones

- **M0** — spec layer: DESIGN, profile, cyber + web-dev tracks.
- **M1** — state + gate: SQLite schema, gate module, `hunt` CLI,
  kariyer + techcareer scrapers. The djinni regression passes.
- **M2** — AI search through the gate: `hunt add`, `hunt check`,
  the /hunt orchestrator playbook, headless wired.
- **M3** — review + digests: `hunt review` / `resolve` / `digest`,
  watermarked windows, reversible `hunt reset`.
- **M4** — application materials: the funnel in state, LaTeX machinery
  ported to `materials/`, /apply + /outcome, `hunt verify` + `layout`.
- **After M4** — `/pipeline`: the whole loop as one pass, `hunt pool`.

`uv run pytest` · `uv run ruff check` — 92 tests, clean.