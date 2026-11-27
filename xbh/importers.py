"""Importers for legacy markbook SQLite databases and exported JSON files."""

import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from xbh.db import extract_handle_from_url, snowflake_to_iso, upsert_many


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
) -> dict[str, int]:
    """Import bookmarks from bookmarks_raw.json / bookmarks_filtered.json arrays."""
    src_path = Path(json_path)
    if not src_path.exists():
        raise FileNotFoundError(f"JSON file not found: {src_path}")

    data = json.loads(src_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array of bookmark objects")

    copied = 0
    if copy_assets_from and media_dir:
        media_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("tweet_id") or item.get("id") or "").strip()
        if not tid:
            continue

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

        rec = dict(item)
        rec["tweet_id"] = tid
        if "timestamp" in rec and not rec.get("created_at"):
            rec["created_at"] = rec["timestamp"]
        if local_media:
            rec["local_media"] = local_media
        records.append(rec)

    res = upsert_many(target_conn, records)
    res["assets_copied"] = copied
    return res
