# x-tweet-fetcher

Fetch tweets from X/Twitter **without login or API keys**.

An [OpenClaw](https://github.com/openclaw/openclaw) skill. Zero dependencies, zero configuration.

## What It Can Fetch

| Content | Support | Requirement |
|---------|---------|-------------|
| Regular tweets | ✅ Full text + stats | None |
| Long tweets | ✅ Full text | None |
| X Articles (long-form) | ✅ Full text via `--article` (title+preview without login) | **Camofox required** |
| Quoted tweets | ✅ Included | None |
| Stats (likes/RT/views) | ✅ Included | None |
| **Reply comments** | ⚠️ With comments | **Camofox required** |
| **User timeline** | ⚠️ With timeline | **Camofox required** |
| **Mentions monitoring** | ⚠️ With monitor | **Camofox required** |

## All Scripts

| 脚本 | 功能 | 依赖 |
|------|------|------|
| `scripts/fetch_tweet.py` | 抓推文/评论区/用户时间线 / Mentions 监控 | 基础无依赖，评论区/监控需 Camofox |
| `scripts/camofox_client.py` | Google 搜索（无需 API key） | Camofox |
| `scripts/x-profile-analyzer.py` | X 用户画像分析（MBTI/大五/话题图谱） | Camofox + LLM API |
| `scripts/fetch_china.py` | 国内平台抓取（微博/B站/CSDN/微信公众号） | 微信无依赖，其他需 Camofox |
| `scripts/version_check.py` | 启动时检查 GitHub 新版本（内部模块） | 无依赖，后台线程，失败静默 |

## Quick Start

### Basic Usage (No Dependencies)

```bash
# JSON output
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456"

# Human readable
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456" --text-only

# Pretty JSON
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456" --pretty
```

### Fetching Comments & Timeline (Requires Camofox)

To fetch reply comments or user timelines, you need to install **Camofox** (anti-detection browser server):

```bash
# Option 1: Install as OpenClaw plugin
openclaw plugins install @askjo/camofox-browser

# Option 2: Standalone installation
git clone https://github.com/jo-inc/camofox-browser
cd camofox-browser
npm install
npm start  # Starts on port 9377
```

Then use the `--replies` flag:

```bash
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456" --replies
```

### Fetching X Articles (Long-form Posts)

```bash
# By full article URL
python3 scripts/fetch_tweet.py --article "https://x.com/i/article/2011779830157557760" --pretty

# By bare article ID
python3 scripts/fetch_tweet.py --article "2011779830157557760" --pretty

# Human-readable output
python3 scripts/fetch_tweet.py --article "https://x.com/i/article/2011779830157557760" --text-only
```

> **Note**: X Articles require X login to view full content. Without login, Camofox captures only the publicly visible portion (title + preview). This is an X platform limitation.

## Requirements

- Python 3.7+ (for basic tweet fetching)
- **Camofox** (optional, for comments/timeline only)

## How It Works

- **Basic mode**: Uses [FxTwitter](https://github.com/FxEmbed/FxEmbed) public API to fetch tweet data
- **Comments/Timeline**: Uses Camofox (powered by Camoufox) to bypass anti-bot detection

## Camofox Setup

### What is Camofox?

Camofox is an anti-detection browser server built on [Camoufox](https://camoufox.com) - a Firefox fork with fingerprint spoofing at the C++ level. It can bypass:
- Google bot detection
- Cloudflare protection
- Most anti-scraping measures

### Environment Variable (Optional)

If using Camofox with OpenClaw, set the API key:

```bash
export CAMOFOX_API_KEY="your-secret-key"
openclaw start
```

## Google Search (No API Key)

```bash
# CLI 搜索
python3 scripts/camofox_client.py "OpenClaw AI agent"

# Python 调用
from scripts.camofox_client import camofox_search
results = camofox_search("OpenClaw AI agent")
```

Uses Camofox to search Google directly — zero API keys, no rate limits from search providers.

## User Profile Analysis

```bash
# 分析用户画像（抓推文 + AI 分析）
python3 scripts/x-profile-analyzer.py --user elonmusk --count 100

# 只抓数据不分析
python3 scripts/x-profile-analyzer.py --user elonmusk --no-analyze

# 保存报告
python3 scripts/x-profile-analyzer.py --user elonmusk --output report.md
```

Generates MBTI, Big Five personality traits, topic graph, and communication style analysis from a user's tweets.

## Monitor Mode (Requires Camofox)

Track X/Twitter mentions with incremental detection — perfect for cron jobs and notifications.

### Basic Usage

```bash
# First run (establishes baseline, no new mentions reported)
python3 scripts/fetch_tweet.py --monitor @username

# Subsequent runs (reports only new mentions since last check)
python3 scripts/fetch_tweet.py --monitor @username

# Human-readable output
python3 scripts/fetch_tweet.py --monitor @username --text-only

# Control search result count (default: 10 per strategy)
python3 scripts/fetch_tweet.py --monitor @username --limit 20
```

### How It Works

1. **Dual-Strategy Search**: Uses Google search via Camofox with two queries:
   - `site:x.com @username` — direct @mentions
   - `site:x.com username` — broader mentions without @

2. **Local Cache Deduplication**:
   - Cache file: `~/.x-tweet-fetcher/mentions-cache-{username}.json`
   - Maximum 500 entries per user (rotates automatically)
   - First run establishes baseline (all current mentions are cached)

3. **Exit Codes** (cron-friendly):
   - `0` — No new mentions found
   - `1` — New mentions detected
   - `2` — Error occurred

### Cron Integration

```bash
# Check every 30 minutes, send notification on new mentions
*/30 * * * * python3 /path/to/fetch_tweet.py --monitor @your_username --text-only || notify-send "New X mentions!"

# Example with email notification
0 * * * * python3 /path/to/fetch_tweet.py --monitor @your_username --text-only | mail -s "New X mentions" your@email.com

# Example with Discord webhook (requires curl)
*/15 * * * * python3 /path/to/fetch_tweet.py --monitor @your_username && echo "no new" || curl -X POST -d "{\"content\":\"New X mentions!\"}" $WEBHOOK_URL
```

**Note**: Since exit code 1 means "new content found", use `||` (OR) to trigger notifications when new mentions arrive.

## China Platform Support

```bash
# 微博
python3 scripts/fetch_china.py --url "https://weibo.com/..."

# B站
python3 scripts/fetch_china.py --url "https://www.bilibili.com/video/..."

# 微信公众号（无需 Camofox）
python3 scripts/fetch_china.py --url "https://mp.weixin.qq.com/s/..."

# CSDN
python3 scripts/fetch_china.py --url "https://blog.csdn.net/..."
```

Auto-detects platform from URL. WeChat articles work without Camofox; others require it.

## Limitations

- Cannot fetch deleted or private tweets
- Depends on FxTwitter / Camofox service availability

## License

MIT