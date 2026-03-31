import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(PROJECT_ROOT))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


X_TARGETS_CONFIG_PATH = PROJECT_ROOT / "config" / "x_targets.json"
# The extractor writes a JSON blob back into the DOM so Python can read a
# deterministic payload instead of scraping the entire timeline HTML with regexes.
EXPORT_SCRIPT_ID = "__c4a_x_latest_posts__"
EXPORT_PATTERN = re.compile(
    rf'<script[^>]+id="{EXPORT_SCRIPT_ID}"[^>]*>(?P<payload>.*?)</script>',
    re.DOTALL,
)
WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_x_source_link(value: str) -> str:
    # Normalize any account URL to its canonical profile root. This makes config
    # matching and source identity stable even if the user pastes a longer variant.
    parsed = urlparse(value.strip())
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError(f"Invalid X account URL: {value}")
    handle = path_parts[0].lstrip("@")
    if not handle:
        raise ValueError(f"Invalid X account URL: {value}")
    return f"https://x.com/{handle}"


def extract_handle(source_link: str) -> str:
    # Handles are derived from the normalized profile URL so we filter timeline
    # posts to the exact target account even if the page contains replies or embeds.
    return normalize_x_source_link(source_link).rsplit("/", 1)[-1]


def load_json_file(path: Path) -> Any:
    # Small helper used for config, cookies, and storage-state files.
    return json.loads(path.read_text(encoding="utf-8"))


def load_targets_config() -> dict[str, Any]:
    # Missing config is valid; it just means no X accounts are configured yet.
    if not X_TARGETS_CONFIG_PATH.exists():
        return {"defaults": {}, "auth": {}, "accounts": []}
    payload = load_json_file(X_TARGETS_CONFIG_PATH)
    if not isinstance(payload, dict):
        raise ValueError(f"{X_TARGETS_CONFIG_PATH} must contain a JSON object.")
    payload.setdefault("defaults", {})
    payload.setdefault("auth", {})
    payload.setdefault("accounts", [])
    return payload


def resolve_config_path(value: str) -> Path:
    # Config paths may be absolute or project-relative. Resolving them here keeps
    # the rest of the scraper logic free of path-joining details.
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def get_source_runtime_config(source_link: str) -> dict[str, Any]:
    # Merge three layers of config:
    # 1. defaults shared by all X sources
    # 2. auth shared by all X sources
    # 3. per-account overrides such as max_posts
    normalized_url = normalize_x_source_link(source_link)
    config = load_targets_config()
    defaults = config.get("defaults", {})
    auth = config.get("auth", {})
    accounts = config.get("accounts", [])

    account_config = {}
    for account in accounts:
        if not isinstance(account, dict):
            continue
        url = str(account.get("url", "")).strip()
        if url and normalize_x_source_link(url) == normalized_url:
            account_config = account
            break

    merged = {
        "defaults": defaults if isinstance(defaults, dict) else {},
        "auth": auth if isinstance(auth, dict) else {},
        "account": account_config,
    }
    return merged


def get_html_payload(result: Any) -> str:
    # Crawl4AI may expose the returned HTML on different fields depending on mode.
    # We prefer the richest HTML available and fail only if none exists.
    for field_name in ("html", "cleaned_html", "fit_html"):
        value = getattr(result, field_name, None)
        if value:
            return value
    raise RuntimeError("Crawl4AI did not return HTML content.")


def load_seen_ids(state_file: Path) -> set[str]:
    # Existing runner behavior is preserved: unseen filtering is still driven by a
    # per-source state file in addition to the new SQLite persistence layer.
    if not state_file.exists():
        return set()

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    return set(payload.get("seen_ids", []))


def save_state(state_file: Path, items: list[dict[str, Any]]) -> None:
    # Save the latest visible item IDs so default runs return only newly observed posts.
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "seen_ids": sorted(item["id"] for item in items),
        "total_items": len(items),
    }
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_text(value: str) -> str:
    # X text often contains HTML entities or irregular whitespace after DOM reads.
    return WHITESPACE_PATTERN.sub(" ", html.unescape(value or "")).strip()


