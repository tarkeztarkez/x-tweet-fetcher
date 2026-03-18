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
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

SEARXNG_URL = "http://localhost:8080/search"
MAC_BRIDGE = "http://localhost:17891"


def verify_freshness(finds, today_str=None):
    """
    Batch-verify freshness of search results via AI (Gemini → Grok → ask-ai fallback).
    One API call for all items. Returns finds with 'verified' and 'freshness_note' fields.
    """
    if not finds:
        return finds
    
    if not today_str:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Build batch prompt
    items_text = ""
    for i, f in enumerate(finds):
        items_text += f"{i+1}. Title: {f.get('title','')}\n   URL: {f.get('url','')}\n   Snippet: {f.get('snippet','')[:100]}\n\n"
    
    prompt = f"""Today is {today_str}. I have {len(finds)} search results that claim to be recent. 
For each one, determine if it is genuinely from the last 24-48 hours or if it's older content.

{items_text}

Reply ONLY with valid JSON array, no markdown. Format:
[{{"index": 1, "fresh": true, "reason": "mentions GPT-5.4 released today"}}, ...]

If unsure, mark fresh=true. Be strict only on obviously old content."""

    # Chain: local Gemini (VPS) → Mac bridge → ask-ai
    import subprocess
    ai_response = None
    
    # 1. Local Gemini first (VPS direct, fastest & most reliable)
    try:
        script_dir = Path(__file__).resolve().parent.parent.parent
        gemini_script = script_dir / "gemini-chat" / "scripts" / "gemini_chat.py"
        if not gemini_script.exists():
            gemini_script = Path("/root/clawd/skills/our/gemini-chat/scripts/gemini_chat.py")
        r = subprocess.run(
            ["python3", str(gemini_script), prompt[:3000], "--model", "flash"],
            capture_output=True, text=True, timeout=45
        )
        lines = r.stdout.strip().split("\n")
        ai_response = "\n".join(l for l in lines if not l.startswith("[gemini]")).strip()
        if ai_response:
            print("✅ Verified via local Gemini", file=sys.stderr)
    except Exception as e:
        print(f"Local Gemini failed: {e}", file=sys.stderr)
    
    # 2. Mac bridge fallback (gemini/grok)
    if not ai_response:
        endpoint_params = {"/gemini": "prompt", "/grok": "message"}
        for endpoint, param_key in endpoint_params.items():
            try:
                data = json.dumps({param_key: prompt}).encode()
                req = urllib.request.Request(
                    f"{MAC_BRIDGE}{endpoint}",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read())
                    ai_response = result.get("output", result.get("response", result.get("message", "")))
                    if ai_response:
                        print(f"✅ Verified via bridge{endpoint}", file=sys.stderr)
                        break
            except Exception as e:
                print(f"Bridge {endpoint} failed: {e}", file=sys.stderr)
    
    if not ai_response:
        print("⚠ Verification failed: no AI backend available", file=sys.stderr)
        for f in finds:
            f["verified"] = None
            f["freshness_note"] = "verification unavailable"
        return finds
    
    # Parse AI response
    try:
        # Extract JSON from response (may have markdown wrapping)
        text = ai_response
        if "```" in text:
            parts = text.split("```")
            if len(parts) > 1:
                text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        verdicts = json.loads(text)
        
        verdict_map = {}
        for v in verdicts:
            verdict_map[v.get("index", 0)] = v
        
        for i, f in enumerate(finds):
            v = verdict_map.get(i + 1, {})
            f["verified"] = v.get("fresh", None)
            f["freshness_note"] = v.get("reason", "no verdict")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"⚠ Could not parse AI verification: {e}", file=sys.stderr)
        for f in finds:
            f["verified"] = None
            f["freshness_note"] = f"parse error: {ai_response[:100]}"
    
    return finds


def search_searxng(query, max_results=10, fresh=False):
    """Search via local SearxNG instance (zero-cost, real-time)"""
    try:
        params = {
            "q": query,
            "format": "json",
            "categories": "general",
            "engines": "google,duckduckgo,brave,bing",
            "pageno": 1,
        }
        if fresh:
            params["time_range"] = "week"
        
        url = f"{SEARXNG_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        
        results = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "publishedDate": r.get("publishedDate", ""),
            })
        return results
    except Exception as e:
        print(f"SearxNG failed: {e}", file=sys.stderr)
        return []


def search_ddg(query, max_results=5):
    """Fallback: DuckDuckGo search"""
    try:
        from duckduckgo_search import DDGS
        import warnings
        warnings.filterwarnings("ignore")
        ddgs = DDGS()
        results = ddgs.text(query, max_results=max_results)
        if results:
            return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in results]
    except Exception:
        pass
    return []


def search_web(query, max_results=5, fresh=False):
    """Search with fallback chain: SearxNG → DuckDuckGo → Camofox"""
    # SearxNG first (local, fast, real-time)
    results = search_searxng(query, max_results=max_results, fresh=fresh)
    if results:
        return results
    
    # DuckDuckGo fallback
    results = search_ddg(query, max_results=max_results)
    if results:
        return results

    # Camofox fallback
    try:
        from camofox_client import camofox_search
        results = camofox_search(query)
        if results:
            return results[:max_results]
    except Exception:
        pass

    print(f"All search backends failed for: {query[:40]}...", file=sys.stderr)
    return []


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
