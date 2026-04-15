#!/usr/bin/env python3
"""
Tweet-to-HTML proxy server for Instapaper.

Accepts URLs like: http://yourserver:8080/rauchg/status/2031802691966705728
Fetches the tweet via fetch_tweet.py (FxTwitter API) and returns a clean,
well-structured HTML page with Open Graph meta tags for Instapaper parsing.

Usage:
  python server.py [--port 8080] [--host 0.0.0.0]
"""

import html
import json
import os
import re
import sys
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Add scripts/ to path so we can import fetch_tweet
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from fetch_tweet import fetch_tweet

SITE_URL = os.environ.get("SITE_URL", "http://localhost:8080").rstrip("/")


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

_STYLES = """
body {
    font-family: Georgia, 'Times New Roman', Times, serif;
    max-width: 680px;
    margin: 0 auto;
    padding: 24px 16px;
    color: #1a1a1a;
    line-height: 1.7;
    background: #fff;
}
article { margin-bottom: 40px; }
header { margin-bottom: 20px; border-bottom: 1px solid #e0e0e0; padding-bottom: 12px; }
h1 { font-size: 1.15em; margin: 0 0 4px; color: #111; }
.author-line { color: #666; font-size: 0.9em; }
.author-line a { color: #1da1f2; text-decoration: none; }
.tweet-body { font-size: 1.1em; margin: 20px 0; white-space: pre-wrap; word-wrap: break-word; }
.tweet-body p { margin: 0 0 12px; }
.tweet-body img {
    display: block;
    max-width: 100%;
    height: auto;
    border-radius: 8px;
    margin: 16px 0;
}
.video-thumb {
    display: block;
    max-width: 100%;
    border-radius: 8px;
    margin: 16px 0;
}
.stats {
    color: #888;
    font-size: 0.85em;
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid #eee;
}
.stats span { margin-right: 16px; }
.quoted-tweet {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 12px 16px;
    margin: 16px 0;
    background: #fafafa;
}
.quoted-tweet .qt-author { font-weight: bold; font-size: 0.95em; }
.quoted-tweet .qt-text { margin-top: 6px; font-size: 0.95em; }
.article-content {
    margin: 20px 0;
    font-size: 1.05em;
    line-height: 1.8;
}
.article-content img {
    display: block;
    max-width: 100%;
    height: auto;
    border-radius: 8px;
    margin: 16px 0;
}
.error-page { text-align: center; padding: 60px 20px; }
.error-page h1 { color: #c00; }
footer.site { color: #aaa; font-size: 0.8em; text-align: center; margin-top: 40px; }
"""

_ERROR_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Error</title>
<style>{styles}</style></head>
<body class="error-page">
<h1>Error</h1>
<p>{message}</p>
<p><a href="/">Back</a></p>
</body></html>"""

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Tweet to HTML Proxy</title>
<style>{styles}</style></head>
<body>
<h1>Tweet to HTML Proxy</h1>
<p>Replace <code>x.com</code> in any tweet URL with your server address.</p>
<p>Example: <code>http://yourserver:8080/rauchg/status/2031802691966705728</code></p>
</body></html>"""


def _escape(text):
    return html.escape(str(text), quote=True)


