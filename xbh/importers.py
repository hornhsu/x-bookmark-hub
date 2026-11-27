"""Importers for legacy markbook SQLite databases and exported JSON files."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any

from xbh.db import extract_handle_from_url, snowflake_to_iso, upsert_many

MEDIA_BY_ID_RE = re.compile(r"^(\d+)[-_]\d+\.(jpg|jpeg|png|webp|gif)$", re.I)


def _normalize_pbs_url(url: str) -> str:
    """Append default format query to extensionless pbs.twimg.com/media URLs."""
    u = (url or "").strip()
    if "pbs.twimg.com/media/" in u and "?" not in u and not re.search(r"\.(jpg|jpeg|png|webp|gif)$", u, re.I):
        return f"{u}?format=jpg&name=medium"
    return u


def _index_media_by_id(media_by_id_dir: Path | None) -> dict[str, list[Path]]:
    """Index files named <tweet_id>-<n>.<ext> or <tweet_id>_<n>.<ext> by tweet_id."""
    index: dict[str, list[Path]] = {}
    if not media_by_id_dir:
        return index
    root = Path(media_by_id_dir)
    if not root.exists() or not root.is_dir():
        return index
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        m = MEDIA_BY_ID_RE.match(p.name)
        if m:
            index.setdefault(m.group(1), []).append(p)
    return index


def import_markbook_db(source_db: Path, target_conn: sqlite3.Connection) -> dict[str, int]:
    """Import rows from a legacy markbook.db into the unified X Bookmark Hub database."""
    src_path = Path(source_db)
    if not src_path.exists():
        raise FileNotFoundError(f"Source database not found: {src_path}")

    src_conn = sqlite3.connect(str(src_path))
    src_conn.row_factory = sqlite3.Row
    try:
        rows = src_conn.execute("SELECT * FROM bookmarks ORDER BY id ASC").fetchall()
    finally:
        src_conn.close()

    records: list[dict[str, Any]] = []
    for r in rows:
        tid = str(r["tweet_id"] or "").strip()
        if not tid:
            continue
        url = (r["url"] or "").strip()
        handle = (r["author_handle"] or "").strip() or extract_handle_from_url(url)
        created_at = (r["created_at"] or "").strip() or (snowflake_to_iso(tid) or "")
        media_urls = []
        if r["media_urls"]:
            try:
                parsed = json.loads(r["media_urls"])
                if isinstance(parsed, list):
                    media_urls = parsed
            except json.JSONDecodeError:
                pass
        records.append(
            {
                "tweet_id": tid,
                "url": url,
                "text": r["text"] or "",
                "author_name": r["author_name"] or "",
                "author_handle": handle,
                "created_at": created_at,
                "media_urls": media_urls,
                "like_count": r["like_count"] or 0,
                "retweet_count": r["retweet_count"] or 0,
                "reply_count": r["reply_count"] or 0,
                "category": r["category"] or "",
            }
        )

    return upsert_many(target_conn, records)


def import_json_bookmarks(
    json_path: Path,
    target_conn: sqlite3.Connection,
    copy_assets_from: Path | None = None,
    media_dir: Path | None = None,
    media_by_id_dir: Path | None = None,
) -> dict[str, int]:
    """Import bookmarks from bookmarks_raw.json, bookmarks_filtered.json, or OpenCode JSON exports."""
    src_path = Path(json_path)
    if not src_path.exists():
        raise FileNotFoundError(f"JSON file not found: {src_path}")

    data = json.loads(src_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array of bookmark objects")

    copied = 0
    if (copy_assets_from or media_by_id_dir) and media_dir:
        media_dir.mkdir(parents=True, exist_ok=True)

    media_id_index = _index_media_by_id(media_by_id_dir)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("tweet_id") or item.get("id") or "").strip()
        if not tid:
            continue

        is_opencode = ("approx" in item) or ("fx" in item) or ("ym" in item)
        rec = dict(item)
        rec["tweet_id"] = tid

        # Map OpenCode author/handle/links/media fields when canonical fields are absent
        if not rec.get("author_handle") and rec.get("handle"):
            rec["author_handle"] = rec["handle"]
        if not rec.get("author_name") and rec.get("author"):
            rec["author_name"] = rec["author"]

        if not rec.get("created_at"):
            if rec.get("approx"):
                rec["created_at"] = snowflake_to_iso(tid) or rec.get("timestamp") or rec.get("when") or ""
            else:
                rec["created_at"] = rec.get("timestamp") or snowflake_to_iso(tid) or rec.get("when") or ""

        if not rec.get("external_links") and isinstance(rec.get("links"), list):
            rec["external_links"] = [{"text": u, "url": u} for u in rec["links"] if isinstance(u, str) and u]

        media_urls: list[str] = [
            _normalize_pbs_url(u)
            for u in (rec.get("media_urls") or rec.get("media") or [])
            if isinstance(u, str) and u.strip()
        ]
        video_urls: list[str] = list(rec.get("video_urls") or [])
        has_video = bool(rec.get("has_video"))

        # Merge FxTwitter enrichment data from OpenCode video-enriched.json
        fx = rec.get("fx")
        if isinstance(fx, dict):
            err = str(fx.get("error") or "")
            if not err:
                full_text = (fx.get("fullText") or "").strip()
                if full_text:
                    rec["text"] = full_text
                    rec["is_truncated"] = 0
                rec["like_count"] = max(int(rec.get("like_count") or 0), int(fx.get("likes") or 0))
                rec["retweet_count"] = max(int(rec.get("retweet_count") or 0), int(fx.get("retweets") or 0))
                rec["reply_count"] = max(int(rec.get("reply_count") or 0), int(fx.get("replies") or 0))
                rec["view_count"] = max(int(rec.get("view_count") or 0), int(fx.get("views") or 0))
                quote = fx.get("quote")
                if isinstance(quote, dict):
                    if quote.get("text") and not rec.get("quoted_text"):
                        rec["quoted_text"] = quote["text"]
                    if quote.get("author") and not rec.get("quoted_author"):
                        rec["quoted_author"] = quote["author"]
                for fm in fx.get("media") or []:
                    if not isinstance(fm, dict):
                        continue
                    mtype = (fm.get("type") or "").lower()
                    if mtype == "photo" and fm.get("url"):
                        u = _normalize_pbs_url(fm["url"])
                        if u not in media_urls:
                            media_urls.append(u)
                    elif mtype in {"video", "gif"}:
                        has_video = True
                        if fm.get("thumb"):
                            u = _normalize_pbs_url(fm["thumb"])
                            if u not in media_urls:
                                media_urls.append(u)
                        if fm.get("url") and fm["url"] not in video_urls:
                            video_urls.append(fm["url"])
                rec["enriched_at"] = rec.get("enriched_at") or now_iso
                rec["enrich_status"] = "ok"
            elif "404" in err or "401" in err:
                rec["enrich_status"] = "unavailable"

        if media_urls:
            rec["media_urls"] = list(dict.fromkeys(media_urls))
        if video_urls:
            rec["video_urls"] = list(dict.fromkeys(video_urls))
        if has_video:
            rec["has_video"] = 1

        # Avoid importing lowercase OpenCode model slugs; let classifier assign canonical names
        if is_opencode:
            rec.pop("models", None)

        local_media: list[str] = []
        for rel in item.get("local_media") or []:
            rel_str = str(rel).replace("\\", "/").lstrip("./")
            if copy_assets_from and media_dir and not rel_str.startswith("http"):
                src_file = (Path(copy_assets_from) / rel_str).resolve()
                if src_file.exists() and src_file.is_file():
                    dst_name = src_file.name
                    dst_file = media_dir / dst_name
                    if not dst_file.exists():
                        shutil.copy2(src_file, dst_file)
                        copied += 1
                    local_media.append(f"media/{dst_name}")
                    continue
            if rel_str.startswith("media/"):
                local_media.append(rel_str)

        if not local_media and tid in media_id_index and media_dir:
            for src_file in media_id_index[tid]:
                dst_file = media_dir / src_file.name
                if not dst_file.exists():
                    shutil.copy2(src_file, dst_file)
                    copied += 1
                local_media.append(f"media/{src_file.name}")

        if local_media:
            rec["local_media"] = local_media
        records.append(rec)

    res = upsert_many(target_conn, records)
    res["assets_copied"] = copied
    return res
