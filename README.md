# x-tweet-fetcher

Fetch tweets from X/Twitter **without login or API keys**.

An [OpenClaw](https://github.com/openclaw/openclaw) skill. Zero dependencies, zero configuration.

## What It Can Fetch

### X/Twitter
| Content | Support | Requirement |
|---------|---------|-------------|
| Regular tweets | ✅ Full text + stats | None |
| Long tweets | ✅ Full text | None |
| X Articles (long-form) | ✅ Complete article | None |
| Quoted tweets | ✅ Included | None |
| Stats (likes/RT/views) | ✅ Included | None |
| **Reply comments** | ⚠️ With comments | **Camofox required** |
| **User timeline** | ⚠️ With timeline | **Camofox required** |

### China Platforms (NEW)
| Platform | Content | Requirement |
|----------|---------|-------------|
| **微博 Weibo** | ✅ Posts, comments, stats | Camofox |
| **B站 Bilibili** | ✅ Video info, UP, views, likes | Camofox |
| **CSDN** | ✅ Articles, code blocks, stats | Camofox |
| **微信公众号 WeChat** | ✅ Full articles, images | None (direct HTTP) |

## Quick Start

### Basic Usage (No Dependencies)

```bash
# JSON output
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456"

# Human readable
python3 scripts/fetch_tweet.py --url "https://x.com/user/status/123456" --text-only

# Pretty JSON
python3weet.py --url scripts/fetch_t "https://x.com/user/status/123456" --pretty
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

## China Platform Fetcher

Fetch content from Chinese platforms with automatic platform detection.

```bash
# Weibo post
python3 scripts/fetch_china.py --url "https://weibo.com/user/post" --pretty

# Bilibili video
python3 scripts/fetch_china.py --url "https://www.bilibili.com/video/BVxxxxxx" --pretty

# CSDN article
python3 scripts/fetch_china.py --url "https://blog.csdn.net/user/article/details/xxx" --pretty

# WeChat article (no Camofox needed!)
python3 scripts/fetch_china.py --url "https://mp.weixin.qq.com/s/xxxxx" --pretty

# Markdown output with YAML frontmatter
python3 scripts/fetch_china.py --url "<URL>" --markdown

# Human readable text
python3 scripts/fetch_china.py --url "<URL>" --text-only

# English output
python3 scripts/fetch_china.py --url "<URL>" --lang en
```

### Supported Output Formats

- **JSON** (default): Structured data with stats, media, metadata
- **Markdown** (`--markdown`): Clean markdown with YAML frontmatter
- **Text** (`--text-only`): Human-readable plain text

## Limitations

- Cannot fetch deleted or private tweets
- Depends on FxTwitter / Camofox service availability
- China platforms: Comments may require login (graceful degradation)
- WeChat: Only works with valid article links (not expired short links)

## License

MIT