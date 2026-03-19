#!/usr/bin/env python3
"""
arxiv_author_finder.py — 从 arxiv 论文自动发现作者 X/Twitter 账号

Pipeline（4层级联）：
  Layer 1: arxiv API → 提取作者名 + GitHub URL
  Layer 2: GitHub API → twitter_username / blog 字段
  Layer 3: Scholars on Twitter 本地数据集（需预下载）
  Layer 4: 搜索引擎兜底（SearxNG 或 DuckDuckGo）

Usage:
  python3 arxiv_author_finder.py --arxiv "https://arxiv.org/abs/2603.10165"
  python3 arxiv_author_finder.py --arxiv "2603.10165" --github-token $GITHUB_TOKEN
  python3 arxiv_author_finder.py --arxiv "2603.10165" --json
  python3 arxiv_author_finder.py --arxiv "2603.10165" --scholars-db /path/to/scholars.csv

环境变量：
  GITHUB_TOKEN — GitHub Personal Access Token（5000次/小时 vs 60次/小时）

可选依赖：
  duckduckgo_search — Layer 4 网络搜索的首选后端（pip install duckduckgo-search）。
                      未安装时自动回退至本地 SearxNG（127.0.0.1:8080）。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ─── Config ───────────────────────────────────────────────────────────────────

ARXIV_API = "https://export.arxiv.org/api/query?id_list={arxiv_id}"
GITHUB_USER_API  = "https://api.github.com/users/{username}"
GITHUB_ORG_API   = "https://api.github.com/orgs/{org}"
GITHUB_REPO_API  = "https://api.github.com/repos/{owner}/{repo}"
GITHUB_CONTRIB_API = "https://api.github.com/repos/{owner}/{repo}/contributors?per_page=5"
GITHUB_ORG_MEMBERS_API = "https://api.github.com/orgs/{org}/members?per_page=10"
GITHUB_SEARCH_USERS_API = "https://api.github.com/search/users?q={query}&per_page=5"

TWITTER_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,50})(?:[/?#]|$)'
)
GITHUB_REPO_URL_RE = re.compile(
    r'https?://github\.com/([A-Za-z0-9_\-\.]+)/([A-Za-z0-9_\-\.]+)'
)

REQUEST_DELAY = 0.5   # seconds between GitHub API calls


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _get(url: str, headers: dict | None = None, timeout: int = 15) -> dict | str | None:
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", "arxiv-author-finder/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except Exception:
                return raw
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(f"[WARN] GitHub rate limit hit. Set GITHUB_TOKEN for higher limits.", file=sys.stderr)
        elif e.code != 404:
            print(f"[WARN] HTTP {e.code} — {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[WARN] Request failed ({url[:60]}...): {e}", file=sys.stderr)
        return None


def github_get(path_url: str, token: str | None) -> dict | list | None:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers["Accept"] = "application/vnd.github+json"
    time.sleep(REQUEST_DELAY)
    return _get(path_url, headers=headers)


# ─── Layer 1: arxiv API ───────────────────────────────────────────────────────

def parse_arxiv_id(url_or_id: str) -> str:
    """Extract arxiv ID from URL or raw ID string."""
    url_or_id = url_or_id.strip().rstrip("/")
    # e.g. arxiv.org/abs/2603.10165 or arxiv.org/abs/cs/0301017
    m = re.search(r'arxiv\.org/(?:abs|pdf|html)/([^\s?#]+)', url_or_id)
    if m:
        return m.group(1)
    # Raw ID like "2603.10165" or "cs.AI/0301017"
    if re.match(r'[\w.]+/\d{7}|\d{4}\.\d{4,5}', url_or_id):
        return url_or_id
    raise ValueError(f"Cannot parse arxiv ID from: {url_or_id!r}")


def fetch_arxiv_paper(arxiv_id: str) -> dict:
    """
    Returns:
      {
        "id": str,
        "title": str,
        "authors": [str, ...],
        "abstract": str,
        "github_urls": [str, ...],   # extracted from abstract/comment
        "arxiv_url": str,
      }
    """
    url = ARXIV_API.format(arxiv_id=urllib.parse.quote(arxiv_id))
    raw = _get(url, timeout=20)
    if not isinstance(raw, str):
        raise RuntimeError(f"Failed to fetch arxiv metadata for {arxiv_id}")

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"Malformed XML from arxiv API for {arxiv_id}: {exc}") from exc
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise RuntimeError(f"No arxiv entry found for ID: {arxiv_id}")

    title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
    abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
    authors = [
        s for a in entry.findall("atom:author", ns)
        if (s := (a.findtext("atom:name", "", ns) or "").strip())
    ]

    # Extract GitHub URLs from abstract + any comment field
    combined_text = abstract
    comment_el = entry.find("arxiv:comment", ns)
    if comment_el is not None and comment_el.text:
        combined_text += " " + comment_el.text
    # Also check links
    for link in entry.findall("atom:link", ns):
        href = link.get("href", "")
        combined_text += " " + href

    github_urls = list(dict.fromkeys(
        m.group(0).rstrip(".,;)'\"")
        for m in GITHUB_REPO_URL_RE.finditer(combined_text)
    ))

    arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

    return {
        "id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "github_urls": github_urls,
        "arxiv_url": arxiv_url,
    }


def search_github_for_paper(title: str, token: str | None) -> list[str]:
    """Try to find a GitHub repo URL by searching for the paper title."""
    query = urllib.parse.quote(f'"{title}" in:readme')
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&per_page=3"
    result = github_get(url, token)
    if not result or not isinstance(result, dict):
        return []
    items = result.get("items", [])
    return [item["html_url"] for item in items if item.get("html_url")]


# ─── Layer 2: GitHub API ──────────────────────────────────────────────────────

def extract_twitter_from_github_user(user_data: dict) -> str | None:
    """Extract twitter handle from GitHub user/org API response."""
    # Direct field
    handle = user_data.get("twitter_username")
    if handle and isinstance(handle, str) and handle.strip():
        return handle.strip().lstrip("@")

    # blog field — may contain twitter.com URL
    blog = user_data.get("blog", "") or ""
    m = TWITTER_URL_RE.search(blog)
    if m:
        handle = m.group(1)
        if handle.lower() not in ("home", "share", "intent", "i"):
            return handle

    return None


def find_twitter_via_repo(repo_url: str, authors: list[str], token: str | None) -> dict[str, str]:
    """
    Given a GitHub repo URL, find twitter handles for authors.
    Returns {author_name: twitter_handle}
    """
    m = GITHUB_REPO_URL_RE.match(repo_url.rstrip("/"))
    if not m:
        return {}
    owner, repo = m.group(1), m.group(2)
    results: dict[str, str] = {}

    # 1. Get repo info (owner might be org or user)
    repo_data = github_get(GITHUB_REPO_API.format(owner=owner, repo=repo), token)
    owner_type = "unknown"
    if isinstance(repo_data, dict):
        owner_type = (repo_data.get("owner") or {}).get("type", "User")

    # 2. If org, get org twitter + members
    if owner_type == "Organization":
        org_data = github_get(GITHUB_ORG_API.format(org=owner), token)
        if isinstance(org_data, dict):
            handle = extract_twitter_from_github_user(org_data)
            if handle:
                # Attribute to first author as "org account"
                if authors:
                    results[authors[0]] = results.get(authors[0]) or handle

        members = github_get(GITHUB_ORG_MEMBERS_API.format(org=owner), token)
        if isinstance(members, list):
            for member in members[:10]:  # Limit to avoid API quota exhaustion
                uname = member.get("login", "")
                if not uname:
                    continue
                udata = github_get(GITHUB_USER_API.format(username=uname), token)
                if not isinstance(udata, dict):
                    continue
                handle = extract_twitter_from_github_user(udata)
                if handle:
                    # Match member name to author
                    matched = _match_github_to_author(udata, authors)
                    if matched:
                        results[matched] = handle
    else:
        # 3. Get owner user directly
        udata = github_get(GITHUB_USER_API.format(username=owner), token)
        if isinstance(udata, dict):
            handle = extract_twitter_from_github_user(udata)
            if handle:
                matched = _match_github_to_author(udata, authors)
                if matched:
                    results[matched] = handle

    # 4. Get contributors
    contribs = github_get(GITHUB_CONTRIB_API.format(owner=owner, repo=repo), token)
    if isinstance(contribs, list):
        for c in contribs:
            uname = c.get("login", "")
            if not uname:
                continue
            udata = github_get(GITHUB_USER_API.format(username=uname), token)
            if not isinstance(udata, dict):
                continue
            handle = extract_twitter_from_github_user(udata)
            if handle:
                matched = _match_github_to_author(udata, authors)
                if matched and matched not in results:
                    results[matched] = handle

    return results


def _normalize_name(name: str) -> str:
    """Lowercase, remove punctuation, collapse spaces."""
    return re.sub(r'[^a-z ]', '', name.lower()).strip()


def _match_github_to_author(udata: dict, authors: list[str]) -> str | None:
    """
    Try to match a GitHub user to one of the paper authors.
    Checks: name field, login, bio against author names.
    Returns matched author name or None.
    """
    gh_name = _normalize_name(udata.get("name") or "")
    gh_login = _normalize_name(udata.get("login") or "")
    gh_bio = (udata.get("bio") or "").lower()

    best_match = None
    best_score = 0

    for author in authors:
        norm_author = _normalize_name(author)
        if not norm_author:
            continue

        # Exact name match
        if gh_name == norm_author:
            return author

        # Parts match: all parts of author name appear in github name
        author_parts = norm_author.split()
        if len(author_parts) >= 2:
            if all(p in gh_name for p in author_parts):
                return author
            # Last name + first initial
            last = author_parts[-1]
            first_initial = author_parts[0][0] if author_parts[0] else ""
            if last in gh_name and first_initial and first_initial in gh_name:
                score = len(last) + 1
                if score > best_score:
                    best_score = score
                    best_match = author

        # Login contains author last name
        if len(author_parts) >= 1:
            last = author_parts[-1]
            if len(last) >= 4 and last in gh_login:
                score = len(last)
                if score > best_score:
                    best_score = score
                    best_match = author

    return best_match if best_score >= 4 else None


def search_github_users_for_author(author_name: str, token: str | None) -> str | None:
    """
    Use GitHub user search to try to find an author's account.
    Returns twitter handle or None.
    """
    parts = author_name.strip().split()
    if len(parts) < 2:
        return None

    # Search by full name
    query = urllib.parse.quote(f"{author_name} type:user")
    url = GITHUB_SEARCH_USERS_API.format(query=query)
    result = github_get(url, token)
    if not isinstance(result, dict):
        return None

    items = result.get("items", [])
    for item in items[:3]:
        uname = item.get("login", "")
        if not uname:
            continue
        udata = github_get(GITHUB_USER_API.format(username=uname), token)
        if not isinstance(udata, dict):
            continue
        gh_name = _normalize_name(udata.get("name") or "")
        norm_author = _normalize_name(author_name)
        author_parts = norm_author.split()
        # Only trust if name matches well
        if len(author_parts) >= 2 and all(p in gh_name for p in author_parts):
            handle = extract_twitter_from_github_user(udata)
            if handle:
                return handle

    return None


# ─── Layer 3: Scholars on Twitter dataset ─────────────────────────────────────

def load_scholars_dataset(csv_path: str) -> dict[str, str]:
    """
    Load Scholars on Twitter dataset (CSV).
    Expected columns: author_name (or full_name), twitter_handle (or screen_name)
    Returns {normalized_name: handle}
    """
    import csv
    mapping = {}
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Build column name mapping: original_header → lowercase for matching
            orig_headers = list(reader.fieldnames or [])
            name_col = next((h for h in orig_headers if "name" in h.lower()), None)
            handle_col = next(
                (h for h in orig_headers if any(k in h.lower() for k in ("twitter", "screen", "handle"))),
                None
            )
            if not name_col or not handle_col:
                print(f"[WARN] Cannot identify name/handle columns in {csv_path}. Headers: {orig_headers}", file=sys.stderr)
                return {}
            for row in reader:
                name = row.get(name_col, "").strip()
                handle = row.get(handle_col, "").strip().lstrip("@")
                if name and handle:
                    mapping[_normalize_name(name)] = handle
    except Exception as e:
        print(f"[WARN] Failed to load scholars dataset: {e}", file=sys.stderr)
    return mapping


def lookup_scholars(author: str, dataset: dict[str, str]) -> str | None:
    """Fuzzy match author name in scholars dataset."""
    norm = _normalize_name(author)
    if norm in dataset:
        return dataset[norm]

    # Try lastname, firstname → firstname lastname
    parts = norm.split()
    if len(parts) == 2:
        reversed_name = f"{parts[1]} {parts[0]}"
        if reversed_name in dataset:
            return dataset[reversed_name]

    # Partial match: last name + first initial
    if len(parts) >= 2:
        last = parts[-1]
        first_init = parts[0][0] if parts[0] else ""
        for key, handle in dataset.items():
            key_parts = key.split()
            if len(key_parts) >= 2 and key_parts[-1] == last:
                if first_init and key_parts[0].startswith(first_init):
                    return handle

    return None


# ─── Layer 4: Search fallback ─────────────────────────────────────────────────

def search_twitter_for_author(author_name: str, affiliation: str = "") -> str | None:
    """
    Use DuckDuckGo or local SearxNG to search for an author's Twitter.
    Returns twitter handle or None.
    """
    queries = [
        f'"{author_name}" twitter.com',
        f'"{author_name}" site:x.com',
    ]
    if affiliation:
        queries.insert(0, f'"{author_name}" "{affiliation}" twitter')

    for query in queries:
        results = _search_web(query, max_results=5)
        for r in results:
            url = r.get("url", r.get("href", ""))
            snippet = r.get("snippet", r.get("body", ""))
            for text in [url, snippet]:
                m = TWITTER_URL_RE.search(text)
                if m:
                    handle = m.group(1)
                    if handle.lower() not in ("home", "share", "intent", "i", "search"):
                        # Basic name plausibility check
                        if _name_plausibly_matches_handle(author_name, handle):
                            return handle
        time.sleep(0.3)

    return None


def _name_plausibly_matches_handle(author_name: str, handle: str) -> bool:
    """Very loose check: last name appears in handle (case insensitive)."""
    parts = author_name.lower().split()
    if not parts:
        return True
    last = parts[-1]
    if len(last) < 4:
        return True  # Too short to verify
    return last in handle.lower()


def _search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search via DuckDuckGo or Camofox."""
    try:
        from duckduckgo_search import DDGS
        import warnings
        warnings.filterwarnings("ignore")
        results = DDGS().text(query, max_results=max_results)
        if results:
            return results
    except Exception:
        pass

    # Fallback: local SearxNG at 127.0.0.1:8080 or VPS localhost:8080
    searxng_urls = [
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]
    for base in searxng_urls:
        try:
            url = f"{base}/search?q={urllib.parse.quote(query)}&format=json&categories=general"
            raw = _get(url, timeout=10)
            if isinstance(raw, dict) and raw.get("results"):
                return [
                    {"url": r.get("url",""), "snippet": r.get("content","")}
                    for r in raw["results"][:max_results]
                ]
        except Exception:
            pass

    return []


