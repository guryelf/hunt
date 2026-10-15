# /review — walk the flagged jobs and recent rejects with the human

$ARGUMENTS: `[track]`

You are the review half of the loop. The gate flags what it cannot
judge — remote-with-anchor, relocation, unknown dates, seniority out
of band. Flags are judgment calls that belong to the human, never to
you. Read CLAUDE.md first; its rules govern every step.

## Step 1: list the queue

```
uv run hunt review [track] [--limit N]
```

One JSON line per outstanding flagged job (id, title, company, url,
location, flags, provenance). An empty queue still deserves Step 3 —
rejects are worth a look even when nothing awaits a decision.

## Step 2: walk the flags in small batches

Present at most ~5 at a time; never dump the whole queue. For each job
show: title, company, URL, country/city, posted date, engine + intent
(its provenance), and its flags translated:

- `remote_anchored` — remote-friendly and anchored to an allowed
  country; check how often they expect you onsite.
- `remote_anywhere` — fully remote with no country tie; the track
  normally wants one.
- `relocation` / `relocation_no_support` — would require moving; the
  second means no visa support was mentioned.
- `date_unknown` — the gate couldn't read a posting date; check how
  fresh the posting is before trusting it.
- `employment_unknown` — employment type unstated in the posting.
- `seniority_out_of_band` — title reads outside the track's seniority
  band; the human's call, not a reject.

Never hide a posting behind its flag: if the description in the JSON
line isn't enough to judge, WebFetch the URL. Never invent details the
posting doesn't state. For each job, ask the human: **keep, reject, or
skip?**

- keep → `uv run hunt resolve <id> --decision keep [--note "..."]`
- reject → `uv run hunt resolve <id> --decision reject --note "..."`
- skip → resolve nothing; it stays queued for next time. Skipped is
  deferred, not discarded.

NEVER edit state.db by hand, and never treat a flag as a reject
without the human's explicit decision. Notes are the human's voice —
record what they say, not what you infer.

## Step 3: read the rejects

```
uv run hunt rejects [track] --limit 30
```

Walk them briefly — they are the gate's audit trail, not a to-do list.
Never rescue a reject by hand. When ONE reason dominates, name it and
suggest the fix in the SPEC layer, never in the gate or the DB:

- `country_unverifiable` — discovery is skipping location_raw. That is
  the djinni failure mode: re-read the extraction rules in /hunt
  Step 3 with the human — location must come from the posting itself.
- `board_denied` — the exclude_boards rule is doing its job. Nothing
  to fix.
- `stale_posting` — the boards' reality may be older than
  max_age_days; the human can loosen it in tracks/<track>.yaml.
- `employment_mismatch` — a stream of wanted-but-mismatched types may
  mean the spec is stricter than the human's actual intent.
- `language_undeclared` — either the postings demand a language the
  human doesn't have, or profile.yaml understates their languages.

If the human accepts a change, edit the YAML (you own the spec edit —
like /setup, read before writing, change only what they named), then
`uv run hunt check` must end green.

## Step 4: report

Summarize: kept N, rejected N (with their notes), skipped N, and the
dominant reject reason with any spec change the human accepted. Then
point on: `/digest` for the numbers, `/hunt <track>` for the next
cycle.