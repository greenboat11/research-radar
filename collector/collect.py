"""
collector/collect.py — Tier-1 source harvester.

No LLM calls. Reads sources.yaml, fetches new items since last run,
normalizes to a common schema, deduplicates against state/seen_urls.json,
writes state/candidates/<date>-raw.jsonl, and updates seen_urls.json.
"""

import hashlib
import json
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "state"
CANDIDATES_DIR = STATE_DIR / "candidates"
SEEN_URLS_FILE = STATE_DIR / "seen_urls.json"
SOURCES_FILE = ROOT / "sources.yaml"
CONFIG_FILE = ROOT / "config.yaml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def url_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def load_seen_urls() -> dict:
    if SEEN_URLS_FILE.exists():
        return json.loads(SEEN_URLS_FILE.read_text())
    return {}


def save_seen_urls(seen: dict, ttl_days: int = 90) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_URLS_FILE.write_text(json.dumps(pruned, indent=2))
    log.info("seen_urls: %d entries (pruned to %d days)", len(pruned), ttl_days)


def make_record(source: str, url: str, title: str, authors: list,
                published: str, abstract: str, raw_tags: list) -> dict:
    return {
        "id": url_id(url),
        "source": source,
        "url": url,
        "title": title.strip(),
        "authors": authors,
        "published": published,
        "abstract": (abstract or "").strip()[:2000],
        "raw_tags": raw_tags,
    }


def parse_date(date_str: str) -> str:
    """Normalize various date formats to YYYY-MM-DD."""
    if not date_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str[:25], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str[:10]


def since_days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

def fetch_arxiv(cfg: dict, since: datetime) -> list[dict]:
    records = []
    base = cfg.get("base_url", "http://export.arxiv.org/api/query")
    max_r = cfg.get("max_results_per_query", 40)
    cats = " OR ".join(f"cat:{c}" for c in cfg.get("categories", ["cs.CR", "cs.AI"]))

    for lane, queries in cfg.get("queries", {}).items():
        for q in queries:
            params = {
                "search_query": f"({cats}) AND all:{urllib.parse.quote(q)}",
                "start": 0,
                "max_results": max_r,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            try:
                r = requests.get(base, params=params, timeout=20)
                r.raise_for_status()
                root = ET.fromstring(r.text)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns):
                    pub = entry.findtext("atom:published", "", ns)
                    if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) < since:
                        continue
                    url = entry.findtext("atom:id", "", ns).strip()
                    title = entry.findtext("atom:title", "", ns)
                    abstract = entry.findtext("atom:summary", "", ns)
                    authors = [a.findtext("atom:name", "", ns)
                               for a in entry.findall("atom:author", ns)]
                    categories = [c.get("term", "") for c in entry.findall("atom:category", ns)]
                    records.append(make_record(
                        source="arxiv", url=url, title=title,
                        authors=authors, published=parse_date(pub),
                        abstract=abstract, raw_tags=categories + [lane],
                    ))
                time.sleep(3)  # arXiv rate limit: 1 req/3s
            except Exception as e:
                log.warning("arXiv query failed (%s): %s", q, e)

    return records


