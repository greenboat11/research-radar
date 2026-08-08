# Contributing to Research Radar

Contributions welcome. The most useful things to add:

## New sources (`sources.yaml`)

Add any RSS feed, arXiv query, or supported API source. Open a PR that:
- Adds the source to `sources.yaml` with a comment explaining what it covers
- Notes which lane(s) it's relevant to
- Confirms it's freely accessible (no scraping, no ToS violations)

## New lanes (`agents/`, `config.yaml`, `sources.yaml`)

To add a new research lane:
1. Add a system prompt file to `agents/<lane-name>.md` following the existing format
2. Add the lane to `config.yaml` under `lanes:` (default `false` so it's opt-in)
3. Add arXiv queries and feed entries to `sources.yaml` for the new lane
4. Add the lane mapping in `scripts/run.py` (the `agent_file` dict in `run_specialist_agents`)

## Bug fixes and improvements

Standard GitHub flow: fork → branch → PR. Include a brief description of what changed and why.

## Reporting gaps in coverage

If you notice the tool is missing an important source, sub-area, or type of finding, open an issue with the label `coverage-gap`.

## What we're not looking for

- Exploit code or operational attack tooling of any kind
- Sources that require scraping or violate ToS
- Paid APIs without a clear free tier or bring-your-own-key path

## Code of conduct

Be direct and technical. No marketing. Cite sources.
