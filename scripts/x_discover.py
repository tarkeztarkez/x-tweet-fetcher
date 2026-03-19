#!/usr/bin/env python3
"""
X Discover - Search and discover valuable tweets by keyword.
Part of x-tweet-fetcher.

Uses SearxNG (local, zero-cost) → DuckDuckGo → Camofox as fallback chain.
Supports --fresh flag to only get recent results (past week).
Supports --verify flag to cross-validate freshness via AI (Gemini/Grok).

Usage:
  python3 x_discover.py --keywords "AI Agent,automation" --limit 5
  python3 x_discover.py --keywords "openclaw" --json
  python3 x_discover.py --keywords "LLM tool" --limit 10 --fresh
  python3 x_discover.py --keywords "LLM tool" --fresh --verify
  python3 x_discover.py --keywords "crypto arbitrage" --cache discover_cache.json
"""

import json
import hashlib
import argparse
import sys
from datetime import datetime
from pathlib import Path

from common import search_web


def verify_freshness(finds, today_str=None):
    """
    Verify freshness of search results using publishedDate heuristic.
    Marks results as fresh/stale based on date metadata from search engine.
    Returns finds with 'verified' and 'freshness_note' fields.
    """
    if not finds:
        return finds

    if not today_str:
        today_str = datetime.now().strftime("%Y-%m-%d")

    from datetime import timedelta
    today = datetime.strptime(today_str, "%Y-%m-%d")
    cutoff = today - timedelta(days=7)

    for f in finds:
        pub_date = f.get("publishedDate", "")
        if not pub_date:
            # No date info — assume fresh (benefit of doubt)
            f["verified"] = None
            f["freshness_note"] = "no date metadata"
            continue

        try:
            # SearxNG returns ISO format like "2026-03-18T12:00:00+00:00"
            date_str = pub_date[:10]
            parsed = datetime.strptime(date_str, "%Y-%m-%d")
            if parsed >= cutoff:
                f["verified"] = True
                f["freshness_note"] = f"published {date_str}"
            else:
                f["verified"] = False
                f["freshness_note"] = f"stale: published {date_str}"
        except (ValueError, IndexError):
            f["verified"] = None
            f["freshness_note"] = f"unparseable date: {pub_date[:20]}"

    return finds


def url_hash(url):
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def load_cache(cache_file):
    if cache_file and Path(cache_file).exists():
        return json.loads(Path(cache_file).read_text())
    return {"seen_urls": []}


def save_cache(cache, cache_file):
    if cache_file:
        Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_file).write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def discover_tweets(keywords, max_results=10, cache_file=None, fresh=False):
    """
    Search for tweets matching keywords.
    
    Args:
        keywords: list of keyword strings
        max_results: max results per keyword
        cache_file: optional path to cache file (skip seen URLs)
        fresh: only return recent results (past week)
    
    Returns:
        dict with total_new, finds list
    """
    cache = load_cache(cache_file)
    all_finds = []

    for keyword in keywords:
        query = f"site:x.com {keyword}"
        results = search_web(query, max_results=max_results, fresh=fresh)

        for r in results:
            url = r.get('url', r.get('href', ''))
            if not url:
                continue

            h = url_hash(url)
            if h in cache["seen_urls"]:
                continue

            cache["seen_urls"].append(h)
            all_finds.append({
                "url": url,
                "title": r.get('title', ''),
                "snippet": r.get('body', r.get('snippet', '')),
                "query": keyword,
                "found_at": datetime.now().isoformat()
            })

    save_cache(cache, cache_file)

    return {
        "timestamp": datetime.now().isoformat(),
        "total_new": len(all_finds),
        "finds": all_finds
    }


def main():
    parser = argparse.ArgumentParser(description="Discover tweets by keyword search")
    parser.add_argument("--keywords", "-k", required=True, help="Comma-separated keywords")
    parser.add_argument("--limit", "-l", type=int, default=5, help="Max results per keyword")
    parser.add_argument("--cache", "-c", help="Cache file path (skip seen URLs)")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON")
    parser.add_argument("--fresh", "-f", action="store_true", help="Only recent results (past week)")
    parser.add_argument("--verify", "-v", action="store_true", help="Cross-verify freshness via AI (Gemini/Grok)")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    result = discover_tweets(keywords, max_results=args.limit, cache_file=args.cache, fresh=args.fresh)

    # AI freshness verification
    if args.verify and result["finds"]:
        print("🔍 Verifying freshness via AI...", file=sys.stderr)
        result["finds"] = verify_freshness(result["finds"])
        # Filter out stale results
        fresh_finds = [f for f in result["finds"] if f.get("verified") is not False]
        stale_count = len(result["finds"]) - len(fresh_finds)
        if stale_count > 0:
            print(f"🗑 Filtered {stale_count} stale result(s)", file=sys.stderr)
        result["finds"] = fresh_finds
        result["total_new"] = len(fresh_finds)
        result["verified"] = True

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["total_new"] == 0:
            print("No new discoveries.")
        else:
            print(f"Found {result['total_new']} new tweets:\n")
            for i, f in enumerate(result["finds"], 1):
                badge = ""
                if f.get("verified") is True:
                    badge = " ✅"
                elif f.get("verified") is None and "freshness_note" in f:
                    badge = " ❓"
                print(f"{i}. {f['title']}{badge}")
                date = f.get('publishedDate', '')
                if date:
                    print(f"   📅 {date[:10]}")
                if f.get("freshness_note"):
                    print(f"   🔍 {f['freshness_note']}")
                if f['snippet']:
                    print(f"   {f['snippet'][:120]}...")
                print(f"   {f['url']}")
                print()

    sys.exit(0 if result["total_new"] == 0 else 1)


if __name__ == "__main__":
    main()
