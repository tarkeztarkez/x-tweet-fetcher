#!/usr/bin/env python3
"""
X Tweet Fetcher - Fetch tweets from X/Twitter without login or API keys.

Backends:
  - FxTwitter  (zero deps) — single tweet fetch via public API
  - Nitter     (HTTP only) — user timeline, search, replies via local Nitter

Modes:
  --url <URL>              Fetch single tweet via FxTwitter (zero deps)
  --url <URL> --replies    Fetch tweet + replies via Nitter
  --user <username>        Fetch user timeline via Nitter
  --monitor @username      Monitor X mentions via Nitter (incremental, cron-friendly)

Note on --monitor mode:
  Uses Nitter search to find mentions. First run establishes a baseline (no output).
  Subsequent runs only report new mentions.
  Exit code: 0 = no new mentions, 1 = new mentions found (cron-friendly).
"""

import json
import os
import re
import sys
import argparse
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, List, Any


# ---------------------------------------------------------------------------
# i18n — bilingual messages (zh default, en via --lang en)
# ---------------------------------------------------------------------------

_MESSAGES = {
    "zh": {
        # stderr progress
        "opening_via_nitter": "[x-tweet-fetcher] 正在通过 Nitter 打开 {url} ...",
        "nitter_error": "[Nitter] 获取失败: {err}",
        # error field values (go into JSON output)
        "err_nitter_not_running_user": (
            "Nitter 后端不可用。"
            "使用 --user 前请先确保本地 Nitter 运行正常。"
        ),
        "err_nitter_not_running_replies": (
            "Nitter 后端不可用。"
            "使用 --replies 前请先确保本地 Nitter 运行正常。"
        ),
        "err_snapshot_failed": "无法从 Nitter 获取页面数据",
        "err_mutually_exclusive": "错误：--user、--url 和 --monitor 不能同时使用",
        "err_no_input": "错误：请提供 --url 或 --user",
        "err_prefix": "错误：",
        # warning field values
        "warn_no_tweets": (
            "未解析到推文。Nitter 可能触发了频率限制，或该用户不存在，请稍后重试。"
        ),
        "warn_no_replies": (
            "未解析到评论。该推文可能没有回复，或 Nitter 触发了频率限制，请稍后重试。"
        ),
        # text-only labels
        "timeline_header": "@{user} — 最新 {count} 条推文",
        "replies_header": "{url} 的评论区",
        "media_label": "🖼 {n} 张图片",
        "media_label_with_urls": "🖼 {n} 张图片: {urls}",
        # article/tweet text-only
        "article_by": "作者 @{screen_name} | {created_at}",
        "article_stats": "点赞: {likes} | 转推: {retweets} | 浏览: {views}",
        "article_words": "字数: {word_count}",
        "tweet_stats": "\n点赞: {likes} | 转推: {retweets} | 浏览: {views}",
        # FxTwitter network error
        "err_network": "网络错误：重试后仍无法获取推文",
        "err_unexpected": "获取推文时发生意外错误",
        # monitor mode
        "monitor_baseline": "[monitor] 首次运行，建立基线 ({count} 条)，下次运行起报告增量。",
        "monitor_no_new": "[monitor] 无新 mentions（已知 {known} 条）。",
        "monitor_new_found": "[monitor] 发现 {count} 条新 mentions！",
        "monitor_searching": "[monitor] 搜索 mentions: {query}",
        "monitor_nitter_error": (
            "Nitter 后端不可用。"
            "使用 --monitor 前请先确保本地 Nitter 运行正常。"
        ),
        "monitor_header": "@{username} 的新 mentions ({count} 条)",
    },
    "en": {
        "opening_via_nitter": "[x-tweet-fetcher] Opening {url} via Nitter...",
        "nitter_error": "[Nitter] fetch error: {err}",
        "err_nitter_not_running_user": (
            "Nitter backend is not available. "
            "Please ensure local Nitter is running before using --user."
        ),
        "err_nitter_not_running_replies": (
            "Nitter backend is not available. "
            "Please ensure local Nitter is running before using --replies."
        ),
        "err_snapshot_failed": "Failed to get page data from Nitter",
        "err_mutually_exclusive": "Error: --user, --url, and --monitor are mutually exclusive",
        "err_no_input": "Error: provide --url or --user",
        "err_prefix": "Error: ",
        "warn_no_tweets": (
            "No tweets parsed. Nitter may be rate-limited or the user doesn't exist. "
            "Try again later."
        ),
        "warn_no_replies": (
            "No replies parsed. The tweet may have no replies, "
            "or Nitter may be rate-limited. Try again later."
        ),
        "timeline_header": "@{user} — latest {count} tweets",
        "replies_header": "Replies to {url}",
        "media_label": "🖼 {n} media",
        "media_label_with_urls": "🖼 {n} image(s): {urls}",
        "article_by": "By @{screen_name} | {created_at}",
        "article_stats": "Likes: {likes} | Retweets: {retweets} | Views: {views}",
        "article_words": "Words: {word_count}",
        "tweet_stats": "\nLikes: {likes} | Retweets: {retweets} | Views: {views}",
        "err_network": "Network error: Failed to fetch tweet after retry",
        "err_unexpected": "An unexpected error occurred while fetching the tweet",
        # monitor mode
        "monitor_baseline": "[monitor] First run: baseline established ({count} entries). Future runs will report incremental results.",
        "monitor_no_new": "[monitor] No new mentions (known: {known}).",
        "monitor_new_found": "[monitor] Found {count} new mention(s)!",
        "monitor_searching": "[monitor] Searching mentions: {query}",
        "monitor_nitter_error": (
            "Nitter backend is not available. "
            "Please ensure local Nitter is running before using --monitor."
        ),
        "monitor_header": "New mentions for @{username} ({count})",
    },
}