def shorten_title(value: str, limit: int = 120) -> str:
    # The output contract requires a title; for posts we derive it from the body.
    normalized = normalize_text(value)
    if not normalized:
        return ""
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def make_browser_config(runtime_config: dict[str, Any]) -> tuple[BrowserConfig, str]:
    # Build one Crawl4AI BrowserConfig from config/x_targets.json. Auth precedence is:
    # user_data_dir > storage_state_path > cookies_path > anonymous.
    defaults = runtime_config["defaults"]
    auth = runtime_config["auth"]

    browser_kwargs: dict[str, Any] = {
        "browser_type": str(defaults.get("browser_type", "chromium")),
        "chrome_channel": str(defaults.get("chrome_channel", "chromium")),
        "headless": bool(defaults.get("headless", True)),
        "verbose": False,
        "viewport_width": int(defaults.get("viewport_width", 1440)),
        "viewport_height": int(defaults.get("viewport_height", 1200)),
        "enable_stealth": bool(defaults.get("enable_stealth", True)),
        "text_mode": False,
        "light_mode": False,
    }

    auth_mode = "anonymous"
    storage_state_path = str(auth.get("storage_state_path", "")).strip()
    cookies_path = str(auth.get("cookies_path", "")).strip()
    user_data_dir = str(auth.get("user_data_dir", "")).strip()

    if user_data_dir:
        # Persistent context is the most browser-like option and lets Playwright
        # reuse a regular logged-in profile directory.
        browser_kwargs["use_persistent_context"] = True
        browser_kwargs["user_data_dir"] = str(resolve_config_path(user_data_dir))
        auth_mode = "persistent_context"
    elif storage_state_path:
        # storage_state is the cleanest portable auth artifact if exported from Playwright.
        browser_kwargs["storage_state"] = load_json_file(resolve_config_path(storage_state_path))
        auth_mode = "storage_state"
    elif cookies_path:
        # Cookie-array mode supports direct injection of a regular user's session cookies.
        cookies_payload = load_json_file(resolve_config_path(cookies_path))
        if not isinstance(cookies_payload, list):
            raise ValueError("cookies_path must point to a JSON array of Playwright-style cookies.")
        browser_kwargs["cookies"] = cookies_payload
        auth_mode = "cookies"

    return BrowserConfig(**browser_kwargs), auth_mode


def summarize_auth_config(runtime_config: dict[str, Any]) -> dict[str, Any]:
    # Validation commands use this summary to report what auth mode the scraper
    # intends to use and whether the referenced auth artifact exists on disk.
    auth = runtime_config["auth"]
    auth_mode = "anonymous"
    resolved_path = ""

    user_data_dir = str(auth.get("user_data_dir", "")).strip()
    storage_state_path = str(auth.get("storage_state_path", "")).strip()
    cookies_path = str(auth.get("cookies_path", "")).strip()

    if user_data_dir:
        auth_mode = "persistent_context"
        resolved_path = str(resolve_config_path(user_data_dir))
    elif storage_state_path:
        auth_mode = "storage_state"
        resolved_path = str(resolve_config_path(storage_state_path))
    elif cookies_path:
        auth_mode = "cookies"
        resolved_path = str(resolve_config_path(cookies_path))

    path_exists = Path(resolved_path).exists() if resolved_path else False
    return {
        "auth_mode": auth_mode,
        "resolved_path": resolved_path,
        "path_exists": path_exists,
    }


def make_extraction_js(handle: str, max_posts: int) -> str:
    # We intentionally extract structured data in the browser context because X is
    # highly dynamic. Walking rendered DOM nodes is more resilient than trying to
    # reverse-engineer all timeline data from raw HTML strings alone.
    handle_json = json.dumps(handle.lower())
    return f"""
(() => {{
  const handle = {handle_json};
  const maxPosts = {max_posts};
  const seen = new Set();
  const posts = [];
  // Timeline cards for posts currently render as article[data-testid="tweet"].
  const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));

  for (const article of articles) {{
    // Find the canonical status URL inside each card.
    const anchors = Array.from(article.querySelectorAll('a[href*="/status/"]'));
    const statusAnchor = anchors.find((anchor) => /^\\/[^/?#]+\\/status\\/\\d+/.test(anchor.getAttribute('href') || ''));
    if (!statusAnchor) {{
      continue;
    }}

    const href = statusAnchor.getAttribute('href') || '';
    const match = href.match(/^\\/([^/?#]+)\\/status\\/(\\d+)/);
    if (!match) {{
      continue;
    }}

    const authorHandle = (match[1] || '').toLowerCase();
    const postId = match[2];
    // Filter to the target account only. This avoids cross-account noise from
    // pinned cards, replies, embeds, or other timeline elements.
    if (authorHandle !== handle) {{
      continue;
    }}

    const postUrl = new URL(href, location.origin).toString();
    if (seen.has(postUrl)) {{
      continue;
    }}
    seen.add(postUrl);

    // tweetText nodes contain the rendered text fragments we want for title/description.
    const text = Array.from(article.querySelectorAll('[data-testid="tweetText"]'))
      .map((node) => (node.innerText || '').trim())
      .filter(Boolean)
      .join('\\n')
      .trim();

    // Capture a few lightweight fields that map cleanly to the project contract.
    const timestamp = article.querySelector('time')?.getAttribute('datetime') || '';
    const image = article.querySelector('img[src*="pbs.twimg.com/media"]')?.getAttribute('src') || '';
    const metrics = Array.from(article.querySelectorAll('[role="group"] [data-testid]'))
      .map((node) => (node.innerText || '').trim())
      .filter(Boolean);

    posts.push({{
      id: `tweet:${{postUrl}}`,
      post_id: postId,
      handle: authorHandle,
      text,
      published_date: timestamp,
      url: postUrl,
      image,
      metrics_text: metrics.join(' | '),
      source_page: location.href
    }});

    if (posts.length >= maxPosts) {{
      break;
    }}
  }}

  let script = document.getElementById('{EXPORT_SCRIPT_ID}');
  if (!script) {{
    // Injecting JSON into a script tag lets Python recover a precise payload from
    // the final HTML returned by Crawl4AI.
    script = document.createElement('script');
    script.type = 'application/json';
    script.id = '{EXPORT_SCRIPT_ID}';
    document.body.appendChild(script);
  }}
  script.textContent = JSON.stringify({{
    handle,
    page_url: location.href,
    collected_at_utc: new Date().toISOString(),
    posts
  }});
}})();
""".strip()