def _render_tweet_html(result, proxy_url=""):
    """Convert fetch_tweet() JSON result to Instapaper-friendly HTML."""
    tweet = result.get("tweet", {})
    if not tweet:
        error_msg = result.get("error", "Tweet not found")
        return _ERROR_HTML.format(styles=_STYLES, message=_escape(error_msg)), 404

    screen_name = tweet.get("screen_name", "")
    author = tweet.get("author", screen_name)
    text = tweet.get("text", "")
    created_at = tweet.get("created_at", "")
    likes = tweet.get("likes", 0)
    retweets = tweet.get("retweets", 0)
    views = tweet.get("views", 0)
    bookmarks = tweet.get("bookmarks", 0)
    replies_count = tweet.get("replies_count", 0)
    is_article = tweet.get("is_article", False)
    article = tweet.get("article")
    media = tweet.get("media", {})
    quote = tweet.get("quote")
    original_url = result.get("url", "")

    # Title
    if is_article and article and article.get("title"):
        title = f"{article['title']} — @{screen_name} on X"
    else:
        title = f"@{screen_name} on X"

    # Description (first 200 chars of text)
    desc = text[:200].replace("\n", " ").strip()
    if is_article and article and article.get("title"):
        desc = article["title"]

    # OG Image
    og_image = ""
    if media and media.get("images"):
        og_image = media["images"][0].get("url", "")
    if not og_image and is_article and article and article.get("images"):
        cover = next((i for i in article["images"] if i.get("type") == "cover"), None)
        og_image = (cover or article["images"][0]).get("url", "")
    if not og_image and quote and quote.get("media", {}).get("images"):
        og_image = quote["media"]["images"][0].get("url", "")

    # --- Build <head> meta tags ---
    og_image_tags = ""
    if og_image:
        og_image_tags = (
            f'<meta property="og:image" content="{_escape(og_image)}" />\n'
            f'<meta name="twitter:image" content="{_escape(og_image)}" />'
        )

    # --- Build <body> content ---
    body_parts = []

    # Header section
    if is_article and article:
        if article.get("title"):
            body_parts.append(f'<h1 class="article-title">{_escape(article["title"])}</h1>')
    else:
        body_parts.append(f'<h1>{_escape(author)}</h1>')

    body_parts.append(
        f'<div class="author-line">'
        f'<a href="https://x.com/{_escape(screen_name)}">@{_escape(screen_name)}</a>'
        f'{(" · " + _escape(author)) if author != screen_name else ""}'
        f' &middot; {_escape(created_at)}'
        f'</div>'
    )

    # Main content
    if is_article and article:
        # Article images (cover + inline)
        if article.get("images"):
            for img in article["images"]:
                url = img.get("url", "")
                if url:
                    body_parts.append(f'<img src="{_escape(url)}" alt="" loading="lazy">')

        # Article full text (may contain markdown-style images)
        full_text = article.get("full_text", "")
        if full_text:
            body_parts.append('<div class="article-content">')
            for block in full_text.split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                # Markdown image: ![](url)
                if block.startswith("![") and block.endswith(")"):
                    img_m = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', block)
                    if img_m:
                        alt = img_m.group(1)
                        src = img_m.group(2)
                        body_parts.append(f'<img src="{_escape(src)}" alt="{_escape(alt)}" loading="lazy">')
                        continue
                body_parts.append(f'<p>{_escape(block)}</p>')
            body_parts.append('</div>')

        wc = article.get("word_count", 0)
        if wc:
            body_parts.append(f'<p class="stats">{_escape(str(wc))} words</p>')
    else:
        # Regular tweet text
        if text:
            body_parts.append('<div class="tweet-body">')
            for p in text.split("\n"):
                p = p.strip()
                if p:
                    p_escaped = _escape(p)
                    p_linked = re.sub(
                        r'(https?://[^\s&lt;]+)',
                        r'<a href="\1">\1</a>',
                        p_escaped,
                    )
                    body_parts.append(f'<p>{p_linked}</p>')
            body_parts.append('</div>')

    # Media (for non-article tweets)
    if not is_article and media:
        if media.get("images"):
            body_parts.append('<div class="tweet-media">')
            for img in media["images"]:
                url = img.get("url", "")
                if url:
                    body_parts.append(f'<img src="{_escape(url)}" alt="Tweet image" loading="lazy">')
            body_parts.append('</div>')
        if media.get("videos"):
            for vid in media["videos"]:
                thumb = vid.get("thumbnail", "")
                if thumb:
                    body_parts.append(f'<img class="video-thumb" src="{_escape(thumb)}" alt="Video thumbnail" loading="lazy">')

    # Quoted tweet
    if quote:
        qt_text = quote.get("text", "")
        qt_author = quote.get("author", "")
        qt_handle = quote.get("screen_name", "")
        body_parts.append('<div class="quoted-tweet">')
        if qt_author:
            body_parts.append(
                f'<div class="qt-author">{_escape(qt_author)} '
                f'@{_escape(qt_handle)}</div>'
            )
        if qt_text:
            body_parts.append(f'<div class="qt-text">{_escape(qt_text)}</div>')
        qt_media = quote.get("media", {})
        if qt_media and qt_media.get("images"):
            for img in qt_media["images"]:
                url = img.get("url", "")
                if url:
                    body_parts.append(f'<img src="{_escape(url)}" alt="" loading="lazy">')
        body_parts.append('</div>')

    # Stats
    body_parts.append(
        f'<div class="stats">'
        f'<span>Likes: {likes}</span>'
        f'<span>Retweets: {retweets}</span>'
        f'<span>Views: {views}</span>'
        f'<span>Bookmarks: {bookmarks}</span>'
        f'<span>Replies: {replies_count}</span>'
        f'</div>'
    )

    body_html = "\n".join(body_parts)

    page_url = _escape(proxy_url) if proxy_url else _escape(original_url)

    # Assemble full page
    page = f"""<!DOCTYPE html>
<html lang="en" prefix="og: https://ogp.me/ns# article: https://ogp.me/ns/article#">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<meta name="description" content="{_escape(desc)}" />
<meta name="author" content="@{_escape(screen_name)}" />
<meta property="og:title" content="{_escape(title)}" />
<meta property="og:description" content="{_escape(desc)}" />
<meta property="og:type" content="article" />
<meta property="og:url" content="{page_url}" />
<meta property="article:author" content="@{_escape(screen_name)}" />
<meta property="article:published_time" content="{_escape(created_at)}" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{_escape(title)}" />
<meta name="twitter:description" content="{_escape(desc)}" />
<meta name="twitter:creator" content="@{_escape(screen_name)}" />
{og_image_tags}
<link rel="canonical" href="{page_url}" />
<style>{_STYLES}</style>
</head>
<body>
<article>
{body_html}
</article>
<footer class="site">tweet-proxy &middot; source: <a href="{_escape(original_url)}">{_escape(original_url)}</a></footer>
</body>
</html>"""
    return page, 200


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

