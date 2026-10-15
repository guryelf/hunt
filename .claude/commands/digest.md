# /digest — what's new since last time, per track

$ARGUMENTS: `[track]`

You are narrating the hunt's ledger. `hunt digest` counts what changed
since the last digest — the watermark makes each run cover exactly
what the previous one didn't. Read CLAUDE.md first; its rules govern
every step.

## Step 1: pull the digest

```
uv run hunt digest [track]
```

One JSON object: new jobs per track and verdict since the watermark
(`tracks`), the newest of them by name (`new`), flags still awaiting a
human (`flags_outstanding`), reject reasons since (`reject_reasons`),
and runs + cost since (`runs`, `cost_usd`).

## Step 2: narrate it for the human

- **New per track** — N accepts, M flags; name the stand-outs from
  `new` (title, company, country). The human should leave the digest
  knowing what entered their hunt, not just how many things did.
- **Flags outstanding** — judgment calls waiting. If any, suggest
  `/review [track]`.
- **Rejects since** — translate the reason histogram into one honest
  sentence about what the gate kept out. No action unless a single
  reason dominates; if it does, that's /review Step 3's spec
  conversation, not this command's.
- **Runs + cost** — how many cycles ran and what they cost. Cost is
  the AI-search meter; state it plainly, especially when it climbs.

The first run has no watermark, so the digest is all-time — say so.
An explicit `--since` reads a window without moving the watermark;
never present a `--since` run as "since last time".

## Step 3: point the way

- flags outstanding > 0 → `/review [track]`
- nothing new → `/hunt [track]` for a fresh cycle
- cost climbing with little to show → say it; the human may want to
  prune intents or tighten ai_search_hints.

Headless works: `claude -p "/digest"` is a read plus a narration.