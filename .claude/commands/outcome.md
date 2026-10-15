# /outcome — record what happened to your applications

You are walking the human through the application funnel and recording
reality. Input: none, or an application id (`/outcome 3`).

1. List the funnel: `uv run hunt applications` — newest first, each
   line with its job title, company, status, and updated_at. If the
   human names one, read it with `uv run hunt job <job_id>`; otherwise
   walk the open ones, offering all / one / skip like /review.

2. For each, ask what happened since it last moved. Record exactly
   what the human says, no editorializing:

   - a stage move:
     `uv run hunt outcome <id> --status applied|interview|offer --note "..."`
   - the settled ends:
     `--status hired|rejected|no_response|offer_declined|withdrawn`
     (withdrawn is legal from any open stage)
   - no news yet — a note-only call records feedback on the timeline
     without moving anything:
     `uv run hunt outcome <id> --note "recruiter promised a reply by Friday"`

3. The funnel never moves backwards — the command enforces it, so a
   refusal is the system working. When the human wants a backwards
   move, what they usually mean is a new application to a new posting;
   shortlist that job instead.

4. Notes are worth asking for on every transition: interview dates,
   interviewer names, feedback verbatim, salary numbers. They land on
   the application's event timeline.

5. Close with `uv run hunt digest` and narrate the `applications`
   snapshot: where the funnel stands, what needs action (drafted but
   not applied, applied long ago with silence), and suggest `/apply`
   for shortlisted jobs that still need materials.