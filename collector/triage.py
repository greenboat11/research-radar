"""
collector/triage.py — Tag each candidate to lane(s) or discard.

Uses the cheapest model (Haiku by default). Processes in batches to reduce
API calls. Writes state/candidates/<date>-triaged.jsonl.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_FILE = ROOT / "config.yaml"
CANDIDATES_DIR = ROOT / "state" / "candidates"

log = logging.getLogger(__name__)

LANES = [
    "secure_compute",
    "cyber_capability",
    "recursive_improvement",
    "multiagent_security",
    "adversarial_ml",
    "ai_governance",
    "discard",
]

TRIAGE_SYSTEM = """You are a research triage classifier for an AI-security research gap finder.

Your job: read each candidate item (title + abstract) and assign it to one or more research lanes.
Be strict — only assign a lane if the item has substantive relevance to that lane's core questions.
Items with no clear relevance should be tagged `discard`.

Lanes:
- secure_compute: model weight protection, TEEs, HSMs, confidential computing, datacenter security, supply chain integrity, compute governance
- cyber_capability: autonomous offensive capability benchmarks, CTF evals, vulnerability discovery, moving target defense, AI honeypots/deception, AI-attack detection
- recursive_improvement: recursive self-improvement, self-evolving agents, automated ML research, self-replication, ARA, capability-gain measurement, containment
- multiagent_security: compositional attack surfaces, privilege escalation in agent systems, trust graphs, emergent collusion, multi-agent risk
- adversarial_ml: jailbreaks, prompt injection, adversarial robustness, model extraction
- ai_governance: safety evaluation methodology, red-team frameworks, policy, standards, third-party audit
- discard: marketing, hype without research substance, entirely off-topic

Output: a JSON array, one object per input item, in the same order. Each object:
{
  "id": "<same id as input>",
  "lanes": ["lane1", "lane2"],   // 1-3 lanes, or ["discard"]
  "why_relevant": "one sentence"
}

Return ONLY the JSON array, no explanation.
"""

BATCH_SIZE = 20


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def triage_batch(client: anthropic.Anthropic, batch: list[dict], model: str) -> list[dict]:
    items_text = "\n\n".join(
        f"[{i+1}] id={item['id']}\nTitle: {item['title']}\nAbstract: {item['abstract'][:400]}"
        for i, item in enumerate(batch)
    )

    for attempt in range(5):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                system=TRIAGE_SYSTEM,
                messages=[{"role": "user", "content": items_text}],
            )
            text = resp.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            results = json.loads(text)
            return results
        except json.JSONDecodeError as e:
            log.warning("Triage JSON parse failed (attempt %d): %s", attempt + 1, e)
            time.sleep(5)
        except anthropic.RateLimitError:
            log.warning("Rate limit hit, waiting 65s (attempt %d)", attempt + 1)
            time.sleep(65)
        except Exception as e:
            log.warning("Triage API error (attempt %d): %s", attempt + 1, e)
            time.sleep(10)

    # Fallback: mark all as discard
    return [{"id": item["id"], "lanes": ["discard"], "why_relevant": "triage failed"} for item in batch]


def run(raw_file: Path) -> Path:
    config = load_config()
    model = config.get("models", {}).get("triage", "claude-haiku-4-5-20251001")
    enabled_lanes = {k for k, v in config.get("lanes", {}).items() if v}

    # Load candidates
    candidates = []
    with open(raw_file) as f:
        for line in f:
            line = line.strip()
            if line:
                candidates.append(json.loads(line))

    log.info("Triaging %d candidates with %s...", len(candidates), model)
    client = anthropic.Anthropic()

    # Process in batches
    triage_map: dict[str, dict] = {}
    for i in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[i:i + BATCH_SIZE]
        results = triage_batch(client, batch, model)
        for r in results:
            triage_map[r["id"]] = r
        log.info("  Triaged %d/%d", min(i + BATCH_SIZE, len(candidates)), len(candidates))
        time.sleep(1)

    # Merge triage results back into candidates; filter to enabled lanes
    triaged = []
    discarded = 0
    for cand in candidates:
        tag = triage_map.get(cand["id"], {"lanes": ["discard"], "why_relevant": ""})
        lanes = [l for l in tag.get("lanes", ["discard"]) if l in enabled_lanes or l == "discard"]
        if not lanes or lanes == ["discard"]:
            discarded += 1
            continue
        cand["lanes"] = lanes
        cand["why_relevant"] = tag.get("why_relevant", "")
        triaged.append(cand)

    log.info("Triage complete: %d kept, %d discarded", len(triaged), discarded)

    # Write output
    date_str = raw_file.stem.replace("-raw", "")
    out_file = CANDIDATES_DIR / f"{date_str}-triaged.jsonl"
    with open(out_file, "w") as f:
        for item in triaged:
            f.write(json.dumps(item) + "\n")

    log.info("Wrote %d triaged candidates to %s", len(triaged), out_file)
    return out_file


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_file", help="Path to -raw.jsonl file")
    args = parser.parse_args()
    run(Path(args.raw_file))