# Module-level lang (set once in main(), read everywhere)
_lang: str = "zh"


def t(key: str, **kwargs) -> str:
    """Look up a message in the current language, formatting with kwargs."""
    msg = _MESSAGES.get(_lang, _MESSAGES["zh"]).get(key, key)
    return msg.format(**kwargs) if kwargs else msg


# ---------------------------------------------------------------------------
# FxTwitter single-tweet fetch (zero deps)
# ---------------------------------------------------------------------------

def parse_tweet_url(url: str) -> tuple:
    """Extract username and tweet_id from X/Twitter URL."""
    patterns = [
        r'(?:x\.com|twitter\.com)/([a-zA-Z0-9_]{1,15})/status/(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            tweet_id = match.group(2)
            if not re.match(r'^[a-zA-Z0-9_]{1,15}$', username):
                raise ValueError(f"Invalid username format: {username}")
            if not tweet_id.isdigit():
                raise ValueError(f"Invalid tweet ID format: {tweet_id}")
            return username, tweet_id
    raise ValueError(f"Cannot parse tweet URL: {url}")


def extract_media(tweet_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract media information (photos/videos) from tweet object."""
    media_data = {}
    media = tweet_obj.get("media", {})

    all_media = media.get("all", [])
    if all_media and isinstance(all_media, list):
        photos = [item for item in all_media if item.get("type") == "photo"]
        if photos:
            media_data["images"] = []
            for photo in photos:
                image_info = {"url": photo.get("url", "")}
                if photo.get("width"):
                    image_info["width"] = photo.get("width")
                if photo.get("height"):
                    image_info["height"] = photo.get("height")
                media_data["images"].append(image_info)

    videos = media.get("videos", [])
    if videos and isinstance(videos, list) and len(videos) > 0:
        media_data["videos"] = []
        for video in videos:
            video_info = {}
            if video.get("url"):
                video_info["url"] = video.get("url")
            if video.get("duration"):
                video_info["duration"] = video.get("duration")
            if video.get("thumbnail_url"):
                video_info["thumbnail"] = video.get("thumbnail_url")
            if video.get("variants") and isinstance(video.get("variants"), list):
                video_info["variants"] = []
                for variant in video.get("variants", []):
                    variant_info = {}
                    if variant.get("url"):
                        variant_info["url"] = variant.get("url")
                    if variant.get("bitrate"):
                        variant_info["bitrate"] = variant.get("bitrate")
                    if variant.get("content_type"):
                        variant_info["content_type"] = variant.get("content_type")
                    if variant_info:
                        video_info["variants"].append(variant_info)
            if video_info:
                media_data["videos"].append(video_info)

    return media_data if media_data else None


def fetch_tweet(url: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch single tweet via FxTwitter API (zero deps)."""
    try:
        username, tweet_id = parse_tweet_url(url)
    except ValueError as e:
        return {"url": url, "error": str(e)}
    result = {"url": url, "username": username, "tweet_id": tweet_id}

    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"

    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())

            if data.get("code") != 200:
                result["error"] = f"FxTwitter returned code {data.get('code')}: {data.get('message', 'Unknown')}"
                return result

            tweet = data["tweet"]
            tweet_data = {
                "text": tweet.get("text", ""),
                "author": tweet.get("author", {}).get("name", ""),
                "screen_name": tweet.get("author", {}).get("screen_name", ""),
                "likes": tweet.get("likes", 0),
                "retweets": tweet.get("retweets", 0),
                "bookmarks": tweet.get("bookmarks", 0),
                "views": tweet.get("views", 0),
                "replies_count": tweet.get("replies", 0),
                "created_at": tweet.get("created_at", ""),
                "is_note_tweet": tweet.get("is_note_tweet", False),
                "lang": tweet.get("lang", ""),
            }

            media = extract_media(tweet)
            if media:
                tweet_data["media"] = media

            if tweet.get("quote"):
                qt = tweet["quote"]
                tweet_data["quote"] = {
                    "text": qt.get("text", ""),
                    "author": qt.get("author", {}).get("name", ""),
                    "screen_name": qt.get("author", {}).get("screen_name", ""),
                    "likes": qt.get("likes", 0),
                    "retweets": qt.get("retweets", 0),
                    "views": qt.get("views", 0),
                }
                quote_media = extract_media(qt)
                if quote_media:
                    tweet_data["quote"]["media"] = quote_media

            article = tweet.get("article")
            if article:
                article_data = {
                    "title": article.get("title", ""),
                    "preview_text": article.get("preview_text", ""),
                    "created_at": article.get("created_at", ""),
                }
                content = article.get("content", {})
                blocks = content.get("blocks", [])
                if blocks:
                    full_text = "\n\n".join(
                        b.get("text", "") for b in blocks if b.get("text", "")
                    )
                    article_data["full_text"] = full_text
                    article_data["word_count"] = len(full_text.split())
                    article_data["char_count"] = len(full_text)
                # 提取 article 内的图片
                article_images = []
                cover = article.get("cover_media", {})
                if cover:
                    cover_url = cover.get("media_info", {}).get("original_img_url")
                    if cover_url:
                        article_images.append({"type": "cover", "url": cover_url})
                for entity in article.get("media_entities", []):
                    img_url = entity.get("media_info", {}).get("original_img_url")
                    if img_url:
                        article_images.append({"type": "image", "url": img_url})
                if article_images:
                    article_data["images"] = article_images
                    article_data["image_count"] = len(article_images)

                tweet_data["article"] = article_data
                tweet_data["is_article"] = True
            else:
                tweet_data["is_article"] = False

            result["tweet"] = tweet_data
            return result

        except urllib.error.URLError:
            if attempt < max_attempts - 1:
                time.sleep(1)
                continue
            else:
                result["error"] = t("err_network")
                return result
        except urllib.error.HTTPError as e:
            result["error"] = f"HTTP {e.code}: {e.reason}"
            return result
        except Exception:
            result["error"] = t("err_unexpected")
            return result

    return result


# ---------------------------------------------------------------------------
# Nitter snapshot parsers (stats parsing helper)
# ---------------------------------------------------------------------------

def _parse_stats_from_text(raw: str) -> tuple:
    """Parse stats numbers from Nitter text line like 'content  1   22  4,418'.

    Nitter renders stats as plain numbers separated by spaces (no icon chars on timeline).
    Returns (cleaned_text, replies, retweets, likes, views).
    """
    # Pattern 0: stats-only line (no text prefix), e.g. " 7  9  83 " or "  6  3  39 "
    stat_only = re.match(
        r"^\s*(\d[\d,]*)\s{2,}(\d[\d,]*)\s{2,}(\d[\d,]*)\s*[^\d]*$",
        raw.rstrip(),
    )
    if stat_only:
        nums = [int(stat_only.group(i).replace(",", "")) for i in (1, 2, 3)]
        return "", nums[0], nums[1], nums[2], 0

    # Pattern 1: text content followed by 2–4 space-separated numbers at end
    stat_match = re.search(
        r"^(.*?)\s{2,}(\d[\d,]*)\s{2,}(\d[\d,]*)\s{2,}(\d[\d,]*)\s*[^\d]*$",
        raw.rstrip(),
    )
    if stat_match:
        text_part = stat_match.group(1).strip()
        nums = [int(stat_match.group(i).replace(",", "")) for i in (2, 3, 4)]
        return text_part, nums[0], nums[1], nums[2], 0

    # Only 2 trailing numbers
    stat_match2 = re.search(
        r"^(.*?)\s{2,}(\d[\d,]*)\s{2,}(\d[\d,]*)\s*[^\d]*$",
        raw.rstrip(),
    )
    if stat_match2:
        text_part = stat_match2.group(1).strip()
        nums = [int(stat_match2.group(i).replace(",", "")) for i in (2, 3)]
        return text_part, nums[0], 0, nums[1], 0

    # Private-use unicode icon stats
    icon_match = re.search(
        r"\ue803\s*(\d[\d,]*)?\s*\ue80c\s*(\d[\d,]*)?\s*\ue801\s*(\d[\d,]*)?\s*\ue800",
        raw,
    )
    if icon_match:
        prefix = raw[:icon_match.start()].strip()
        def _icon_int(g):
            return int(g.replace(",", "")) if g else 0
        return (
            prefix,
            _icon_int(icon_match.group(1)),
            _icon_int(icon_match.group(2)),
            _icon_int(icon_match.group(3)),
            0,
        )

    # No stats found — clean any icon chars and return raw text
    cleaned = re.sub(r"\s*[\ue800-\ue8ff]\s*[\d,]+", "", raw).strip()
    return cleaned, 0, 0, 0, 0


# ---------------------------------------------------------------------------
# Nitter direct backend helpers
# ---------------------------------------------------------------------------

def _get_nitter_client():
    """Import and return nitter_client module."""
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import nitter_client
    return nitter_client


def _nitter_available() -> bool:
    """Check if local Nitter instance is reachable."""
    try:
        nc = _get_nitter_client()
        return nc.check_nitter()
    except Exception:
        return False


def _should_use_nitter() -> bool:
    """Check if Nitter is available for use."""
    ok = _nitter_available()
    if ok:
        print("[x-tweet-fetcher] Nitter 可用，使用 Nitter 后端", file=sys.stderr)
    else:
        print("[x-tweet-fetcher] Nitter 不可用", file=sys.stderr)
    return ok


def fetch_user_timeline_nitter(username: str, limit: int = 20) -> Dict[str, Any]:
    """Fetch user timeline via local Nitter (no browser required)."""
    try:
        nitter_client = _get_nitter_client()
    except ImportError as e:
        return {"username": username, "error": f"nitter_client not found: {e}", "tweets": []}

    tweets_raw = nitter_client.fetch_timeline(username, count=limit)

    # Normalize to fetch_tweet.py's tweet dict format
    tweets = []
    for tw in tweets_raw:
        tweets.append({
            "author": f"@{tw.get('username', username)}",
            "author_name": tw.get("display_name", tw.get("username", username)),
            "text": tw.get("text", ""),
            "time_ago": tw.get("time", ""),
            "likes": tw.get("likes", 0),
            "retweets": tw.get("retweets", 0),
            "replies": tw.get("replies", 0),
            "views": tw.get("views", 0),
            "tweet_id": tw.get("tweet_id", ""),
            "media": tw.get("media_urls", []) if tw.get("has_media") else [],
        })

    return {
        "username": username,
        "limit": limit,
        "tweets": tweets,
        "count": len(tweets),
        "backend": "nitter",
    }


def search_mentions_nitter(username: str, limit: int = 20) -> List[Dict]:
    """Search @username mentions via local Nitter."""
    try:
        nitter_client = _get_nitter_client()
    except ImportError as e:
        print(f"[nitter] nitter_client not found: {e}", file=sys.stderr)
        return []

    clean = username.lstrip("@")
    query = f"@{clean}"
    tweets_raw = nitter_client.search_tweets(query, count=limit)

    results = []
    for tw in tweets_raw:
        results.append({
            "url": tw.get("url", ""),
            "title": f"@{tw.get('username', '')}: {tw.get('text', '')[:80]}",
            "snippet": tw.get("text", ""),
            "username": tw.get("username", ""),
            "tweet_id": tw.get("tweet_id", ""),
        })
    return results


# ---------------------------------------------------------------------------
# Mentions 监控（--monitor 模式）
# ---------------------------------------------------------------------------

# 缓存目录：~/.x-tweet-fetcher/
_CACHE_DIR = Path.home() / ".x-tweet-fetcher"
# 单个用户缓存最大保留 URL 数量
_CACHE_MAX = 500


def _get_cache_path(username: str) -> Path:
    """返回指定用户的 mentions 缓存文件路径。"""
    clean = username.lstrip("@").lower()
    return _CACHE_DIR / f"mentions-cache-{clean}.json"


def _load_cache(username: str) -> dict:
    """加载 mentions 缓存，返回 {'seen': [...url...], 'is_baseline': bool}。"""
    path = _get_cache_path(username)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 兼容旧格式（纯列表）
            if isinstance(data, list):
                return {"seen": data, "is_baseline": False}
            return data
        except Exception:
            pass
    return {"seen": [], "is_baseline": True}


def _save_cache(username: str, cache: dict):
    """保存 mentions 缓存到磁盘，超过上限时截断最旧条目。"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if len(cache["seen"]) > _CACHE_MAX:
        cache["seen"] = cache["seen"][-_CACHE_MAX:]
    path = _get_cache_path(username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def monitor_mentions(
    username: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    监控 X mentions 增量变化（via Nitter）。

    首次运行：建立基线，不报任何新内容（exit code 0）。
    后续运行：与缓存对比，只报告新增 URL（exit code 1 = 有新内容）。
    """
    result: Dict[str, Any] = {
        "username": username.lstrip("@"),
        "new_mentions": [],
        "is_baseline": False,
        "known_count": 0,
    }

    # 加载本地缓存
    cache = _load_cache(username)
    seen_set = set(cache["seen"])
    result["known_count"] = len(seen_set)

    # 搜索 mentions via Nitter
    clean_user = username.lstrip("@")
    print(t("monitor_searching", query=f"@{clean_user}"), file=sys.stderr)
    all_results = search_mentions_nitter(clean_user, limit=limit)

    if cache["is_baseline"]:
        # 首次运行：将所有搜索结果写入缓存作为基线，不报新内容
        new_urls = [r["url"] for r in all_results]
        cache["seen"] = list(seen_set | set(new_urls))
        cache["is_baseline"] = False
        _save_cache(username, cache)
        result["is_baseline"] = True
        result["known_count"] = len(cache["seen"])
        print(t("monitor_baseline", count=len(cache["seen"])), file=sys.stderr)
    else:
        # 后续运行：只报告不在缓存中的新条目
        new_mentions = [r for r in all_results if r["url"] not in seen_set]

        for r in new_mentions:
            cache["seen"].append(r["url"])
        _save_cache(username, cache)

        result["new_mentions"] = new_mentions
        result["known_count"] = len(cache["seen"])

        if new_mentions:
            print(t("monitor_new_found", count=len(new_mentions)), file=sys.stderr)
        else:
            print(t("monitor_no_new", known=len(seen_set)), file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    global _lang

    parser = argparse.ArgumentParser(
        description=(
            "Fetch tweets from X/Twitter.\n"
            "  --url <URL>              Single tweet via FxTwitter (zero deps)\n"
            "  --url <URL> --replies    Tweet replies via Nitter\n"
            "  --user <username>        User timeline via Nitter\n"
            "  --monitor @username      Monitor X mentions via Nitter (incremental, cron-friendly)\n"
            "\n"
            "Note: --monitor requires local Nitter. First run builds a baseline (no output).\n"
            "Subsequent runs report only new mentions. Exit code 1 = new content found."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", "-u", help="Tweet URL (x.com or twitter.com)")
    parser.add_argument("--user", help="X/Twitter username (without @)")
    parser.add_argument("--monitor", "-m", metavar="@USERNAME",
                        help="Monitor X mentions for a username (requires Nitter)")
    parser.add_argument("--limit", type=int, default=50, help="Max tweets for --user / max results for --monitor (default: 50 for --user, 10 for --monitor)")
    parser.add_argument("--replies", "-r", action="store_true", help="Fetch replies (requires Nitter)")
    parser.add_argument("--pretty", "-p", action="store_true", help="Pretty print JSON")
    parser.add_argument("--text-only", "-t", action="store_true", help="Human-readable output")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds (default: 30)")
    parser.add_argument(
        "--lang", default="zh", choices=["zh", "en"],
        help="Output language for tool messages: zh (default) or en",
    )

    args = parser.parse_args()

    # Apply language setting globally before any t() calls
    _lang = args.lang

    # Count how many primary modes are requested
    _modes = [bool(args.url), bool(args.user), bool(args.monitor)]
    if sum(_modes) > 1:
        print(t("err_mutually_exclusive"), file=sys.stderr)
        sys.exit(1)

    if not any(_modes):
        parser.print_help()
        sys.exit(1)

    indent = 2 if args.pretty else None

    # ── Mode 0: Mentions 监控 ─────────────────────────────────────────────
    if args.monitor:
        monitor_limit = args.limit if args.limit != 50 else 10

        result = monitor_mentions(
            args.monitor,
            limit=monitor_limit,
        )

        if result.get("error"):
            print(t("err_prefix") + result["error"], file=sys.stderr)
            sys.exit(2)

        if result.get("is_baseline"):
            if not args.text_only:
                print(json.dumps(result, ensure_ascii=False, indent=indent))
            sys.exit(0)

        new_mentions = result.get("new_mentions", [])

        if args.text_only:
            username_clean = result["username"]
            if new_mentions:
                print(t("monitor_header", username=username_clean, count=len(new_mentions)) + "\n")
                for idx, m in enumerate(new_mentions, 1):
                    print(f"[{idx}] {m['title']}")
                    print(f"     {m['url']}")
                    if m.get("snippet"):
                        print(f"     {m['snippet'][:120]}")
                    print()
        else:
            print(json.dumps(result, ensure_ascii=False, indent=indent))

        sys.exit(1 if new_mentions else 0)

    # ── Mode 1: User timeline ─────────────────────────────────────────────
    if args.user:
        result = fetch_user_timeline_nitter(
            args.user,
            limit=args.limit,
        )

        if args.text_only:
            if result.get("error"):
                print(t("err_prefix") + result["error"], file=sys.stderr)
                sys.exit(1)
            tweets = result.get("tweets", [])
            print(t("timeline_header", user=args.user, count=len(tweets)) + "\n")
            for idx, tw in enumerate(tweets, 1):
                print(f"[{idx}] {tw['author_name']} ({tw['author']}) · {tw.get('time_ago', '')}")
                print(f"     {tw['text']}")
                stats = f"     ❤ {tw['likes']}  💬 {tw['replies']}  👁 {tw['views']}"
                if tw.get("media"):
                    stats += "  " + t("media_label", n=len(tw["media"]))
                print(stats)
                print()
        else:
            print(json.dumps(result, ensure_ascii=False, indent=indent))

        if result.get("error"):
            sys.exit(1)
        return

    # ── Mode 2: Tweet replies ─────────────────────────────────────────────
    if args.url and args.replies:
        try:
            username, tweet_id = parse_tweet_url(args.url)
        except ValueError as e:
            print(t("err_prefix") + str(e), file=sys.stderr)
            sys.exit(1)

        try:
            nitter_client = _get_nitter_client()
            detail = nitter_client.fetch_tweet_detail(username, tweet_id)
        except Exception as e:
            result = {"url": args.url, "error": str(e), "replies": []}
            print(json.dumps(result, ensure_ascii=False, indent=indent))
            sys.exit(1)

        replies = detail.get("replies_list", [])
        # Normalize replies format
        normalized = []
        for r in replies:
            normalized.append({
                "author": f"@{r.get('username', '')}",
                "author_name": r.get("display_name", r.get("username", "")),
                "text": r.get("text", ""),
                "time_ago": r.get("time", ""),
                "likes": r.get("likes", 0),
                "retweets": r.get("retweets", 0),
                "replies": r.get("replies", 0),
                "views": r.get("views", 0),
                "tweet_id": r.get("tweet_id", ""),
            })

        result = {
            "url": args.url,
            "username": username,
            "tweet_id": tweet_id,
            "replies": normalized,
            "reply_count": len(normalized),
        }
        if not normalized:
            result["warning"] = t("warn_no_replies")

        if args.text_only:
            print(t("replies_header", url=args.url) + "\n")
            for idx, r in enumerate(normalized, 1):
                print(f"[{idx}] {r['author_name']} ({r['author']}) · {r.get('time_ago', '')}")
                print(f"     {r['text']}")
                stats = f"     ❤ {r['likes']}  💬 {r['replies']}  👁 {r['views']}"
                print(stats)
                print()
        else:
            print(json.dumps(result, ensure_ascii=False, indent=indent))

        return

    # ── Mode 3: Single tweet via FxTwitter (zero deps) ────────────────────
    result = fetch_tweet(args.url, timeout=args.timeout)

    if args.text_only:
        tweet = result.get("tweet", {})
        if tweet.get("is_article") and tweet.get("article", {}).get("full_text"):
            article = tweet["article"]
            print(f"# {article['title']}\n")
            print(t("article_by", screen_name=tweet["screen_name"], created_at=tweet.get("created_at", "")))
            print(t("article_stats", likes=tweet["likes"], retweets=tweet["retweets"], views=tweet["views"]))
            print(t("article_words", word_count=article["word_count"]) + "\n")
            print(article["full_text"])
        elif tweet.get("text"):
            print(f"@{tweet['screen_name']}: {tweet['text']}")
            print(t("tweet_stats", likes=tweet["likes"], retweets=tweet["retweets"], views=tweet["views"]))
        elif result.get("error"):
            print(t("err_prefix") + result["error"], file=sys.stderr)
            sys.exit(1)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=indent))

    if result.get("error"):
        sys.exit(1)


def supplement_views(tweets: List[Dict], max补充: int = 50) -> List[Dict]:
    """用 FxTwitter API 补充浏览量数据"""
    try:
        import requests
    except ImportError:
        print("[views] 'requests' not installed — skipping view supplementation", file=sys.stderr)
        return tweets
    for i, tw in enumerate(tweets[:max补充]):
        if tw.get("views", 0) != 0:
            continue  # 已有浏览量，跳过
        author = tw.get("author", "")
        if not author or not author.startswith("@"):
            print(f"[views] 跳过无 author: {tw.get('text', '')[:50]}...", file=sys.stderr)
            continue
        username = author.lstrip("@")
        tweet_id = tw.get("tweet_id") or tw.get("id")
        if not tweet_id:
            print(f"[views] 跳过无 tweet_id: @{username} - {tw.get('text', '')[:50]}...", file=sys.stderr)
            continue
        try:
            resp = requests.get(f"https://api.fxtwitter.com/{username}/status/{tweet_id}", timeout=5)
            data = resp.json()
            views = data.get("tweet", {}).get("views", 0)
            if views:
                tw["views"] = views
                print(f"[views] {username}/{tweet_id[:8]}... → {views}", file=sys.stderr)
        except Exception:
            pass
    return tweets


if __name__ == "__main__":
    # Version check (best-effort, no crash if unavailable)
    try:
        from scripts.version_check import check_for_update
        check_for_update("ythx-101/x-tweet-fetcher")
    except Exception:
        pass

    main()
