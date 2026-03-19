#!/usr/bin/env python3
"""
paper_recommend.py — 论文推荐工具

从 X 推文 / GitHub 仓库 / ArXiv ID / 论文标题 出发，提取论文信息，
通过 Semantic Scholar API 查找相关论文（cited-by、references、同作者），
反向查找作者 X/Twitter 账号。

Usage:
  python3 paper_recommend.py --tweet https://x.com/user/status/123456
  python3 paper_recommend.py --github https://github.com/org/repo
  python3 paper_recommend.py --arxiv 2603.10165
  python3 paper_recommend.py --title "Memory Sparse Attention"
  python3 paper_recommend.py --arxiv 1706.03762 --top 3 --skip-twitter
  python3 paper_recommend.py --arxiv 1706.03762 --zh

Zero pip dependencies — stdlib only (urllib/json/re + subprocess).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET


# ─── Config ───────────────────────────────────────────────────────────────────

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
ARXIV_API = "https://export.arxiv.org/api/query?id_list={arxiv_id}"
REQUEST_DELAY = 1.0  # Semantic Scholar free tier: be polite

# Optional API keys (set via environment variables for higher rate limits)
S2_API_KEY = os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

ARXIV_ID_RE = re.compile(r'(\d{4}\.\d{4,5}(?:v\d+)?)')
ARXIV_URL_RE = re.compile(r'arxiv\.org/(?:abs|pdf|html)/([^\s?#]+?)(?:\.pdf)?(?:[?#]|$)')
GITHUB_REPO_RE = re.compile(r'https?://github\.com/([A-Za-z0-9_\-\.]+)/([A-Za-z0-9_\-\.]+)')
TWITTER_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,50})(?:[/?#]|$)'
)

# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _get(url: str, headers: dict | None = None, timeout: int = 20) -> dict | str | None:
    """GET request, returns parsed JSON or raw string."""
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", "paper-recommend/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except Exception:
                return raw
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"[WARN] Rate limited (429), will retry with backoff", file=sys.stderr)
            return None  # Signal to caller to retry
        if e.code != 404:
            print(f"[WARN] HTTP {e.code} — {url[:80]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[WARN] Request failed: {e}", file=sys.stderr)
        return None


def _s2_get_with_backoff(path: str, params: str = "", retries: int = 3) -> dict | None:
    """
    Semantic Scholar API GET with exponential backoff on 429.
    Retries up to `retries` times with delays: 1s → 2s → 4s
    """
    url = f"{SEMANTIC_SCHOLAR_API}{path}"
    if params:
        url += f"?{params}"

    headers = {}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    for attempt in range(retries + 1):
        time.sleep(REQUEST_DELAY)
        result = _get(url, headers=headers if headers else None)

        if result is not None:
            return result if isinstance(result, dict) else None

        if attempt < retries:
            delay = 2 ** attempt  # 1, 2, 4 seconds
            print(f"[WARN] S2 request failed (attempt {attempt + 1}/{retries + 1}), "
                  f"retrying in {delay}s...", file=sys.stderr)
            time.sleep(delay)
        else:
            print(f"[ERROR] S2 request failed after {retries + 1} attempts", file=sys.stderr)

    return None


def _s2_get(path: str, params: str = "") -> dict | None:
    """Semantic Scholar API GET — uses backoff helper."""
    return _s2_get_with_backoff(path, params)


# ─── ArXiv helpers ────────────────────────────────────────────────────────────

def parse_arxiv_id(text: str) -> str | None:
    """Extract arxiv ID from URL or raw text."""
    text = text.strip().rstrip("/")
    m = ARXIV_URL_RE.search(text)
    if m:
        return re.sub(r'v\d+$', '', m.group(1)) if re.search(r'v\d+$', m.group(1)) else m.group(1)
    m = ARXIV_ID_RE.search(text)
    if m:
        return m.group(1)
    return None


def fetch_arxiv_metadata(arxiv_id: str) -> dict | None:
    """Fetch paper metadata from ArXiv API."""
    clean_id = re.sub(r'v\d+$', '', arxiv_id)
    url = ARXIV_API.format(arxiv_id=urllib.parse.quote(clean_id))
    raw = _get(url, timeout=20)
    if not isinstance(raw, str):
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(raw)
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
    authors = [a.findtext("atom:name", "", ns).strip() for a in entry.findall("atom:author", ns)]
    abstract = (entry.findtext("atom:summary", "", ns) or "").strip()

    # Extract GitHub URLs
    combined = abstract
    comment_el = entry.find("arxiv:comment", ns)
    if comment_el is not None and comment_el.text:
        combined += " " + comment_el.text
    for link in entry.findall("atom:link", ns):
        combined += " " + link.get("href", "")
    github_urls = list(dict.fromkeys(
        m.group(0).rstrip(".,;)'\"") for m in GITHUB_REPO_RE.finditer(combined)
    ))

    return {
        "arxiv_id": clean_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "github_urls": github_urls,
    }


# ─── Input extraction ────────────────────────────────────────────────────────

def extract_from_tweet(tweet_url: str) -> dict | None:
    """
    Extract paper info from a tweet URL.
    Uses local fetch_tweet.py via subprocess (preferred),
    falls back to nitter / mac-bridge if local fetch fails.
    """
    print(f"[INFO] Fetching tweet: {tweet_url}", file=sys.stderr)

    tweet_id_m = re.search(r'/status/(\d+)', tweet_url)
    if not tweet_id_m:
        print("[ERROR] Cannot extract tweet ID from URL", file=sys.stderr)
        return None

    tweet_id = tweet_id_m.group(1)
    text = ""

    # ── Method 1: Local fetch_tweet.py (preferred) ────────────────────────
    fetch_script = os.path.join(os.path.dirname(__file__), "fetch_tweet.py")
    if os.path.exists(fetch_script):
        try:
            result = subprocess.run(
                ['python3', fetch_script, '--url', tweet_url],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                tweet_data = json.loads(result.stdout)
                text = tweet_data.get('tweet', {}).get('text', '')
                if text:
                    print(f"[INFO] Got tweet text via local fetch_tweet.py ({len(text)} chars)", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Local fetch_tweet.py failed ({e}), trying fallbacks...", file=sys.stderr)
    else:
        print(f"[WARN] fetch_tweet.py not found at {fetch_script}, trying fallbacks...", file=sys.stderr)

    # ── Method 2: Nitter (public instances) ──────────────────────────────
    if not text:
        nitter_urls = [
            f"https://nitter.privacydev.net/{tweet_id}",
            f"https://nitter.poast.org/{tweet_id}",
            f"https://nitter.1d4.us/{tweet_id}",
        ]
        for nitter_url in nitter_urls:
            try:
                data = _get(nitter_url, timeout=10)
                if isinstance(data, str) and len(data) > 100:
                    # Extract text from nitter HTML
                    m = re.search(r'class="tweet-content"[^>]*>([^<]+)', data)
                    if m:
                        text = m.group(1).strip()
                    if text:
                        print(f"[INFO] Got tweet text via nitter", file=sys.stderr)
                        break
            except Exception:
                pass

    # ── Method 3: mac-bridge fallback ────────────────────────────────────
    if not text:
        try:
            bridge_url = f"http://localhost:17899/read?url={urllib.parse.quote(tweet_url)}&screens=1"
            bridge_data = _get(bridge_url, timeout=30)
            if isinstance(bridge_data, dict):
                text = bridge_data.get("text", "") or bridge_data.get("content", "")
            elif isinstance(bridge_data, str):
                text = bridge_data
        except Exception:
            pass

    if not text:
        print("[WARN] Could not fetch tweet content, trying ArXiv ID from URL only", file=sys.stderr)
        text = tweet_url

    # Look for arxiv ID in tweet text
    arxiv_id = parse_arxiv_id(text)
    if arxiv_id:
        return fetch_arxiv_metadata(arxiv_id)

    # Look for GitHub URL in tweet
    gh_match = GITHUB_REPO_RE.search(text)
    if gh_match:
        return extract_from_github(gh_match.group(0))

    # Try to extract a paper title from the text (first line or quoted text)
    lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 20]
    if lines:
        # Search Semantic Scholar by title
        return search_paper_by_title(lines[0][:200])

    print("[ERROR] Could not find paper info in tweet", file=sys.stderr)
    return None


def extract_from_github(github_url: str) -> dict | None:
    """Extract paper info from a GitHub repo URL."""
    print(f"[INFO] Fetching GitHub repo: {github_url}", file=sys.stderr)
    m = GITHUB_REPO_RE.match(github_url.rstrip("/"))
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)

    # Check README for arxiv link
    for branch in ["main", "master"]:
        readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        readme = _get(readme_url, timeout=15)
        if isinstance(readme, str) and len(readme) > 50:
            arxiv_id = parse_arxiv_id(readme)
            if arxiv_id:
                info = fetch_arxiv_metadata(arxiv_id)
                if info:
                    if github_url not in info.get("github_urls", []):
                        info.setdefault("github_urls", []).append(github_url)
                    return info
            break

    # Check repo description via GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json"}
    gh_token = os.environ.get("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    repo_data = _get(api_url, headers=headers, timeout=15)
    if isinstance(repo_data, dict):
        desc = repo_data.get("description", "") or ""
        arxiv_id = parse_arxiv_id(desc)
        if arxiv_id:
            info = fetch_arxiv_metadata(arxiv_id)
            if info:
                if github_url not in info.get("github_urls", []):
                    info.setdefault("github_urls", []).append(github_url)
                return info

        # Use repo name/description as paper title
        name = repo_data.get("name", repo).replace("-", " ").replace("_", " ")
        title = desc if desc and len(desc) > 15 else name
        result = search_paper_by_title(title)
        if result:
            if github_url not in result.get("github_urls", []):
                result.setdefault("github_urls", []).append(github_url)
            return result

    return None


def search_paper_by_title(title: str) -> dict | None:
    """Search Semantic Scholar by title to find the paper."""
    print(f"[INFO] Searching S2 for: {title[:60]}...", file=sys.stderr)
    query = urllib.parse.quote(title[:200])
    data = _s2_get("/paper/search", f"query={query}&limit=1&fields=externalIds,title,authors")
    if not data or not data.get("data"):
        return None
    paper = data["data"][0]
    ext = paper.get("externalIds", {})
    arxiv_id = ext.get("ArXiv")
    if arxiv_id:
        return fetch_arxiv_metadata(arxiv_id)
    # Return what we have from S2
    authors = [a.get("name", "") for a in paper.get("authors", [])]
    return {
        "arxiv_id": arxiv_id,
        "title": paper.get("title", title),
        "authors": authors,
        "abstract": "",
        "github_urls": [],
        "s2_paper_id": paper.get("paperId"),
    }


# ─── Semantic Scholar recommendation engine ──────────────────────────────────

def get_s2_paper(arxiv_id: str = None, title: str = None, s2_id: str = None) -> dict | None:
    """Get Semantic Scholar paper data."""
    fields = "paperId,externalIds,title,authors,year,citationCount,influentialCitationCount,abstract,url"

    if s2_id:
        return _s2_get(f"/paper/{s2_id}", f"fields={fields}")
    if arxiv_id:
        return _s2_get(f"/paper/ArXiv:{arxiv_id}", f"fields={fields}")
    if title:
        query = urllib.parse.quote(title[:200])
        data = _s2_get("/paper/search", f"query={query}&limit=1&fields={fields}")
        if data and data.get("data"):
            return data["data"][0]
    return None


def get_citations(paper_id: str, limit: int = 50) -> list[dict]:
    """Get papers that cite this paper (cited-by)."""
    fields = "paperId,externalIds,title,authors,year,citationCount,abstract,url"
    data = _s2_get(f"/paper/{paper_id}/citations", f"fields={fields}&limit={limit}")
    if not data:
        return []
    return [c.get("citingPaper", {}) for c in data.get("data", []) if c.get("citingPaper")]


def get_references(paper_id: str, limit: int = 50) -> list[dict]:
    """Get papers referenced by this paper."""
    fields = "paperId,externalIds,title,authors,year,citationCount,abstract,url"
    data = _s2_get(f"/paper/{paper_id}/references", f"fields={fields}&limit={limit}")
    if not data:
        return []
    return [r.get("citedPaper", {}) for r in data.get("data", []) if r.get("citedPaper")]


def get_author_papers(author_id: str, limit: int = 20) -> list[dict]:
    """Get recent papers by an author."""
    fields = "paperId,externalIds,title,authors,year,citationCount,abstract,url"
    data = _s2_get(f"/author/{author_id}/papers", f"fields={fields}&limit={limit}")
    if not data:
        return []
    return data.get("data", [])


def rank_and_dedupe(papers: list[dict], source_paper_id: str = None) -> list[dict]:
    """Rank papers by citation count, deduplicate, exclude source paper."""
    seen = set()
    unique = []
    for p in papers:
        pid = p.get("paperId")
        if not pid or pid == source_paper_id or pid in seen:
            continue
        title = p.get("title", "")
        if not title or len(title) < 5:
            continue
        seen.add(pid)
        unique.append(p)

    # Sort by citation count (descending)
    unique.sort(key=lambda x: x.get("citationCount", 0) or 0, reverse=True)
    return unique


def find_related_papers(paper_info: dict, top_n: int = 5) -> list[dict]:
    """
    Find top-N related papers using Semantic Scholar.
    Strategy: citations + references + same-author papers, then rank.
    """
    arxiv_id = paper_info.get("arxiv_id")
    s2_id = paper_info.get("s2_paper_id")
    title = paper_info.get("title", "")

    # Get the source paper on S2
    print("[INFO] Looking up paper on Semantic Scholar...", file=sys.stderr)
    s2_paper = get_s2_paper(arxiv_id=arxiv_id, title=title, s2_id=s2_id)
    if not s2_paper:
        print("[WARN] Paper not found on Semantic Scholar", file=sys.stderr)
        return []

    paper_id = s2_paper["paperId"]
    print(f"[INFO] S2 paper: {s2_paper.get('title', '')[:60]}", file=sys.stderr)
    print(f"[INFO] Citations: {s2_paper.get('citationCount', 0)}", file=sys.stderr)

    all_candidates = []

    # 1. Citing papers (who cited this?)
    print("[INFO] Fetching citations...", file=sys.stderr)
    citations = get_citations(paper_id, limit=30)
    for p in citations:
        p["_source"] = "cited_by"
    all_candidates.extend(citations)

    # 2. References (what does this paper cite?)
    print("[INFO] Fetching references...", file=sys.stderr)
    references = get_references(paper_id, limit=30)
    for p in references:
        p["_source"] = "reference"
    all_candidates.extend(references)

    # 3. Same-author papers (first 2 authors)
    s2_authors = s2_paper.get("authors", [])
    for author in s2_authors[:2]:
        author_id = author.get("authorId")
        if not author_id:
            continue
        print(f"[INFO] Fetching papers by {author.get('name', 'unknown')}...", file=sys.stderr)
        author_papers = get_author_papers(author_id, limit=10)
        for p in author_papers:
            p["_source"] = "same_author"
        all_candidates.extend(author_papers)

    # Rank and deduplicate
    ranked = rank_and_dedupe(all_candidates, paper_id)
    return ranked[:top_n]


# ─── Author Twitter finder ────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    return re.sub(r'[^a-z ]', '', name.lower()).strip()


def _match_github_to_author(udata: dict, author_name: str) -> bool:
    """Check if GitHub user matches the author name."""
    gh_name = _normalize_name(udata.get("name") or "")
    norm = _normalize_name(author_name)
    parts = norm.split()
    if not parts or len(parts) < 2:
        return False
    if gh_name == norm:
        return True
    if all(p in gh_name for p in parts):
        return True
    return False


def find_author_twitter(author_name: str, github_urls: list[str] | None = None) -> str | None:
    """
    Find an author's Twitter handle via GitHub.
    Simplified version of arxiv_author_finder.py's logic.
    """
    gh_token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "paper-recommend/1.0"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    def _gh_get(url):
        time.sleep(0.5)
        return _get(url, headers=headers, timeout=15)

    def _extract_twitter(udata):
        if not isinstance(udata, dict):
            return None
        handle = udata.get("twitter_username")
        if handle and isinstance(handle, str) and handle.strip():
            return handle.strip().lstrip("@")
        blog = udata.get("blog", "") or ""
        m = TWITTER_URL_RE.search(blog)
        if m and m.group(1).lower() not in ("home", "share", "intent", "i"):
            return m.group(1)
        return None

    # 1. Check GitHub repo contributors
    if github_urls:
        for repo_url in github_urls[:2]:
            m = GITHUB_REPO_RE.match(repo_url.rstrip("/"))
            if not m:
                continue
            owner, repo = m.group(1), m.group(2)

            # Check repo owner
            udata = _gh_get(f"https://api.github.com/users/{owner}")
            if isinstance(udata, dict):
                if _match_github_to_author(udata, author_name):
                    handle = _extract_twitter(udata)
                    if handle:
                        return handle

            # Check top 5 contributors (per_page=5 already set in API)
            contribs = _gh_get(f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=5")
            if isinstance(contribs, list):
                for c in contribs[:5]:
                    login = c.get("login", "")
                    if not login:
                        continue
                    udata = _gh_get(f"https://api.github.com/users/{login}")
                    if isinstance(udata, dict) and _match_github_to_author(udata, author_name):
                        handle = _extract_twitter(udata)
                        if handle:
                            return handle

    # 2. GitHub user search by name
    parts = author_name.strip().split()
    if len(parts) >= 2:
        query = urllib.parse.quote(f"{author_name} type:user")
        data = _gh_get(f"https://api.github.com/search/users?q={query}&per_page=3")
        if isinstance(data, dict):
            for item in data.get("items", [])[:3]:
                login = item.get("login", "")
                if not login:
                    continue
                udata = _gh_get(f"https://api.github.com/users/{login}")
                if isinstance(udata, dict) and _match_github_to_author(udata, author_name):
                    handle = _extract_twitter(udata)
                    if handle:
                        return handle

    return None


# ─── Output formatting ───────────────────────────────────────────────────────

def format_paper(p: dict, idx: int, twitter_map: dict) -> str:
    """Format a single recommended paper for display."""
    title = p.get("title", "Unknown")
    year = p.get("year") or "?"
    citations = p.get("citationCount", 0) or 0
    source = p.get("_source", "")
    url = p.get("url", "")

    # ArXiv URL if available
    ext = p.get("externalIds", {}) or {}
    arxiv = ext.get("ArXiv")
    if arxiv:
        url = f"https://arxiv.org/abs/{arxiv}"

    authors = [a.get("name", "") for a in (p.get("authors") or [])[:3]]
    author_str = ", ".join(authors)
    if len(p.get("authors", [])) > 3:
        author_str += " et al."

    lines = [f"  {idx}. {title}"]
    lines.append(f"     {author_str} ({year}) | Citations: {citations} | Source: {source}")
    if url:
        lines.append(f"     {url}")

    # Abstract (truncated to 200 chars)
    abstract = p.get("abstract") or ""
    if abstract:
        abstract = abstract.strip().replace("\n", " ")
        if len(abstract) > 200:
            abstract = abstract[:197] + "..."
        lines.append(f"     Abstract: {abstract}")

    # Author twitter links
    tw_links = []
    for a in (p.get("authors") or []):
        name = a.get("name", "")
        if name in twitter_map and twitter_map[name]:
            tw_links.append(f"@{twitter_map[name]}")
    if tw_links:
        lines.append(f"     Twitter: {', '.join(tw_links)}")

    return "\n".join(lines)


def format_paper_zh(p: dict, idx: int, twitter_map: dict) -> str:
    """Format a single recommended paper in concise Chinese."""
    title = p.get("title", "Unknown")
    year = p.get("year") or "?"
    citations = p.get("citationCount", 0) or 0
    source_label = {"cited_by": "引用", "reference": "参考文献", "same_author": "同作者"}.get(
        p.get("_source", ""), p.get("_source", "")
    )
    url = p.get("url", "") or ""

    ext = p.get("externalIds", {}) or {}
    arxiv = ext.get("ArXiv")
    if arxiv:
        url = f"https://arxiv.org/abs/{arxiv}"

    authors = [a.get("name", "") for a in (p.get("authors") or [])[:3]]
    author_str = ", ".join(authors)
    if len(p.get("authors", [])) > 3:
        author_str += " 等"

    # Twitter handles
    tw = []
    for a in (p.get("authors") or []):
        name = a.get("name", "")
        if name in twitter_map and twitter_map[name]:
            tw.append(f"@{twitter_map[name]}")
    tw_str = f" | 推特: {', '.join(tw)}" if tw else ""

    lines = [f"  {idx}. {title}"]
    lines.append(f"     {author_str} ({year}) | 引用: {citations} | 来源: {source_label}{tw_str}")
    if url:
        lines.append(f"     {url}")

    # Abstract (truncated to 200 chars)
    abstract = p.get("abstract") or ""
    if abstract:
        abstract = abstract.strip().replace("\n", " ")
        if len(abstract) > 200:
            abstract = abstract[:197] + "..."
        lines.append(f"     摘要: {abstract}")

    return "\n".join(lines)


def format_output(paper_info: dict, recommendations: list[dict], twitter_map: dict,
                  as_json: bool = False, zh: bool = False) -> str:
    """Format complete output."""
    if as_json:
        output = {
            "source_paper": {
                "title": paper_info.get("title"),
                "arxiv_id": paper_info.get("arxiv_id"),
                "authors": paper_info.get("authors", []),
                "github_urls": paper_info.get("github_urls", []),
            },
            "recommendations": [],
        }
        for p in recommendations:
            rec = {
                "title": p.get("title"),
                "year": p.get("year"),
                "citations": p.get("citationCount", 0),
                "source": p.get("_source", ""),
                "url": p.get("url", ""),
                "authors": [a.get("name", "") for a in (p.get("authors") or [])],
                "author_twitter": {},
            }
            ext = p.get("externalIds", {}) or {}
            if ext.get("ArXiv"):
                rec["arxiv_id"] = ext["ArXiv"]
            for a in (p.get("authors") or []):
                name = a.get("name", "")
                if name in twitter_map and twitter_map[name]:
                    rec["author_twitter"][name] = twitter_map[name]
            output["recommendations"].append(rec)
        return json.dumps(output, ensure_ascii=False, indent=2)

    # Human-readable (English)
    if not zh:
        lines = []
        lines.append(f"\n  Source: {paper_info.get('title', 'Unknown')}")
        if paper_info.get("arxiv_id"):
            lines.append(f"  ArXiv: https://arxiv.org/abs/{paper_info['arxiv_id']}")
        if paper_info.get("authors"):
            lines.append(f"  Authors: {', '.join(paper_info['authors'][:5])}")
        if paper_info.get("github_urls"):
            lines.append(f"  GitHub: {', '.join(paper_info['github_urls'])}")

        lines.append(f"\n  Top-{len(recommendations)} Related Papers:")
        lines.append("  " + "─" * 70)

        for i, p in enumerate(recommendations, 1):
            lines.append(format_paper(p, i, twitter_map))
            if i < len(recommendations):
                lines.append("")

        lines.append("")
        return "\n".join(lines)

    # Chinese output
    lines = []
    source_title = paper_info.get('title', 'Unknown')
    source_arxiv = paper_info.get("arxiv_id")
    source_authors = paper_info.get("authors", [])

    lines.append(f"\n  📄 论文: {source_title}")
    if source_arxiv:
        lines.append(f"  🔗 arXiv: https://arxiv.org/abs/{source_arxiv}")
    if source_authors:
        lines.append(f"  👥 作者: {', '.join(source_authors[:5])}")
    if paper_info.get("github_urls"):
        lines.append(f"  💻 GitHub: {', '.join(paper_info['github_urls'])}")

    lines.append(f"\n  🔖 相关论文 Top-{len(recommendations)}：")
    lines.append("  " + "─" * 60)

    for i, p in enumerate(recommendations, 1):
        lines.append(format_paper_zh(p, i, twitter_map))
        if i < len(recommendations):
            lines.append("")

    lines.append("")
    return "\n".join(lines)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Paper recommendation tool — find related papers + author Twitter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 paper_recommend.py --tweet https://x.com/user/status/123456
  python3 paper_recommend.py --github https://github.com/org/paper-repo
  python3 paper_recommend.py --arxiv 2603.10165
  python3 paper_recommend.py --title "Memory Sparse Attention"
  python3 paper_recommend.py --arxiv 1706.03762 --top 3 --skip-twitter
  python3 paper_recommend.py --arxiv 1706.03762 --zh
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tweet", "-t", help="X/Twitter URL containing paper link")
    group.add_argument("--github", "-g", help="GitHub repo URL for the paper")
    group.add_argument("--arxiv", "-a", help="ArXiv ID or URL (e.g. 2603.10165)")
    group.add_argument("--title", help="Paper title to search for (uses Semantic Scholar)")
    parser.add_argument("--top", "-n", type=int, default=5, help="Number of recommendations (default: 5)")
    parser.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
    parser.add_argument("--zh", action="store_true", help="Simplified Chinese output")
    parser.add_argument("--skip-twitter", action="store_true", help="Skip Twitter lookup (faster)")
    args = parser.parse_args()

    # Step 1: Extract paper info
    paper_info = None

    if args.title:
        # New: direct title search
        paper_info = search_paper_by_title(args.title)
    elif args.arxiv:
        arxiv_id = parse_arxiv_id(args.arxiv)
        if not arxiv_id:
            arxiv_id = args.arxiv.strip()
        paper_info = fetch_arxiv_metadata(arxiv_id)
    elif args.github:
        paper_info = extract_from_github(args.github)
    elif args.tweet:
        paper_info = extract_from_tweet(args.tweet)

    if not paper_info:
        print("[ERROR] Could not extract paper information from input", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Found: {paper_info.get('title', 'Unknown')}", file=sys.stderr)
    print(f"  Authors: {', '.join(paper_info.get('authors', [])[:5])}", file=sys.stderr)

    # Step 2: Find related papers via Semantic Scholar
    recommendations = find_related_papers(paper_info, top_n=args.top)
    if not recommendations:
        print("[WARN] No recommendations found", file=sys.stderr)

    # Step 3: Find author Twitter handles (for recommended papers)
    twitter_map: dict[str, str] = {}
    if not args.skip_twitter and recommendations:
        print("[INFO] Looking up author Twitter accounts...", file=sys.stderr)
        # Collect unique authors from recommendations
        all_authors = set()
        for p in recommendations:
            for a in (p.get("authors") or [])[:2]:  # First 2 authors per paper
                name = a.get("name", "")
                if name and len(name) > 3:
                    all_authors.add(name)

        # Collect all GitHub URLs from source + recommendations
        all_gh_urls = list(paper_info.get("github_urls", []))

        for author in list(all_authors)[:10]:  # Limit to 10 authors total
            handle = find_author_twitter(author, all_gh_urls)
            if handle:
                twitter_map[author] = handle
                print(f"  [Twitter] {author} -> @{handle}", file=sys.stderr)

    # Step 4: Output
    output = format_output(paper_info, recommendations, twitter_map,
                           as_json=args.json, zh=args.zh)
    print(output)


if __name__ == "__main__":
    main()
