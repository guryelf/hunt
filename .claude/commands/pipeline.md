# /pipeline — the whole loop in one pass

$ARGUMENTS: `<track> [--from find|review|digest|shortlist|apply|outcome]`

You are running the full real-use loop for one track: **find →
review → digest → shortlist → apply → outcome**. This command is the
pipeline's spine, not an autopilot: it sequences the existing
playbooks, delegates each stage to them, and stops for the human
wherever the standalone command would. Read CLAUDE.md first; its
rules govern every step.

`--from <stage>` starts mid-pipeline when earlier stages are already
done — a hunt cycle ran this morning: `/pipeline cyber --from review`.

A stage with nothing to do says so and passes: an empty review queue,
a digest with nothing new, a pool with no jobs. Each is a result to
report, not a failure to fix. Never pad an idle stage.

## Stage 1: find

Run one hunt cycle for the track exactly as
`.claude/commands/hunt.md` defines it — plan from the spec,
`uv run hunt run`, varied AI-search phrasings through
`uv run hunt add`, everything through the gate. When it finishes,
report the cycle: found / accepted / flagged / rejected, duplicates,
and cost.

## Stage 2: review

Settle the gate's flags exactly as `.claude/commands/review.md`
defines it — batches of ~5, keep/reject only through
`uv run hunt resolve`, then rejects → spec tuning when one reason
dominates. Empty queue: say so, move on.

## Stage 3: digest

```
uv run hunt digest
```

Full digest, not `--track` — the watermark is global, and this pass
consumes the whole window it advances. Narrate it exactly as
`.claude/commands/digest.md` defines it, this track's news front and
center, other tracks one line each. The funnel snapshot is the last
one before this pass starts moving applications; the next pipeline's
digest covers exactly what this one didn't.

## Stage 4: shortlist

```
uv run hunt pool --track <track>
```

The shortlist pool: tracked jobs — gate accepts and human-kept flags
— with no application yet, newest first. Present them compactly (id,
title, company, location, posted date), at most ~5 per batch, like
/review. The human picks; never auto-shortlist. For each pick:

```
uv run hunt shortlist <id> --note "<why, one line, in the human's words>"
```

"None right now" is a fine answer — the pool stays pooled. Skipped
jobs are deferred, not discarded; they are here next pass.

## Stage 5: apply

The applications this pass opened that still need materials:

```
uv run hunt applications --track <track> --status shortlisted
```

Offer to draft now, one at a time. When the human says go, follow
`.claude/commands/apply.md` end to end for that job — including the
mandatory compile-verify-inspect loop and `uv run hunt materials` at
the end. "Not now" leaves it shortlisted for later; /apply is
heavyweight and never automatic. When they actually send something,
that is an outcome (`applied`), not an apply — say so and leave it
for Stage 6.

## Stage 6: outcome

Walk the open applications exactly as `.claude/commands/outcome.md`
defines it: what happened since each last moved, recorded through
`uv run hunt outcome` — stage moves, settled ends, or note-only.

## Close: the run report

One summary at the end: what the hunt found, what review settled,
what was shortlisted, what got materials, where the funnel stands
now, and what awaits the human — outstanding flags, pool jobs left
untouched, applications to send or chase. Then point on: the next
`/pipeline <track>` for a fresh pass, or a standalone stage when one
part needs more attention than a pass gives it.

## Headless

`claude -p "/pipeline cyber"` runs the deterministic stages, but the
run stops at its first human decision and reports what awaits. Never
let a headless run resolve flags, shortlist jobs, draft materials, or
record outcomes: those judgments are the human's — in a pipeline
exactly as they are standalone.