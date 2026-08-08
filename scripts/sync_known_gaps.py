"""
scripts/sync_known_gaps.py — Rebuild state/known_gaps.json from RESEARCH_FRONTIERS.md.

Run this once when seeding the living document, or to resync if the JSON
gets out of date. Parses the [Q-X.Y] entry format.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTIERS_FILE = ROOT / "RESEARCH_FRONTIERS.md"
KNOWN_GAPS_FILE = ROOT / "state" / "known_gaps.json"

ENTRY_PATTERN = re.compile(
    r"###\s+\[(?P<id>Q-[\w.]+)\]\s+(?P<title>[^\n]+)\n"
    r".*?\*\*Question:\*\*\s*(?P<question>[^\n]*(?:\n(?!\*\*)[^\n]*)*)"
    r".*?\*\*Why Hard:\*\*\s*(?P<why_hard>[^\n]*(?:\n(?!\*\*)[^\n]*)*)"
    r".*?\*\*Who's Closest:\*\*\s*(?P<whos_closest>[^\n]*(?:\n(?!\*\*)[^\n]*)*)"
    r".*?\*\*First Added:\*\*\s*(?P<first_added>[^\n]*)"
    r".*?\*\*Last Updated:\*\*\s*(?P<last_updated>[^\n]*)"
    r".*?\*\*Status:\*\*\s*(?P<status>[^\n]*)",
    re.DOTALL,
)


def parse_frontiers(text: str) -> list[dict]:
    gaps = []
    for m in ENTRY_PATTERN.finditer(text):
        gap_id = m.group("id").strip()
        lane_section = int(gap_id.split(".")[0].replace("Q-", "").replace("X", "99"))
        lane_map = {
            1: ["secure_compute"], 2: ["cyber_capability"],
            3: ["recursive_improvement"], 4: ["multiagent_security"],
            5: ["adversarial_ml"], 6: ["ai_governance"],
            99: ["intersection"],
        }
        gaps.append({
            "id": gap_id,
            "title": m.group("title").strip(),
            "question": m.group("question").strip()[:500],
            "why_hard": m.group("why_hard").strip()[:300],
            "whos_closest": m.group("whos_closest").strip()[:300],
            "methodological_approach": "",
            "source_urls": [],
            "lanes": lane_map.get(lane_section, ["intersection"]),
            "is_intersection": lane_section == 99,
            "first_added": m.group("first_added").strip(),
            "last_updated": m.group("last_updated").strip(),
            "status": m.group("status").strip().replace("🔥 ", "").replace("🔥", ""),
        })
    return gaps


def main():
    if not FRONTIERS_FILE.exists():
        print(f"Not found: {FRONTIERS_FILE}")
        return

    text = FRONTIERS_FILE.read_text(encoding="utf-8")
    gaps = parse_frontiers(text)

    known = {"gaps": gaps, "last_updated": ""}
    KNOWN_GAPS_FILE.write_text(json.dumps(known, indent=2))
    print(f"Synced {len(gaps)} gaps to {KNOWN_GAPS_FILE}")


if __name__ == "__main__":
    main()
