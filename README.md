<div align="center">

# 🦞 x-tweet-fetcher

**Fetch tweets, comments, timelines, and articles from X/Twitter — without login or API keys.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![OpenClaw Skill](https://img.shields.io/badge/OpenClaw-Skill-blue.svg)](https://github.com/openclaw/openclaw)
[![Python 3.7+](https://img.shields.io/badge/Python-3.7+-green.svg)](https://www.python.org)
[![GitHub stars](https://img.shields.io/github/stars/ythx-101/x-tweet-fetcher?style=social)](https://github.com/ythx-101/x-tweet-fetcher)

*Zero config · Agent-first JSON output · Cron-friendly exit codes · WeChat + X in one tool*

[Quick Start](#-quick-start) · [Capabilities](#-capabilities) · [Cron Integration](#-cron-integration) · [How It Works](#-how-it-works)

</div>

---

## 😤 Problem

```
You: fetch that tweet for me
AI:  I can't access X/Twitter. Please copy-paste the content manually.

You: ...seriously?
```

X has no free API. Scraping gets you blocked. Browser automation is fragile.

**x-tweet-fetcher** solves this: one command → structured JSON, ready for your agent to consume. No API keys, no login, no cookies.

## 📊 What You Get

| Feature | Zero Deps | With Camofox | Output |
|---------|:---------:|:------------:|--------|
| Single tweet | ✅ | — | text, stats, media, quotes |
| Reply comments | — | ✅ | threaded comment tree |
| User timeline | — | ✅ | paginated tweet list (up to 200) |
| X Articles (long-form) | — | ✅ | full article text |
| X Lists | — | ✅ | paginated tweet list |
| @mentions monitor | — | ✅ | incremental new mentions |
| WeChat article search | ✅ | — | title, url, author, date |
| Tweet discovery | ✅ | optional | keyword search results |
| Google search | — | ✅ | zero API key alternative |
| Chinese platforms | partial | ✅ | Weibo/Bilibili/CSDN/WeChat |
| User profile analysis | — | ✅ + LLM | MBTI, Big Five, topic graph |

> **For AI Agents**: All output is structured JSON. Import as Python modules for direct integration. Exit codes are cron-friendly (`0`=nothing new, `1`=new content).

## 🚀 Quick Start

### 30 seconds (experienced users)

```bash
git clone https://github.com/ythx-101/x-tweet-fetcher.git
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456"
# Done. JSON output with text, likes, retweets, views, media URLs.
```

### For Agents (Python import)

```python
from scripts.fetch_tweet import fetch_tweet

# Fetch a tweet → structured data
tweet = fetch_tweet("https://x.com/user/status/123456")
# {"text": "...", "likes": 91, "retweets": 23, "views": 14468, ...}

# Search WeChat articles (no API key)
from scripts.sogou_wechat import sogou_wechat_search
articles = sogou_wechat_search("AI Agent", max_results=10)

# Discover tweets by keyword
from scripts.x_discover import discover_tweets
result = discover_tweets(["AI Agent", "automation"], max_results=5)

# Google search via Camofox (no API key)
from scripts.camofox_client import camofox_search
results = camofox_search("fetch tweets without API key")
```

### CLI Examples

```bash
# Single tweet (JSON)
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123"

# Human-readable output
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123" --text-only

# Reply comments (requires Camofox)
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123" --replies

# User timeline (up to 200 tweets, auto-pagination)
python3 scripts/fetch_tweet.py --user elonmusk --limit 50

# X Lists
python3 scripts/fetch_tweet.py --list "https://x.com/i/lists/123456"

# X Articles (long-form)
python3 scripts/fetch_tweet.py --article "https://x.com/i/article/123"

# Monitor @mentions (cron-friendly)
python3 scripts/fetch_tweet.py --monitor @username

# WeChat article search
python3 scripts/sogou_wechat.py --keyword "AI Agent" --limit 10 --json

# Discover tweets by keyword
python3 scripts/x_discover.py --keywords "AI Agent,LLM tools" --limit 5 --json

# Chinese platforms (auto-detect: Weibo/Bilibili/CSDN/WeChat)
python3 scripts/fetch_china.py --url "https://mp.weixin.qq.com/s/..."

# Google search (zero API key)
python3 scripts/camofox_client.py "OpenClaw AI agent"

# User profile analysis
python3 scripts/x-profile-analyzer.py --user elonmusk --count 100
```

## ⏰ Cron Integration

All monitoring scripts use exit codes for automation:

| Exit Code | Meaning |
|:---------:|---------|
| `0` | No new content |
| `1` | New content found |
| `2` | Error |

```bash
# Check mentions every 30 min
*/30 * * * * python3 fetch_tweet.py --monitor @username || notify-send "New mentions!"

# Discover tweets daily
0 9 * * * python3 x_discover.py --keywords "AI Agent" --cache ~/.cache/discover.json --json >> ~/discoveries.jsonl
```

## 🔧 Camofox Setup (Optional)

Required for: comments, timelines, mentions, Google search, non-WeChat Chinese platforms.

```bash
# Option 1: OpenClaw plugin
openclaw plugins install @askjo/camofox-browser

# Option 2: Standalone
git clone https://github.com/jo-inc/camofox-browser
cd camofox-browser && npm install && npm start  # Port 9377
```

[Camofox](https://github.com/jo-inc/camofox-browser) is built on [Camoufox](https://camoufox.com) — a Firefox fork with C++ level fingerprint spoofing. Bypasses Google, Cloudflare, and most anti-bot detection.

## 📐 How It Works

```
                    ┌─────────────┐
 --url              │  FxTwitter  │  ← Public API, no auth
                    │  (free)     │
                    └──────┬──────┘
                           │ JSON
┌──────────┐       ┌──────┴──────┐       ┌──────────┐
│ --replies│       │             │       │  Agent   │
│ --user   │──────▶│  Camofox    │──────▶│  (JSON)  │
│ --monitor│       │  (browser)  │       │          │
│ --list   │       └─────────────┘       └──────────┘
└──────────┘
                    ┌─────────────┐
 --keyword          │ DuckDuckGo  │  ← No API key
 sogou_wechat       │ Sogou       │
                    └─────────────┘
```

- **Basic tweets**: [FxTwitter](https://github.com/FxEmbed/FxEmbed) public API (no auth)
- **Comments/Timeline/Mentions**: Camofox headless Firefox + Nitter parsing
- **Views supplement**: FxTwitter API auto-fills view counts missing from Nitter
- **WeChat search**: Sogou search (direct HTTP, no browser)
- **Tweet discovery**: DuckDuckGo with Camofox Google fallback
- **Chinese platforms**: Direct HTTP for WeChat; Camofox for others

## 📦 Requirements

| | Required | Optional |
|--|----------|----------|
| **Runtime** | Python 3.7+ | — |
| **Basic tweets** | Nothing else | — |
| **Advanced features** | [Camofox](https://github.com/jo-inc/camofox-browser) | `duckduckgo-search` (pip) |
| **Profile analysis** | Camofox + LLM API key | — |

## 🤝 Contributing

Issues and PRs welcome! Especially:

- 🐛 Parsing edge cases (new Nitter layouts, X Article formats)
- 🌍 New platform support (Threads, Mastodon, etc.)
- 📊 Performance improvements for large-scale fetching

## 📄 License

[MIT](LICENSE)

---

<div align="center">

*Built for AI agents. Used by [OpenClaw](https://github.com/openclaw/openclaw) 🦞*

**[GitHub](https://github.com/ythx-101/x-tweet-fetcher)** · **[Issues](https://github.com/ythx-101/x-tweet-fetcher/issues)** · **[OpenClaw Q&A](https://github.com/ythx-101/openclaw-qa)**

</div>