def make_run_config(timeout_ms: int, handle: str, max_posts: int) -> CrawlerRunConfig:
    # This run config is tuned for a dynamic authenticated profile page:
    # bypass cache, wait for tweet cards, then execute the DOM extraction script.
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout_ms,
        verbose=False,
        wait_until="domcontentloaded",
        wait_for='css:article[data-testid="tweet"]',
        wait_for_timeout=timeout_ms,
        delay_before_return_html=2.0,
        js_code=make_extraction_js(handle=handle, max_posts=max_posts),
        remove_overlay_elements=True,
        remove_consent_popups=True,
        simulate_user=True,
        override_navigator=True,
        process_iframes=False,
    )


def make_validation_run_config(timeout_ms: int) -> CrawlerRunConfig:
    # Validation is intentionally lighter than full scraping. We just want enough
    # page load behavior to detect whether auth and the profile timeline appear valid.
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout_ms,
        verbose=False,
        wait_until="domcontentloaded",
        delay_before_return_html=2.0,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        simulate_user=True,
        override_navigator=True,
    )


def parse_exported_posts(raw_html: str) -> list[dict[str, Any]]:
    # Read the injected script payload back out of the rendered document.
    match = EXPORT_PATTERN.search(raw_html)
    if not match:
        raise RuntimeError("Unable to locate exported X post payload in the rendered page.")

    payload = json.loads(html.unescape(match.group("payload")))
    posts = payload.get("posts", [])
    if not isinstance(posts, list):
        raise RuntimeError("Exported X post payload is malformed.")
    return posts


def normalize_posts(posts: list[dict[str, Any]], source_link: str) -> list[dict[str, Any]]:
    # Convert the browser-side payload into the shared scraper output contract used
    # by the rest of the project.
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for post in posts:
        if not isinstance(post, dict):
            continue

        item_id = str(post.get("id", "")).strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        text = normalize_text(str(post.get("text", "")))
        handle = normalize_text(str(post.get("handle", "")))

        # Stable URL-based ids make both file-based and SQLite dedupe predictable.
        items.append(
            {
                "id": item_id,
                "section": "posts",
                "type": "Post",
                "title": shorten_title(text) or f"Latest post from @{handle}",
                "description": text,
                "published_date": normalize_text(str(post.get("published_date", ""))),
                "url": normalize_text(str(post.get("url", ""))),
                "image": normalize_text(str(post.get("image", ""))),
                "read_time": "",
                "button_text": "View Post",
                "external": True,
                "source_page": source_link,
            }
        )

    return sorted(items, key=lambda item: (item.get("published_date", ""), item.get("id", "")), reverse=True)


def inspect_profile_html(raw_html: str, handle: str) -> dict[str, Any]:
    # Validation uses a few heuristic checks so the CLI can quickly tell the user
    # whether the profile likely loaded with an authenticated timeline or hit a wall.
    lowered_html = raw_html.lower()
    lowered_handle = handle.lower()

    return {
        "has_tweet_articles": 'data-testid="tweet"' in lowered_html,
        "mentions_handle": f"/{lowered_handle}/status/" in lowered_html or f"@{lowered_handle}" in lowered_html,
        "login_wall_detected": any(
            marker in lowered_html
            for marker in (
                "sign in to x",
                "log in to x",
                "create an account",
                "sign up for x",
            )
        ),
    }


