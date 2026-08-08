"""
scripts/run.py — Main orchestrator for Research Radar.

Phases:
  1. Collect (daily): fetch new candidates, dedup, write raw.jsonl
  2. Triage (daily): tag candidates to lanes
  3. Analyze (weekly or on threshold): run specialist agents + analyst, update state, write digest
  4. Email (optional): send digest via Resend or SMTP

Usage:
  python scripts/run.py                  # full run (collect + analyze if due)
  python scripts/run.py --collect-only   # collect and triage, no analysis
  python scripts/run.py --analyze-only   # analyze from most recent triaged pool
  python scripts/run.py --force          # force analysis even if not due
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "state"
CANDIDATES_DIR = STATE_DIR / "candidates"
DIGESTS_DIR = ROOT / "digests"
AGENTS_DIR = ROOT / "agents"
CONFIG_FILE = ROOT / "config.yaml"
KNOWN_GAPS_FILE = STATE_DIR / "known_gaps.json"
RESEARCH_FRONTIERS_FILE = ROOT / "RESEARCH_FRONTIERS.md"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "logs" / "run.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config & State
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_known_gaps() -> dict:
    if KNOWN_GAPS_FILE.exists():
        return json.loads(KNOWN_GAPS_FILE.read_text())
    return {"gaps": [], "last_updated": ""}


def save_known_gaps(gaps: dict) -> None:
    gaps["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    KNOWN_GAPS_FILE.write_text(json.dumps(gaps, indent=2))


def load_env() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def analysis_is_due(config: dict) -> bool:
    """Check if weekly analysis cadence has elapsed since last digest."""
    digests = sorted(DIGESTS_DIR.glob("*.md"))
    # filter out latest.md
    dated = [d for d in digests if d.stem != "latest"]
    if not dated:
        return True
    last_date_str = dated[-1].stem
    try:
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        cadence = config.get("cadence", {}).get("analysis", "weekly")
        days = 7 if cadence == "weekly" else 1
        return (datetime.now(timezone.utc) - last_date).days >= days
    except ValueError:
        return True


def count_new_candidates(config: dict) -> int:
    """Count triaged candidates since last analysis."""
    dated_digests = sorted(d for d in DIGESTS_DIR.glob("*.md") if d.stem != "latest")
    if dated_digests:
        try:
            last = datetime.strptime(dated_digests[-1].stem, "%Y-%m-%d")
        except ValueError:
            last = datetime.now(timezone.utc) - timedelta(days=7)
    else:
        last = datetime.now(timezone.utc) - timedelta(days=7)

    total = 0
    for f in CANDIDATES_DIR.glob("*-triaged.jsonl"):
        date_str = f.stem.replace("-triaged", "")
        try:
            fdate = datetime.strptime(date_str, "%Y-%m-%d")
            if fdate > last:
                total += sum(1 for _ in f.open())
        except ValueError:
            pass
    return total


# ---------------------------------------------------------------------------
# Agent calls
# ---------------------------------------------------------------------------

def call_agent(agent_file: str, user_content: str, model: str,
               max_tokens: int, client: anthropic.Anthropic) -> str:
    system_prompt = (AGENTS_DIR / agent_file).read_text()
    for attempt in range(5):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            return resp.content[0].text
        except anthropic.RateLimitError:
            wait = 65
            log.warning("Rate limit (attempt %d), waiting %ds", attempt + 1, wait)
            time.sleep(wait)
        except Exception as e:
            log.error("Agent call failed (attempt %d): %s", attempt + 1, e)
            time.sleep(10)
    return ""


def extract_json_block(text: str) -> list | dict | None:
    pattern = r"```json\s*([\s\S]*?)\s*```"
    matches = re.findall(pattern, text)
    for match in reversed(matches):  # last block is usually the structured output
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    return None


def build_specialist_prompt(lane: str, candidates: list[dict], known_gaps: dict) -> str:
    lane_candidates = [c for c in candidates if lane in c.get("lanes", [])]
    if not lane_candidates:
        return f"No candidates tagged for the '{lane}' lane this period. Output []."

    items = "\n\n".join(
        f"[{i+1}] {c['title']}\nSource: {c['source']} | URL: {c['url']} | Date: {c['published']}\n{c['abstract'][:600]}"
        for i, c in enumerate(lane_candidates[:40])
    )

    existing_titles = "\n".join(
        f"- {g.get('title', 'untitled')} [{g.get('id', '')}] — {g.get('status', '')}"
        for g in known_gaps.get("gaps", [])
        if lane in g.get("lanes", [])
    )

    return f"""CANDIDATE POOL ({len(lane_candidates)} items for your lane):

{items}

EXISTING KNOWN GAPS IN YOUR LANE (do not re-propose these):
{existing_titles or '(none yet)'}

