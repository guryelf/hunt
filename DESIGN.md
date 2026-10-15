# hunt — design

One local-first job hunt. An AI orchestrator runs the search; deterministic
code enforces the rules; everything lands in one inspectable state.

Born from two templates that stay checked out next to this repo as
read-only reference: the Job Tracker (`../ai-job-scraper` — scrapers,
dedup DB, fit scoring) and the application assistant (`../ai-job-search`
— profiles, CV/cover-letter machinery, agent-workflow shape).

## Principles

1. **Intent is data, not prose.** Constraints live in `tracks/*.yaml` and
   `profile.yaml` — versioned, diffable, edited by the human. Never in a
   prompt, never in a skill paragraph, never in code.
2. **AI improvises search; code enforces acceptance.** The orchestrator
   varies search phrasing freely — variance is why AI search finds posts
   scrapers never will. What a job must *satisfy* is checked by a
   deterministic gate, not by the agent's discipline.
3. **A job is not "found" until it passes the gate.** Find + extract +
   validate is one step. A result without extractable, in-scope fields is
   a reject with a reason — never a silent import.
4. **Provenance or it didn't happen.** Every job traces to
   track → intent → run → discovery engine → cost. Rejects are recorded
   with reasons, not discarded.
5. **Tracks are first-class.** Cyber and web-dev are separate hunts:
   own intents, own fit profile, own digest. Nothing lands in one
   uncategorized pool.

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
                                per-track digest of what's new
        │
  /apply + /outcome              materials drafted from the master CV,
                                compiled, verified, recorded; the funnel
                                shortlisted → drafted → applied → …