# ─── Main finder ──────────────────────────────────────────────────────────────

class ArxivAuthorFinder:
    def __init__(
        self,
        github_token: str | None = None,
        scholars_db: str | None = None,
        skip_search: bool = False,
        verbose: bool = False,
    ):
        self.token = github_token or os.environ.get("GITHUB_TOKEN")
        self.scholars: dict[str, str] = {}
        if scholars_db and os.path.exists(scholars_db):
            self.scholars = load_scholars_dataset(scholars_db)
            if verbose:
                print(f"[INFO] Loaded {len(self.scholars)} entries from scholars dataset", file=sys.stderr)
        self.skip_search = skip_search
        self.verbose = verbose

    def find(self, arxiv_url_or_id: str) -> dict:
        """
        Returns:
        {
          "paper": { title, authors, arxiv_url, github_urls },
          "results": { author_name: { "handle": str|None, "source": str, "confidence": str } },
          "summary": { found: int, total: int, coverage_pct: float }
        }
        """
        # Layer 0: Parse paper
        arxiv_id = parse_arxiv_id(arxiv_url_or_id)
        if self.verbose:
            print(f"[INFO] Fetching arxiv:{arxiv_id} ...", file=sys.stderr)
        paper = fetch_arxiv_paper(arxiv_id)

        authors = paper["authors"]
        github_urls = paper["github_urls"]

        if self.verbose:
            print(f"[INFO] Paper: {paper['title'][:60]}", file=sys.stderr)
            print(f"[INFO] Authors ({len(authors)}): {', '.join(authors)}", file=sys.stderr)
            print(f"[INFO] GitHub URLs found: {github_urls}", file=sys.stderr)

        results: dict[str, dict] = {a: {"handle": None, "source": None, "confidence": None} for a in authors}

        # Layer 1: GitHub via repo URLs
        if github_urls:
            for repo_url in github_urls:
                found = find_twitter_via_repo(repo_url, authors, self.token)
                for author, handle in found.items():
                    if results[author]["handle"] is None:
                        results[author] = {"handle": handle, "source": "github_repo", "confidence": "high"}
                        if self.verbose:
                            print(f"  [GitHub] {author} → @{handle}", file=sys.stderr)
        else:
            # Try GitHub search for paper
            if self.verbose:
                print("[INFO] No GitHub URL in paper, trying search...", file=sys.stderr)
            found_repos = search_github_for_paper(paper["title"], self.token)
            for repo_url in found_repos[:2]:
                found = find_twitter_via_repo(repo_url, authors, self.token)
                for author, handle in found.items():
                    if results[author]["handle"] is None:
                        results[author] = {"handle": handle, "source": "github_search", "confidence": "medium"}
                        if self.verbose:
                            print(f"  [GitHub/search] {author} → @{handle}", file=sys.stderr)

        # Layer 1b: GitHub user search for still-missing authors
        missing = [a for a, v in results.items() if v["handle"] is None]
        for author in missing:
            handle = search_github_users_for_author(author, self.token)
            if handle:
                results[author] = {"handle": handle, "source": "github_user_search", "confidence": "medium"}
                if self.verbose:
                    print(f"  [GitHub/user] {author} → @{handle}", file=sys.stderr)

        # Layer 2: Scholars on Twitter dataset
        missing = [a for a, v in results.items() if v["handle"] is None]
        for author in missing:
            handle = lookup_scholars(author, self.scholars)
            if handle:
                results[author] = {"handle": handle, "source": "scholars_dataset", "confidence": "high"}
                if self.verbose:
                    print(f"  [Scholars] {author} → @{handle}", file=sys.stderr)

        # Layer 3: Search fallback
        if not self.skip_search:
            missing = [a for a, v in results.items() if v["handle"] is None]
            for author in missing:
                handle = search_twitter_for_author(author)
                if handle:
                    results[author] = {"handle": handle, "source": "web_search", "confidence": "low"}
                    if self.verbose:
                        print(f"  [Search] {author} → @{handle}", file=sys.stderr)

        # Summary
        found_count = sum(1 for v in results.values() if v["handle"])
        total = len(authors)
        coverage = found_count / total * 100 if total > 0 else 0

        return {
            "paper": {
                "title": paper["title"],
                "arxiv_id": arxiv_id,
                "arxiv_url": paper["arxiv_url"],
                "authors": authors,
                "github_urls": github_urls,
            },
            "results": results,
            "summary": {
                "found": found_count,
                "total": total,
                "coverage_pct": round(coverage, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Discover X/Twitter accounts of arxiv paper authors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 arxiv_author_finder.py --arxiv 2603.10165
  python3 arxiv_author_finder.py --arxiv https://arxiv.org/abs/1706.03762 --json
  python3 arxiv_author_finder.py --arxiv 2603.10165 --github-token $GITHUB_TOKEN --verbose
  python3 arxiv_author_finder.py --arxiv 2603.10165 --scholars-db scholars.csv
        """
    )
    parser.add_argument("--arxiv", "-a", required=True, help="arxiv ID or URL (e.g. 2603.10165)")
    parser.add_argument("--github-token", "-t", help="GitHub Personal Access Token (or set GITHUB_TOKEN env)")
    parser.add_argument("--scholars-db", "-s", help="Path to Scholars on Twitter CSV dataset")
    parser.add_argument("--skip-search", action="store_true", help="Skip web search fallback (faster, less coverage)")
    parser.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show progress")
    args = parser.parse_args()

    finder = ArxivAuthorFinder(
        github_token=args.github_token,
        scholars_db=args.scholars_db,
        skip_search=args.skip_search,
        verbose=args.verbose,
    )

    try:
        output = finder.find(args.arxiv)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # Human-readable output
    paper = output["paper"]
    results = output["results"]
    summary = output["summary"]

    print(f"\n  Paper: {paper['title']}")
    print(f"  arxiv: {paper['arxiv_url']}")
    if paper["github_urls"]:
        print(f"  GitHub: {', '.join(paper['github_urls'])}")
    print()
    print(f"  {'Author':<30} {'Twitter':<25} {'Source':<20} {'Confidence'}")
    print("  " + "─" * 85)
    for author, info in results.items():
        handle = f"@{info['handle']}" if info["handle"] else "—"
        source = info["source"] or ""
        conf = info["confidence"] or ""
        print(f"  {author:<30} {handle:<25} {source:<20} {conf}")
    print()
    print(f"  Coverage: {summary['found']}/{summary['total']} authors ({summary['coverage_pct']}%)")
    print()


if __name__ == "__main__":
    main()