def fetch_feeds(cfg: dict, since: datetime) -> list[dict]:
    records = []
    for feed in cfg.get("items", []):
        url = feed.get("url", "")
        lanes = feed.get("lanes", [])
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries:
                pub = entry.get("published", entry.get("updated", ""))
                pub_dt = None
                if pub:
                    try:
                        pub_dt = datetime(*entry.get(
                            "published_parsed", entry.get("updated_parsed",
                            time.gmtime()[:6]))[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass
                if pub_dt and pub_dt < since:
                    continue
                link = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                if not link or not title:
                    continue
                records.append(make_record(
                    source=f"feed:{feed.get('name', url)}",
                    url=link, title=title, authors=[],
                    published=parse_date(pub),
                    abstract=summary, raw_tags=lanes,
                ))
        except Exception as e:
            log.warning("Feed failed (%s): %s", feed.get("name", url), e)
    return records


def fetch_hackernews(cfg: dict, since: datetime) -> list[dict]:
    records = []
    base = cfg.get("base_url", "https://hn.algolia.com/api/v1/search")
    min_pts = cfg.get("min_points", 10)
    cutoff_ts = int(since.timestamp())

    for kw in cfg.get("keywords", []):
        try:
            params = {
                "query": kw,
                "tags": "story",
                "numericFilters": f"created_at_i>{cutoff_ts},points>={min_pts}",
                "hitsPerPage": 30,
            }
            r = requests.get(base, params=params, timeout=15)
            r.raise_for_status()
            for hit in r.json().get("hits", []):
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                title = hit.get("title", "")
                ts = hit.get("created_at", "")
                if not title:
                    continue
                records.append(make_record(
                    source="hackernews", url=url, title=title,
                    authors=[hit.get("author", "")],
                    published=parse_date(ts),
                    abstract=hit.get("story_text", "")[:500],
                    raw_tags=["hackernews"],
                ))
            time.sleep(1)
        except Exception as e:
            log.warning("HN query failed (%s): %s", kw, e)
    return records


def fetch_lesswrong(cfg: dict, since: datetime) -> list[dict]:
    records = []
    gql_url = cfg.get("graphql_url", "https://www.lesswrong.com/graphql")
    min_karma = cfg.get("min_karma", 25)
    cutoff_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = """
    query RecentPosts($after: String!, $minKarma: Int!) {
      posts(input: {
        terms: {
          after: $after,
          karmaThreshold: $minKarma,
          limit: 50
        }
      }) {
        results {
          title
          pageUrl
          postedAt
          baseScore
          excerpt
          tags { name }
          user { displayName }
        }
      }
    }
    """
    try:
        r = requests.post(
            gql_url,
            json={"query": query, "variables": {"after": cutoff_str, "minKarma": min_karma}},
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        posts = r.json().get("data", {}).get("posts", {}).get("results", [])
        for post in posts:
            url = post.get("pageUrl", "")
            title = post.get("title", "")
            if not url or not title:
                continue
            tags = [t.get("name", "") for t in post.get("tags", [])]
            records.append(make_record(
                source="lesswrong",
                url=url, title=title,
                authors=[post.get("user", {}).get("displayName", "")],
                published=parse_date(post.get("postedAt", "")),
                abstract=post.get("excerpt", ""),
                raw_tags=tags,
            ))
    except Exception as e:
        log.warning("LessWrong fetch failed: %s", e)
    return records


def fetch_reddit(cfg: dict, since: datetime) -> list[dict]:
    records = []
    ua = cfg.get("user_agent", "research-radar/1.0")
    min_score = cfg.get("min_score", 10)
    headers = {"User-Agent": ua}

    for sub in cfg.get("subreddits", []):
        sub = sub.lstrip("r/")
        try:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit=50"
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            for post in r.json().get("data", {}).get("children", []):
                d = post.get("data", {})
                if d.get("score", 0) < min_score:
                    continue
                created = datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc)
                if created < since:
                    continue
                link = d.get("url", "")
                permalink = f"https://www.reddit.com{d.get('permalink', '')}"
                if not link:
                    link = permalink
                records.append(make_record(
                    source=f"reddit:r/{sub}",
                    url=link, title=d.get("title", ""),
                    authors=[d.get("author", "")],
                    published=created.strftime("%Y-%m-%d"),
                    abstract=d.get("selftext", "")[:500],
                    raw_tags=[sub],
                ))
            time.sleep(2)  # Reddit rate limit
        except Exception as e:
            log.warning("Reddit fetch failed (r/%s): %s", sub, e)
    return records


def fetch_openalex(cfg: dict, since: datetime) -> list[dict]:
    records = []
    base = cfg.get("base_url", "https://api.openalex.org/works")
    max_r = cfg.get("max_results", 30)
    cutoff = since.strftime("%Y-%m-%d")

    for seed in cfg.get("seed_papers", []):
        try:
            # Resolve seed to OpenAlex ID
            r = requests.get(f"{base}?filter=doi:{urllib.parse.quote(seed)}", timeout=15)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                continue
            work_id = results[0]["id"].split("/")[-1]

            # Fetch citing works
            cite_url = (f"{base}?filter=cites:{work_id},"
                        f"from_publication_date:{cutoff}&per-page={max_r}&sort=cited_by_count:desc")
            r2 = requests.get(cite_url, timeout=15)
            r2.raise_for_status()
            for work in r2.json().get("results", []):
                url = work.get("doi") or work.get("id", "")
                if url.startswith("https://doi.org/"):
                    pass
                else:
                    url = f"https://openalex.org/{work.get('id', '').split('/')[-1]}"
                title = work.get("title", "")
                if not url or not title:
                    continue
                authors = [a.get("author", {}).get("display_name", "")
                           for a in work.get("authorships", [])[:5]]
                abstract = ""
                if work.get("abstract_inverted_index"):
                    # Reconstruct abstract from inverted index
                    idx = work["abstract_inverted_index"]
                    words = [""] * (max(pos for positions in idx.values() for pos in positions) + 1)
                    for word, positions in idx.items():
                        for pos in positions:
                            words[pos] = word
                    abstract = " ".join(words)
                records.append(make_record(
                    source="openalex",
                    url=url, title=title,
                    authors=authors,
                    published=work.get("publication_date", cutoff),
                    abstract=abstract[:1000],
                    raw_tags=["openalex", f"cites:{seed[:40]}"],
                ))
            time.sleep(1)
        except Exception as e:
            log.warning("OpenAlex fetch failed (seed: %s): %s", seed[:40], e)
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(since_days: int = 2) -> Path:
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    sources = load_yaml(SOURCES_FILE)
    config = load_yaml(CONFIG_FILE)
    ttl_days = config.get("state", {}).get("seen_url_ttl_days", 90)

    seen = load_seen_urls()
    since = since_days_ago(since_days)
    all_records = []

    fetchers = [
        ("arxiv",       fetch_arxiv),
        ("feeds",       fetch_feeds),
        ("hackernews",  fetch_hackernews),
        ("lesswrong",   fetch_lesswrong),
        ("reddit",      fetch_reddit),
        ("openalex",    fetch_openalex),
    ]

    for key, fn in fetchers:
        cfg = sources.get(key, {})
        if not cfg.get("enabled", False):
            log.info("Skipping %s (disabled)", key)
            continue
        log.info("Fetching %s...", key)
        try:
            records = fn(cfg, since)
            log.info("  %s: %d items fetched", key, len(records))
            all_records.extend(records)
        except Exception as e:
            log.error("Source %s failed entirely: %s", key, e)

    # Deduplicate
    new_records = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for rec in all_records:
        uid = rec["id"]
        if uid not in seen:
            new_records.append(rec)
            seen[uid] = now_iso

    log.info("Dedup: %d new out of %d total fetched", len(new_records), len(all_records))

    # Write output
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_file = CANDIDATES_DIR / f"{date_str}-raw.jsonl"
    with open(out_file, "w") as f:
        for rec in new_records:
            f.write(json.dumps(rec) + "\n")

    save_seen_urls(seen, ttl_days)
    log.info("Wrote %d candidates to %s", len(new_records), out_file)
    return out_file


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-days", type=int, default=2)
    args = parser.parse_args()
    run(since_days=args.since_days)
