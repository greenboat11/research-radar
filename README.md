# Research Radar

> *An open-source multi-agent AI-security research gap finder*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub Actions](https://img.shields.io/badge/runs%20on-GitHub%20Actions-2088FF?logo=github-actions)](https://github.com/features/actions)

Research Radar scans the open literature on a schedule, routes findings to domain-specialist AI agents, and synthesizes everything into a dated digest of open research questions — with special emphasis on **intersection gaps** that span multiple domains and are hardest to find without cross-domain reasoning.

The repo is the product: every run commits a digest to [`digests/`](digests/) and updates [`digests/latest.md`](digests/latest.md), building a public archive of the tool's output over time.

---

## What it tracks

Six research lanes, configurable in `config.yaml`:

| Lane | Core question |
|------|--------------|
| **Secure Compute & Weight Protection** | How do you protect model weights and training infrastructure at the hardware and systems level? |
| **Autonomous Cyber Capability & Defense** | What are the current ceilings of autonomous offensive AI, and what defensive systems can match them? |
| **Recursive Self-Improvement** | How do you measure, detect, and contain self-improving AI systems before capability gain becomes uncontrollable? |
| **Multi-Agent Security & Compositional Risk** | What attack surfaces emerge when agents interact that don't exist in single-agent systems? |
| **Adversarial ML & Prompt Injection** *(optional)* | How do jailbreak/defense co-evolution dynamics play out, and what robustness measures have empirical support? |
| **AI Governance & Eval Methodology** *(optional)* | What makes a safety evaluation credible, and where do current frameworks fall short? |

The analyst agent explicitly hunts **intersection gaps** — questions that span ≥2 lanes and that no single specialist would surface. These are the highest-value output.

---

## Architecture

```
GitHub Actions (scheduled weekly)
  └─ scripts/run.py  (orchestrator)
       ├─ collector/collect.py   Python, no LLM → candidates.jsonl
       │    Sources: arXiv · RSS/lab blogs · Hacker News · LessWrong
       │             Reddit · OpenAlex citation expansion
       │
       ├─ collector/triage.py    Haiku → tag each candidate to lane(s)
       │
       ├─ agents/  (one specialist per enabled lane)
       │    Each reads its triaged slice → proposes gaps in a structured schema
       │    Model: Sonnet (configurable)
       │
       ├─ agents/analyst.md
       │    Deduplicates across lanes · cross-links findings · surfaces intersections
       │    Writes the final digest  ·  Model: Opus (configurable)
       │
       ├─ state/  seen_urls.json + known_gaps.json  (committed, machine-updated)
       │    Prevents re-reporting the same gap on every run
       │
       └─ digests/  <date>.md + latest.md  (committed — the archive)
```

**Key design choices:**
- Collection uses zero LLM calls — cheap and deterministic
- Expertise lives only in the agent stages, not in collection or routing
- Committed state means the system remembers what it's already found
- Opus for the analyst is intentional: cross-domain synthesis is where model capability pays off

---

## Fork and run in 5 steps

1. **Fork** this repo

2. **Add your API key** — repo Settings → Secrets → Actions → New secret:
   - `ANTHROPIC_API_KEY` *(required)*
   - `RESEND_API_KEY` + `RESEND_FROM_EMAIL` + `EMAIL_TO` *(optional — email digest)*
   - Or SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (set `email.provider: smtp` in `config.yaml`)

3. **Enable Actions** — in the Actions tab, enable workflows for your fork
   *(disabled on forks by default)*

4. **Tune coverage** (optional) — edit `sources.yaml` to add feeds, and `config.yaml`
   to enable/disable lanes or change models

5. **Wait for the schedule** (Monday 08:00 UTC) or trigger manually:
   Actions → Research Radar → Run workflow

---

## Running locally

```bash
git clone https://github.com/yourusername/research-radar.git
cd research-radar
pip install -r requirements.txt
cp .env.example .env
# Add ANTHROPIC_API_KEY (and optional email secrets) to .env

# Full run (collect + analyze)
python scripts/run.py

# Collect and triage only
python scripts/run.py --collect-only

# Force analysis from existing candidate pool
python scripts/run.py --analyze-only --force

# Preview email HTML without sending
python scripts/send_email.py --dry-run
```

---

## Cost

Collection (daily, no LLM): free
Triage (Haiku): ~$0.01–0.03/day
Weekly analysis — 4 lanes (Sonnet) + analyst (Opus): **~$0.20–0.50/run**
Switch `analyst` to `claude-sonnet-4-6` in `config.yaml` to cut cost ~4x.

A public GitHub repo gets [free Actions minutes](https://docs.github.com/en/billing/managing-billing-for-github-actions/about-billing-for-github-actions).

---

## Customization

All research coverage is driven by two files — no code changes needed:

- **`sources.yaml`** — add or remove feeds, arXiv queries, subreddits
- **`config.yaml`** — enable/disable lanes, change models, adjust cadence

To add a new research lane entirely, see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Project structure

```
research-radar/
├── .github/workflows/radar.yml   # Scheduled workflow + manual dispatch
├── agents/                       # System prompts for each specialist + analyst
│   ├── secure-compute.md
│   ├── cyber-capability.md
│   ├── recursive-improvement.md
│   ├── multiagent-security.md
│   ├── adversarial-ml.md
│   ├── ai-governance.md
│   └── analyst.md
├── collector/
│   ├── collect.py                # Source harvester (no LLM)
│   └── triage.py                 # Lane tagger (Haiku)
├── scripts/
│   ├── run.py                    # Main orchestrator
│   ├── send_email.py             # Resend / SMTP email
│   └── sync_known_gaps.py        # Rebuild state from RESEARCH_FRONTIERS.md
├── state/
│   ├── seen_urls.json            # Dedup ledger
│   └── known_gaps.json           # Structured gap registry
├── digests/                      # Committed digest archive
│   └── latest.md
├── RESEARCH_FRONTIERS.md         # Living document (machine-updated)
├── config.yaml                   # Lane toggles, models, cadence
├── sources.yaml                  # All feeds and queries
└── CLAUDE.md                     # Project memory for Claude Code sessions
```

---

## Guardrails

Every proposed gap must: cite a real source, name who's closest (no fabrications), and pass a defensive-framing check — measurement, detection, or defense only. No exploit code, no operational attack procedures. One dead source or failed agent logs and continues; it never aborts the run.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New sources and lanes welcome.

## License

[MIT](LICENSE)
