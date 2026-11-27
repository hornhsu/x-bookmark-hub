"""FxTwitter metadata enricher and local media thumbnail cacher."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

from xbh.db import _json_list

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def fetch_fxtwitter(tweet_id: str, timeout: int = 15) -> tuple[str, dict | None]:
    """Fetch full tweet JSON from api.fxtwitter.com.

    Returns (status, payload) where status is 'ok', 'unavailable', 'rate_limited', or 'error'.
    """
    url = f"https://api.fxtwitter.com/i/status/{tweet_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "x-bookmark-hub/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("tweet"):
                return "ok", data["tweet"]
            return "unavailable", None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "rate_limited", None
        if e.code in (401, 403, 404):
            return "unavailable", None
        return "error", None
    except Exception:
        return "error", None


def enrich_bookmarks(
    conn: sqlite3.Connection,
    since: str = "",
    limit: int = 100,
    only_missing_or_truncated: bool = True,
    delay: float = 0.35,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict[str, int]:
    """Enrich empty or truncated bookmarks using FxTwitter API."""
    where = ["enrich_status != 'unavailable'"]
    params: list = []
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if only_missing_or_truncated:
        where.append("(length(trim(text)) = 0 OR is_truncated = 1 OR enriched_at = '')")

    sql = f"SELECT tweet_id FROM bookmarks WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    ids = [r[0] for r in rows]

    ok = 0
    unavailable = 0
    errors = 0
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for idx, tid in enumerate(ids, start=1):
        status = "error"
        tweet = None
        for attempt in range(3):
            status, tweet = fetch_fxtwitter(tid)
            if status == "rate_limited":
                time.sleep(2.5 * (attempt + 1))
                continue
            break

        if status == "ok" and tweet:
            author = tweet.get("author") or {}
            media = tweet.get("media") or {}
            photos = [p.get("url") for p in (media.get("photos") or []) if p.get("url")]
            videos = media.get("videos") or []
            vid_thumbs = [v.get("thumbnail_url") for v in videos if v.get("thumbnail_url")]
            vid_urls = [v.get("url") for v in videos if v.get("url")]
            media_urls = photos + [u for u in vid_thumbs if u not in photos]
            quote = tweet.get("quote") or {}
            q_author = (quote.get("author") or {}).get("screen_name") or ""
            handle = author.get("screen_name") or ""
            if handle and not handle.startswith("@"):
                handle = f"@{handle}"
            if q_author and not q_author.startswith("@"):
                q_author = f"@{q_author}"

            conn.execute(
                """UPDATE bookmarks SET
                    author_name = COALESCE(NULLIF(?, ''), author_name),
                    author_handle = COALESCE(NULLIF(?, ''), author_handle),
                    text = CASE WHEN length(?) > 0 THEN ? ELSE text END,
                    quoted_text = COALESCE(NULLIF(?, ''), quoted_text),
                    quoted_author = COALESCE(NULLIF(?, ''), quoted_author),
                    media_urls = CASE WHEN ? != '[]' THEN ? ELSE media_urls END,
                    video_urls = CASE WHEN ? != '[]' THEN ? ELSE video_urls END,
                    has_video = MAX(has_video, ?),
                    like_count = MAX(like_count, ?),
                    retweet_count = MAX(retweet_count, ?),
                    reply_count = MAX(reply_count, ?),
                    view_count = MAX(view_count, ?),
                    is_truncated = 0,
                    enriched_at = ?,
                    enrich_status = 'ok',
                    updated_at = datetime('now')
                WHERE tweet_id = ?""",
                (
                    author.get("name") or "",
                    handle,
                    tweet.get("text") or "",
                    tweet.get("text") or "",
                    quote.get("text") or "",
                    q_author,
                    json.dumps(media_urls, ensure_ascii=False),
                    json.dumps(media_urls, ensure_ascii=False),
                    json.dumps(vid_urls, ensure_ascii=False),
                    json.dumps(vid_urls, ensure_ascii=False),
                    1 if videos else 0,
                    int(tweet.get("likes") or 0),
                    int(tweet.get("retweets") or 0),
                    int(tweet.get("replies") or 0),
                    int(tweet.get("views") or 0),
                    now_iso,
                    tid,
                ),
            )
            ok += 1
        elif status == "unavailable":
            conn.execute(
                "UPDATE bookmarks SET enrich_status = 'unavailable', enriched_at = ?, updated_at = datetime('now') WHERE tweet_id = ?",
                (now_iso, tid),
            )
            unavailable += 1
        else:
            errors += 1

        if idx % 20 == 0:
            conn.commit()
        if progress_cb:
            progress_cb(idx, len(ids), tid)
        if delay > 0 and idx < len(ids):
            time.sleep(delay)

    conn.commit()
    return {"targeted": len(ids), "enriched": ok, "unavailable": unavailable, "errors": errors}


def _infer_ext(url: str, content_type: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    fmt = (qs.get("format", [""])[0] or "").lower()
    if fmt in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "jpg" if fmt == "jpeg" else fmt
    suffix = Path(parsed.path).suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "jpg" if suffix == "jpeg" else suffix
    ct = (content_type or "").lower()
    if "png" in ct:
        return "png"
    if "webp" in ct:
        return "webp"
    if "gif" in ct:
        return "gif"
    return "jpg"


def _download_one(media_dir: Path, tweet_id: str, idx: int, url: str) -> tuple[str, int, str | None]:
    existing = list(media_dir.glob(f"{tweet_id}_{idx}.*"))
    if existing and existing[0].stat().st_size > 0:
        return tweet_id, idx, f"media/{existing[0].name}"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://x.com/"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if not data:
                return tweet_id, idx, None
            ext = _infer_ext(url, resp.headers.get("Content-Type", ""))
            fname = f"{tweet_id}_{idx}.{ext}"
            (media_dir / fname).write_bytes(data)
            return tweet_id, idx, f"media/{fname}"
    except Exception:
        return tweet_id, idx, None


def cache_media(
    conn: sqlite3.Connection,
    media_dir: Path,
    since: str = "",
    limit: int = 300,
    max_workers: int = 10,
) -> dict[str, int]:
    """Download media_urls into media_dir and persist relative media/<file> paths."""
    media_dir.mkdir(parents=True, exist_ok=True)
    where = ["media_urls != '[]'", "local_media = '[]'"]
    params: list = []
    if since:
        where.append("created_at >= ?")
        params.append(since)

    rows = conn.execute(
        f"SELECT tweet_id, media_urls FROM bookmarks WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?",
        [*params, int(limit)],
    ).fetchall()

    jobs = []
    results_map: dict[str, list[str | None]] = {}
    for r in rows:
        tid = r["tweet_id"]
        urls = _json_list(r["media_urls"])
        results_map[tid] = [None] * len(urls)
        for idx, u in enumerate(urls):
            jobs.append((tid, idx, u))

    downloaded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_download_one, media_dir, tid, idx, u) for tid, idx, u in jobs]
        for fut in as_completed(futs):
            tid, idx, rel = fut.result()
            if rel:
                results_map[tid][idx] = rel
                downloaded += 1
            else:
                failed += 1

    updated_rows = 0
    for tid, paths in results_map.items():
        clean = [p for p in paths if p]
        if clean:
            conn.execute(
                "UPDATE bookmarks SET local_media = ?, updated_at = datetime('now') WHERE tweet_id = ?",
                (json.dumps(clean, ensure_ascii=False), tid),
            )
            updated_rows += 1
    conn.commit()
    return {
        "targeted_tweets": len(rows),
        "updated_tweets": updated_rows,
        "images_cached": downloaded,
        "images_failed": failed,
    }
