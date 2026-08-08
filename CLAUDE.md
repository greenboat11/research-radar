# CLAUDE.md — Research Radar

Project memory for Claude Code sessions in this repository.

## What This Is

**Research Radar** is an open-source multi-agent AI-security research gap finder.
It runs on GitHub Actions, scans the open literature on a schedule, and maintains
a living document of open research questions across six lanes.

## Three Core Areas (every gap must touch ≥1)

1. **Secure Compute & Weight Protection** — TEEs, HSMs, weight exfiltration threat models, datacenter security, supply chain integrity, compute governance
2. **Autonomous Cyber Capability & Defense** — capability benchmarks, CTF-to-real generalization, RL-based MTD, AI honeypots/deception, AI-attack detection
3. **Recursive Self-Improvement** — self-evolving agents, ARA evals, automated ML research loops, capability-gain measurement, containment

Intersection gaps (spanning ≥2 areas) are the highest-value output. The analyst agent is explicitly tasked with finding them.

## Pipeline

```
collect.py (no LLM) → triage.py (Haiku) → specialist agents (Sonnet) → analyst (Opus) → digest + state update
```

All model names are in `config.yaml`. Change `analyst` to Sonnet to reduce cost ~4x.

## File Roles

| File | Role |
|------|------|
| `config.yaml` | Cadence, lane toggles, model selection |
| `sources.yaml` | All feeds and queries — edit to tune coverage |
| `agents/*.md` | System prompts for each specialist + analyst |
| `state/seen_urls.json` | Dedup ledger (machine-managed) |
| `state/known_gaps.json` | Structured mirror of RESEARCH_FRONTIERS.md |
| `digests/` | Committed dated digests — the showcase |
| `RESEARCH_FRONTIERS.md` | Living document — machine-updated |

## Gap Schema

Every gap entry in `RESEARCH_FRONTIERS.md` and `known_gaps.json`:

```
### [Q-X.Y] One-sentence question title
**Question:** Full statement.
**Why Hard:** What makes it genuinely open.
**Who's Closest:** Real labs/researchers/papers only — no fabrications.
**Methodological Approach:** How to attack it.
**First Added / Last Updated / Status:** 🔥 Heating up | Stable | Getting Answered
```

## Guardrails (enforced everywhere)

- No fabricated citations. "Who's Closest" must be real and verifiable.
- Defensive framing: measurement, detection, defense. No exploit code, no attack recipes.
- Every gap cites ≥1 real source. No source → no gap.
- Fail-soft: one dead source or failed agent logs and continues, never aborts the run.

## Cadence (configurable in config.yaml)

- Collection: daily (cheap — Haiku triage)
- Analysis: weekly (default) or when ≥30 new high-confidence candidates accumulate
- Early trigger threshold: configurable

## Cost (rough estimate, default config)

- Collection + triage: ~$0.01–0.03/day (Haiku)
- Weekly analysis (4 lanes + Opus analyst): ~$0.15–0.50/run
- Switch analyst to Sonnet: ~$0.10–0.20/run

## GitHub Actions Notes

- Workflow: `.github/workflows/radar.yml`
- Required secret: `ANTHROPIC_API_KEY`
- Optional secrets: `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `EMAIL_TO` (or SMTP equivalents)
- Scheduled workflows are disabled on forks by default — enable in the Actions tab
- The bot commits state + digests back with `permissions: contents: write`