Review the candidates and propose new research gaps or updates to existing gaps. Output the JSON array as specified."""


def run_specialist_agents(candidates: list[dict], known_gaps: dict,
                          config: dict, client: anthropic.Anthropic) -> dict[str, list]:
    lanes = config.get("lanes", {})
    model = config.get("models", {}).get("specialist", "claude-sonnet-4-6")
    max_tokens = config.get("models", {}).get("specialist_max_tokens", 4096)
    results = {}

    for lane, enabled in lanes.items():
        if not enabled:
            continue
        agent_file = {
            "secure_compute": "secure-compute.md",
            "cyber_capability": "cyber-capability.md",
            "recursive_improvement": "recursive-improvement.md",
            "multiagent_security": "multiagent-security.md",
            "adversarial_ml": "adversarial-ml.md",
            "ai_governance": "ai-governance.md",
        }.get(lane)
        if not agent_file or not (AGENTS_DIR / agent_file).exists():
            log.warning("No agent file for lane %s", lane)
            continue

        log.info("Running specialist agent: %s", lane)
        prompt = build_specialist_prompt(lane, candidates, known_gaps)
        response = call_agent(agent_file, prompt, model, max_tokens, client)

        proposals = extract_json_block(response)
        if isinstance(proposals, list):
            results[lane] = proposals
            log.info("  %s: %d proposals", lane, len(proposals))
        else:
            log.warning("  %s: could not parse proposals", lane)
            results[lane] = []

    return results


def run_analyst(specialist_proposals: dict, candidates: list[dict],
                known_gaps: dict, config: dict, client: anthropic.Anthropic) -> tuple[str, dict]:
    model = config.get("models", {}).get("analyst", "claude-opus-4-6")
    max_tokens = config.get("models", {}).get("analyst_max_tokens", 8192)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build known gaps summary (compact)
    gaps_summary = "\n".join(
        f"[{g.get('id', '?')}] {g.get('title', '')} | lanes: {g.get('lanes', [])} | status: {g.get('status', 'Stable')} | updated: {g.get('last_updated', '')}"
        for g in known_gaps.get("gaps", [])
    ) or "(No existing gaps yet — this is the first run.)"

    # Build specialist proposals summary
    proposals_text = ""
    for lane, proposals in specialist_proposals.items():
        proposals_text += f"\n### {lane} ({len(proposals)} proposals)\n"
        for p in proposals:
            proposals_text += f"- {p.get('title', 'untitled')}: {p.get('question', '')[:200]}\n"
            proposals_text += f"  Sources: {p.get('source_urls', [])}\n"

    # Candidate pool summary (top items by lane)
    pool_summary = f"{len(candidates)} candidates triaged this period across lanes: " + \
        ", ".join(f"{l}:{sum(1 for c in candidates if l in c.get('lanes',[]))}"
                  for l in config.get("lanes", {}) if config["lanes"][l])

    user_content = f"""DATE: {date_str}

KNOWN GAPS SUMMARY:
{gaps_summary}

SPECIALIST PROPOSALS:
{proposals_text}

CANDIDATE POOL SUMMARY:
{pool_summary}

