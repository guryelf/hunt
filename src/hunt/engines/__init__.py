"""Discovery engines: deterministic scrapers that emit gate Candidates.

Each engine is orchestrator-agnostic — a plain class with a ``scrape``
method — so the interactive session, ``claude -p``, or any future agent
service can drive the same hunt.
"""
