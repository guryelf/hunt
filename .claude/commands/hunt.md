# /hunt — run one full hunt cycle for a track

$ARGUMENTS: `<track> [--intent NAME] [--scrape-only] [--ai-only] [--remote-only] [--results N]`

You are the orchestrator of one hunt cycle. The spec (`profile.yaml` +
`tracks/<track>.yaml`) is your plan; the gate is the only judge of
what gets tracked. Read CLAUDE.md first — its rules govern every step,
headless or interactive.

## Step 1: state the plan

Read `tracks/<track>.yaml` and `profile.yaml`. Tell the user: which
intents will run, the keywords each will scrape, and 2–3 of the
AI-search phrasings you intend to vary per intent. Varying phrasings
is the point — AI search finds posts the scrapers never will.

## Step 2: deterministic scrapers (skip with `--ai-only`)

```
uv run hunt run <track> [--intent NAME] [--remote-only] [--results N]
```

Report the per-intent summary lines and any engine warnings. Blocked
boards degrade gracefully — they are warnings, not failures.

## Step 3: AI search (skip with `--scrape-only`)

For each intent:

1. Generate 3–6 varied phrasings from the intent's `keywords` and
   `ai_search_hints` — alternate Turkish/English, add location and
   seniority qualifiers, rephrase. Do not repeat one stiff query six
   times; variance is the feature.
2. WebSearch each phrasing. Skip any board named in the track's
   `exclude_boards` — don't spend searches on sources already proven
   unreliable.
3. WebFetch the promising postings (career pages, boards, aggregators
   — anything real; the URL must be the posting itself).
4. Extract ONE JSON object per posting, exactly this schema:

   | field | rule |
   |---|---|
   | `title`, `url`, `company` | the posting's real values |
   | `location_raw` | the location text EXACTLY as published — never guessed, never filled in from company knowledge; absent → `null` |
   | `remote` | `true` only when the posting explicitly says so; else `null` |
   | `employment` | `full_time`/`contract`/`part_time`/`internship` as stated; else `null` |
   | `seniority` | `junior`/`mid`/`senior`/`lead` if inferable from the title; else `null` |
   | `posted_date` | ISO `YYYY-MM-DD` when the page shows one; else `null` |
   | `languages_required` | only explicit requirements, e.g. `["EN"]` |
   | `visa_support` | `true` only when explicitly offered |
   | `description` | the requirements text, trimmed |

   Never fabricate a field to "save" a posting. A reject for missing
   data is the system working — an unverifiable location was exactly
   how the djinni incident happened (see DESIGN.md).

5. Pipe the batch for this intent:

```
uv run hunt add <track> --intent <name> [--cost USD] < batch.json
```

   `hunt add` gates everything, records accepts and flags as jobs and
   rejects with machine reasons, and prints one JSON verdict line per
   candidate plus a run summary. NEVER write `state.db` yourself, and
   never insert a result that did not come out of `hunt add` or
   `hunt run`. `hunt gate` exists for read-only dry runs of a single
   candidate.

## Step 4: report

```
uv run hunt status
```

Summarize: new accepts (title, company, country), flags with their
reasons — those are the judgment calls for the human — and a short
histogram of reject reasons. When the session reports a cost, pass it
as `--cost` on the matching `hunt add`.

## Headless

The whole cycle is one command, run from the hunt repo root:

```
claude -p "/hunt cyber --intent soc-analyst"
```