Produce the digest and the JSON state update block as specified."""

    log.info("Running analyst agent (%s)...", model)
    response = call_agent("analyst.md", user_content, model, max_tokens, client)

    # Split digest text from JSON block
    digest_text = response
    state_updates = extract_json_block(response)
    if isinstance(state_updates, dict):
        # Remove the JSON block from the digest
        digest_text = re.sub(r"```json[\s\S]*?```", "", response).strip()

    return digest_text, state_updates or {}


# ---------------------------------------------------------------------------
# State update & document patching
# ---------------------------------------------------------------------------

def assign_gap_id(known_gaps: dict, lane: str) -> str:
    lane_map = {
        "secure_compute": "1",
        "cyber_capability": "2",
        "recursive_improvement": "3",
        "multiagent_security": "4",
        "adversarial_ml": "5",
        "ai_governance": "6",
        "intersection": "X",
    }
    section = lane_map.get(lane, "X")
    existing = [g for g in known_gaps.get("gaps", []) if g.get("id", "").startswith(f"Q-{section}.")]
    return f"Q-{section}.{len(existing) + 1}"


def apply_state_updates(known_gaps: dict, state_updates: dict, all_proposals: dict) -> dict:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Add new gaps from specialist proposals (validated by analyst)
    new_gap_ids = {g.get("id") for g in state_updates.get("new_gaps", [])}
    for lane, proposals in all_proposals.items():
        for p in proposals:
            if not isinstance(p, dict):
                continue
            title = p.get("title", "")
            if not title:
                continue
            # Check not already known
            if any(g.get("title") == title for g in known_gaps.get("gaps", [])):
                continue
            primary_lane = p.get("lanes", [lane])[0] if p.get("lanes") else lane
            new_id = assign_gap_id(known_gaps, primary_lane)
            known_gaps.setdefault("gaps", []).append({
                "id": new_id,
                "title": title,
                "question": p.get("question", ""),
                "why_hard": p.get("why_hard", ""),
                "whos_closest": p.get("whos_closest", ""),
                "methodological_approach": p.get("methodological_approach", ""),
                "source_urls": p.get("source_urls", []),
                "lanes": p.get("lanes", [lane]),
                "is_intersection": p.get("is_intersection", False),
                "first_added": date_str,
                "last_updated": date_str,
                "status": "Stable",
            })

    # Apply analyst updates
    for update in state_updates.get("updated_gaps", []):
        gap_id = update.get("id")
        for gap in known_gaps.get("gaps", []):
            if gap.get("id") == gap_id:
                if update.get("status"):
                    gap["status"] = update["status"]
                gap["last_updated"] = date_str
                if update.get("notes"):
                    gap.setdefault("notes", []).append(f"{date_str}: {update['notes']}")

    for grad_id in state_updates.get("graduated_gaps", []):
        for gap in known_gaps.get("gaps", []):
            if gap.get("id") == grad_id:
                gap["status"] = "Getting Answered"
                gap["last_updated"] = date_str

    return known_gaps


# ---------------------------------------------------------------------------
# Digest writing
# ---------------------------------------------------------------------------

def write_digest(digest_text: str) -> Path:
    DIGESTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_file = DIGESTS_DIR / f"{date_str}.md"
    out_file.write_text(digest_text, encoding="utf-8")
    (DIGESTS_DIR / "latest.md").write_text(digest_text, encoding="utf-8")
    log.info("Digest written: %s", out_file)
    return out_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Research Radar orchestrator")
    parser.add_argument("--collect-only", action="store_true", help="Collect and triage only")
    parser.add_argument("--analyze-only", action="store_true", help="Analyze from existing triaged pool")
    parser.add_argument("--force", action="store_true", help="Force analysis even if not due")
    parser.add_argument("--no-email", action="store_true", help="Skip email")
    parser.add_argument("--since-days", type=int, default=2, help="Days back for collector")
    args = parser.parse_args()

    load_env()
    config = load_config()
    (ROOT / "logs").mkdir(exist_ok=True)

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # --- Phase 1 & 2: Collect + Triage ---
    raw_file = None
    triaged_file = None

    if not args.analyze_only:
        log.info("=== Phase 1: Collection ===")
        sys.path.insert(0, str(ROOT))
        from collector import collect, triage
        raw_file = collect.run(since_days=args.since_days)

        log.info("=== Phase 2: Triage ===")
        triaged_file = triage.run(raw_file)

    if args.collect_only:
        log.info("--collect-only: stopping after triage.")
        return

    # --- Check if analysis is due ---
    threshold = config.get("cadence", {}).get("early_trigger_threshold", 30)
    should_analyze = (
        args.force or
        args.analyze_only or
        analysis_is_due(config) or
        count_new_candidates(config) >= threshold
    )

    if not should_analyze:
        log.info("Analysis not due yet. Run with --force to override.")
        return

    # --- Phase 3: Analysis ---
    log.info("=== Phase 3: Analysis ===")

    # Load all recent triaged candidates (since last analysis)
    dated_digests = sorted(d for d in DIGESTS_DIR.glob("*.md") if d.stem != "latest")
    if dated_digests:
        try:
            last_analysis = datetime.strptime(dated_digests[-1].stem, "%Y-%m-%d")
        except ValueError:
            last_analysis = datetime.now(timezone.utc) - timedelta(days=7)
    else:
        last_analysis = datetime.now(timezone.utc) - timedelta(days=7)

    candidates = []
    for f in sorted(CANDIDATES_DIR.glob("*-triaged.jsonl")):
        date_str = f.stem.replace("-triaged", "")
        try:
            fdate = datetime.strptime(date_str, "%Y-%m-%d")
            if fdate >= last_analysis:
                with open(f) as fp:
                    for line in fp:
                        line = line.strip()
                        if line:
                            candidates.append(json.loads(line))
        except ValueError:
            pass

    log.info("Loaded %d candidates for analysis", len(candidates))
    known_gaps = load_known_gaps()

    # Run specialists
    specialist_proposals = run_specialist_agents(candidates, known_gaps, config, client)

    # Run analyst
    digest_text, state_updates = run_analyst(
        specialist_proposals, candidates, known_gaps, config, client
    )

    # Apply state updates
    known_gaps = apply_state_updates(known_gaps, state_updates, specialist_proposals)
    save_known_gaps(known_gaps)
    log.info("State updated: %d total gaps", len(known_gaps.get("gaps", [])))

    # Write digest
    digest_file = write_digest(digest_text)

    # --- Phase 4: Email (optional) ---
    email_cfg = config.get("email", {})
    if email_cfg.get("enabled") and not args.no_email:
        log.info("=== Phase 4: Email ===")
        from scripts.send_email import send
        send(digest_file, config)
    else:
        log.info("Email skipped (disabled or --no-email). Digest at: %s", digest_file)

    log.info("=== Run complete ===")


if __name__ == "__main__":
    main()