async def collect_items(source_link: str, timeout_ms: int, logger: logging.Logger) -> list[dict[str, Any]]:
    # Main collection flow:
    # 1. read config
    # 2. build authenticated browser config
    # 3. fetch the live profile page
    # 4. parse injected JSON payload
    # 5. normalize and sort items
    runtime_config = get_source_runtime_config(source_link)
    account_config = runtime_config["account"]
    defaults = runtime_config["defaults"]
    handle = extract_handle(source_link)
    max_posts = int(account_config.get("max_posts") or defaults.get("max_posts") or 10)

    logger.info("Starting Crawl4AI fetch for %s", source_link)
    logger.info("X scraper is intentionally limited to the latest visible posts on the profile timeline.")

    browser_config, auth_mode = make_browser_config(runtime_config)
    logger.info("Using auth mode '%s' for @%s.", auth_mode, handle)

    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        result = await crawler.arun(url=source_link, config=make_run_config(timeout_ms, handle, max_posts))
        if not result.success:
            raise RuntimeError(f"Failed to crawl {source_link}: {result.error_message}")

        posts = parse_exported_posts(get_html_payload(result))
        items = normalize_posts(posts, source_link=source_link)
        logger.info("Collected %s visible latest post(s) for @%s.", len(items), handle)
        return items


async def validate_x_source(source_link: str, timeout_ms: int = 90000) -> dict[str, Any]:
    # Separate validation path used by the CLI helper. It intentionally returns a
    # structured status payload instead of raising for expected setup problems.
    runtime_config = get_source_runtime_config(source_link)
    handle = extract_handle(source_link)
    auth_summary = summarize_auth_config(runtime_config)
    try:
        browser_config, auth_mode = make_browser_config(runtime_config)
    except Exception as exc:
        # Missing/invalid auth files should be shown as a user-friendly validation result.
        return {
            "ok": False,
            "source_link": source_link,
            "handle": handle,
            "auth_mode": auth_summary["auth_mode"],
            "auth_path": auth_summary["resolved_path"],
            "auth_path_exists": auth_summary["path_exists"],
            "error": str(exc),
        }

    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        result = await crawler.arun(url=source_link, config=make_validation_run_config(timeout_ms))
        if not result.success:
            # Crawl-level failures are returned to the caller for easy CLI display.
            return {
                "ok": False,
                "source_link": source_link,
                "handle": handle,
                "auth_mode": auth_mode,
                "auth_path": auth_summary["resolved_path"],
                "auth_path_exists": auth_summary["path_exists"],
                "error": result.error_message or "Crawl failed.",
            }

        html_payload = get_html_payload(result)
        inspection = inspect_profile_html(html_payload, handle)
        # We consider validation successful only if the page appears to have tweet
        # cards and does not clearly look like a sign-in wall.
        ok = inspection["has_tweet_articles"] and not inspection["login_wall_detected"]

        return {
            "ok": ok,
            "source_link": source_link,
            "handle": handle,
            "auth_mode": auth_mode,
            "auth_path": auth_summary["resolved_path"],
            "auth_path_exists": auth_summary["path_exists"],
            "current_url": getattr(result, "url", source_link) or source_link,
            "has_tweet_articles": inspection["has_tweet_articles"],
            "mentions_handle": inspection["mentions_handle"],
            "login_wall_detected": inspection["login_wall_detected"],
            "html_length": len(html_payload),
            "error": "" if ok else "Profile loaded but authenticated tweet timeline was not confidently detected.",
        }


def validate_x_source_sync(source_link: str, timeout_ms: int = 90000) -> dict[str, Any]:
    # Sync wrapper so the CLI can call validation from a normal non-async command.
    return asyncio.run(validate_x_source(source_link=source_link, timeout_ms=timeout_ms))


def scrape_source(
    *,
    source: Any,
    source_dir: Path,
    timeout_ms: int,
    all_items: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    # This is the standard project scraper entrypoint used by run_sources.py.
    state_file = source_dir / "state" / "seen_items.json"
    seen_ids = load_seen_ids(state_file)
    current_items = asyncio.run(collect_items(source.link, timeout_ms=timeout_ms, logger=logger))

    if all_items:
        selected_items = current_items
    else:
        # Default runner behavior returns only newly seen items for the source.
        selected_items = [item for item in current_items if item["id"] not in seen_ids]

    save_state(state_file, current_items)

    payload = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "namespace": source.folder_name,
        "source_name": source.name,
        "source_link": source.link,
        "returned_count": len(selected_items),
        "total_current_count": len(current_items),
        "items": selected_items,
    }
    logger.info(
        "Returning %s item(s); %s current item(s) recorded in state.",
        payload["returned_count"],
        payload["total_current_count"],
    )
    return payload