```

## The contract: intent specs

`tracks/<name>.yaml`. The gate and the orchestrator read the same file;
there is no second source of truth. Defaults live at track level and any
intent can override them.

| field | meaning |
|---|---|
| `countries` | ISO-3166 allowlist — where an on-site job may sit |
| `exclude_countries` | hard reject even if a posting claims remote-friendly |
| `exclude_boards` | job boards whose results proved unreliable |
| `remote` | `no` \| `anchored` (remote OK only if anchored to an allowed country) \| `anywhere` |
| `relocation.countries` | on-site abroad acceptable only here, and only … |
| `relocation.requires` | … when the posting offers these (e.g. `visa_support`) |
| `employment` | allowed employment types |
| `seniority` | allowed seniority bands |
| `max_age_days` | posting age ceiling; unknown dates are flagged, not rejected |
| `keywords` / `ai_search_hints` | search seeds; the orchestrator varies beyond them |

`profile.yaml` owns the facts every track inherits: identity, language
levels (drives the language gate), hard personal excludes.

## The gate

Deterministic, ordered rules; the first failure decides. Verdicts:

- **ACCEPT** — all rules pass.
- **FLAG** — passes the hard rules but carries a judgment call (unknown
  posting date, relocation without visa support, required language level
  above declared). Surfaced in `/review`; the human decides.
- **REJECT** — recorded in `rejects` with a machine reason:
  `country_denied`, `country_unverifiable`, `board_denied`,
  `employment_mismatch`, `stale_posting`, `duplicate`, `language_undeclared`.

Ground rules learned the hard way: **empty location = reject**, never
pass-by-default. A board in `exclude_boards` = reject regardless of what
the posting claims. Dedup is global across runs and engines — the same
job found by a scraper and by AI search is one job; first provenance
wins, later sightings are recorded as duplicates.

## State model (SQLite)

- `runs` — one per orchestration: intent, plan, engines used,
  started/finished, cost_usd, counts (found / accepted / flagged /
  rejected / duplicates).
- `jobs` — gated jobs only. Track, intent, run, engine, URL, normalized
  location (country + city), employment type, posted date, dedup key.
  `reviewed_at` / `review_note` mark the human's decision from /review.
- `rejects` — gate failures with a reason and everything known at
  rejection time; also the destination for jobs the human rejects in
  /review (reason `human_rejected`), with the job's fields preserved.
- `applications` — the funnel, one row per tracked job you intend to
  apply to (unique job reference). Status moves forward only:
  `shortlisted → drafted → applied → interview → offer → hired`, with
  `rejected`, `no_response`, `offer_declined`, `withdrawn` as the
  settled ends (withdrawn reachable from any open stage). File paths
  point at the real CV, cover letter, and archived posting.
- `application_events` — every funnel change as one row (the new
  status, or a note), giving each application its own timeline the way
  runs keep provenance.
- `meta` — small key-value store; holds the digest watermark
  (`last_digest_at`) so each digest covers exactly what the previous
  one didn't.

Dedup spans jobs *and* rejects — a posting the gate has judged is
never re-judged; first provenance wins. The DB is data, not spec:
gitignored (see `.gitignore`). To clear it, `hunt reset --confirm`
archives it to `backups/` before deleting, so a reset is reversible.
Spec lives in git (YAML/MD); state lives in SQLite.

## Orchestration

Claude Code is the orchestrator — the session is the interface; the
Streamlit app is gone. Commands (M1+): `/hunt`, `/review`, `/digest`;
(M4) `/apply` — ports the application-assistant machinery: posting
archived as untrusted data, honest match/gap readout (no LLM fit
score — the gate did the hard constraints and the human does go/no-go),
LaTeX CV + cover letter drafted *from* the master CV with a reviewer
pass, then a mandatory compile-verify-inspect loop (`hunt verify`,
`hunt layout`) before anything is recorded — and `/outcome` for what
happened after sending. `/pipeline <track>` composes the whole loop —
find, review, digest, shortlist, apply, outcome — as one pass, each
stage delegating to its playbook and keeping its decision points;
`hunt pool` feeds the shortlist stage with the tracked jobs no
application has opened on yet. Headless runs use `claude -p` — already proven
by v1's ai_search module. The tools are orchestrator-agnostic: if an
always-on agent service is ever wanted, it drives the same CLIs
against the same DB.

Materials split: the LaTeX machinery (cover.cls, fonts, skeleton, style
guide, the verification rules) is git-tracked in `materials/`; the
personal layer (master CV, per-application drafts and PDFs, archived
postings) is gitignored under `documents/`. The master CV is the sole
fact source — `/apply` tailors, it never invents.

## Milestones

- **M0 — spec layer (this commit).** Repo, DESIGN, profile, cyber +
  web-dev tracks. The contract exists as data.
- **M1 — state + gate.** SQLite schema, the gate module, `hunt` CLI
  reading track YAML; kariyer + techcareer scrapers ported from the
  tracker template. Done when the regression test below passes.
- **M2 — AI search through the gate.** The orchestrator generates varied
  phrasings per intent, extracts structured fields from what it finds,
  and everything passes the same gate. Headless mode wired.
- **M3 — review + digests (done).** `/review` walks flags and rejects
  with you — decisions persist only through `hunt resolve` (keep stamps
  the job reviewed; reject moves it to rejects as `human_rejected`
  with provenance intact). `/digest` gives per-track summaries of
  what's new since the last digest (watermarked in `meta`).
  `hunt reset --confirm` archives the DB to `backups/` before deleting.
- **M4 — application materials (done).** The funnel lives in state
  (`applications` + `application_events`, forward-only transitions
  enforced in code, written only through `hunt shortlist` /
  `materials` / `outcome`). The LaTeX machinery is ported from the
  assistant template into `materials/`; the master CV and per-
  application drafts live in gitignored `documents/`. `/apply` drafts
  from the master and verifies what it compiles (`hunt verify` =
  ATS-readable text layer + page count; `hunt layout` = hole/orphan
  geometry); `/outcome` records reality. `/interview` (stage-specific
  prep from an application's archive) is deliberately deferred — it
  needs a live funnel to be worth its complexity.

## The regression test (M1's acceptance criterion)

On 2026-10-15, a run whose entire spec was the two-word focus
"Offensive Security" pulled two djinni.co (Ukrainian board) postings
with empty location fields into the tracker — and the fit scorer graded
them "medium". M1 is done when those exact inputs produce `rejects`
rows with reasons `board_denied` / `country_unverifiable`, and no job
rows.

## Non-goals

- No web UI (a thin report page is possible later, not day one).
- No multi-user, no accounts, no cloud.
- No always-on service until the interactive loop is proven.
- No LLM inside the gate. Judgment is the orchestrator's; the gate is code.