# Route pattern: /<username>/status/<tweet_id>
_TWEET_ROUTE = re.compile(r'^/([a-zA-Z0-9_]{1,15})/status/(\d+)/?$')


class TweetHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        # Index page
        if path == "/" or path == "":
            self._send_html(_INDEX_HTML.format(styles=_STYLES), 200)
            return

        # Health check
        if path == "/health":
            self._send_json({"status": "ok"}, 200)
            return

        # Tweet route
        m = _TWEET_ROUTE.match(path)
        if not m:
            self._send_html(
                _ERROR_HTML.format(styles=_STYLES, message="Invalid URL. Use /&lt;username&gt;/status/&lt;tweet_id&gt;"),
                400,
            )
            return

        username = m.group(1)
        tweet_id = m.group(2)
        tweet_url = f"https://x.com/{username}/status/{tweet_id}"

        self.log_message(f"Fetching: {tweet_url}")

        try:
            result = fetch_tweet(tweet_url)
        except Exception as e:
            self.log_error(f"fetch_tweet error: {e}")
            self._send_html(
                _ERROR_HTML.format(styles=_STYLES, message=f"Internal error: {_escape(str(e))}"),
                500,
            )
            return

        proxy_url = f"{SITE_URL}{path}"
        page_html, status_code = _render_tweet_html(result, proxy_url=proxy_url)
        self._send_html(page_html, status_code)

    def _send_html(self, content, status=200):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        sys.stderr.write(f"[tweet-proxy] {args[0] if args else format}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tweet-to-HTML proxy for Instapaper")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args()

    import socketserver
    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True

    server = ReusableHTTPServer((args.host, args.port), TweetHandler)
    print(f"[tweet-proxy] Listening on {args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[tweet-proxy] Shutting down.", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    main()
