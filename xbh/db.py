"""SQLite + FTS5 storage layer for X Bookmark Hub."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bookmarks.db"
TWITTER_EPOCH_MS = 1288834974657

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    author_name TEXT DEFAULT '',
    author_handle TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    text TEXT DEFAULT '',
    quoted_text TEXT DEFAULT '',
    quoted_author TEXT DEFAULT '',
    media_urls TEXT DEFAULT '[]',
    local_media TEXT DEFAULT '[]',
    video_urls TEXT DEFAULT '[]',
    has_video INTEGER DEFAULT 0,
    external_links TEXT DEFAULT '[]',
    like_count INTEGER DEFAULT 0,
    retweet_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    models TEXT DEFAULT '[]',
    content_kind TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    bookmark_order INTEGER,
    is_truncated INTEGER DEFAULT 0,
    enriched_at TEXT DEFAULT '',
    enrich_status TEXT DEFAULT '',
    sync_status TEXT DEFAULT 'active',
    ingested_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created_at);
CREATE INDEX IF NOT EXISTS idx_bookmarks_handle ON bookmarks(author_handle);
CREATE INDEX IF NOT EXISTS idx_bookmarks_category ON bookmarks(category);
CREATE INDEX IF NOT EXISTS idx_bookmarks_kind ON bookmarks(content_kind);
CREATE INDEX IF NOT EXISTS idx_bookmarks_sync ON bookmarks(sync_status);
CREATE INDEX IF NOT EXISTS idx_bookmarks_order ON bookmarks(bookmark_order);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
    title,
    summary,
    text,
    quoted_text,
    author_name,
    author_handle,
    models,
    tags,
    content='bookmarks',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS bookmarks_ai AFTER INSERT ON bookmarks BEGIN
    INSERT INTO bookmarks_fts(
        rowid, title, summary, text, quoted_text, author_name, author_handle, models, tags
    ) VALUES (
        new.id, new.title, new.summary, new.text, new.quoted_text, new.author_name, new.author_handle, new.models, new.tags
    );
END;

CREATE TRIGGER IF NOT EXISTS bookmarks_ad AFTER DELETE ON bookmarks BEGIN
    INSERT INTO bookmarks_fts(
        bookmarks_fts, rowid, title, summary, text, quoted_text, author_name, author_handle, models, tags
    ) VALUES (
        'delete', old.id, old.title, old.summary, old.text, old.quoted_text, old.author_name, old.author_handle, old.models, old.tags
    );
END;

CREATE TRIGGER IF NOT EXISTS bookmarks_au AFTER UPDATE ON bookmarks BEGIN
    INSERT INTO bookmarks_fts(
        bookmarks_fts, rowid, title, summary, text, quoted_text, author_name, author_handle, models, tags
    ) VALUES (
        'delete', old.id, old.title, old.summary, old.text, old.quoted_text, old.author_name, old.author_handle, old.models, old.tags
    );
    INSERT INTO bookmarks_fts(
        rowid, title, summary, text, quoted_text, author_name, author_handle, models, tags
    ) VALUES (
        new.id, new.title, new.summary, new.text, new.quoted_text, new.author_name, new.author_handle, new.models, new.tags
    );
END;
"""

VALID_SYNC_STATUSES = {"active", "pending_unbookmark", "unbookmarked"}


def snowflake_to_iso(tweet_id: str | int | None) -> str | None:
    """Convert an X/Twitter Snowflake ID into an ISO-8601 UTC timestamp string."""
    if tweet_id is None:
        return None
    s = str(tweet_id).strip()
    if not s.isdigit():
        return None
    tid = int(s)
    # Snowflake IDs in practice are >= 2^22; skip clearly invalid tiny numbers
    if tid < (1 << 22):
        return None
    ts_ms = (tid >> 22) + TWITTER_EPOCH_MS
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def extract_handle_from_url(url: str) -> str:
    """Extract @handle from a canonical X status URL when available."""
    m = re.search(r"https?://(?:x|twitter)\.com/([^/]+)/status/\d+", url or "")
    if not m:
        return ""
    handle = m.group(1)
    if handle.lower() in {"i", "intent", "search"}:
        return ""
    return handle if handle.startswith("@") else f"@{handle}"


def normalize_handle(handle: str | None) -> str:
    """Ensure non-empty handle starts with '@'."""
    h = (handle or "").strip()
    if not h:
        return ""
    return h if h.startswith("@") else f"@{h}"


def _json_list(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _json_dumps_list(val: Any) -> str:
    return json.dumps(_json_list(val), ensure_ascii=False)


def _fts5_supported(conn: sqlite3.Connection) -> bool:
    try:
        rows = conn.execute("PRAGMA compile_options").fetchall()
        return any("ENABLE_FTS5" in r[0] for r in rows)
    except sqlite3.Error:
        return False


def get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with Row factory and WAL journal mode."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Initialize tables, indexes, and FTS5 triggers if supported."""
    conn = get_conn(db_path)
    conn.executescript(BASE_SCHEMA)
    if _fts5_supported(conn):
        conn.executescript(FTS_SCHEMA)
    conn.commit()
    return conn


def has_fts(conn: sqlite3.Connection) -> bool:
    """Return True if bookmarks_fts virtual table exists in this database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookmarks_fts'"
    ).fetchone()
    return row is not None


def _build_fts_query(raw: str) -> str:
    """Convert user search string into a safe FTS5 MATCH expression."""
    tokens = [t for t in re.split(r"\s+", (raw or "").strip()) if t]
    if not tokens:
        return ""
    escaped = []
    for tok in tokens:
        clean = tok.replace('"', '""')
        escaped.append(f'"{clean}"')
    return " AND ".join(escaped)


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize diverse input payload shapes into canonical DB column values."""
    tweet_id = str(raw.get("tweet_id") or raw.get("id") or "").strip()
    if not tweet_id:
        raise ValueError("Bookmark record missing tweet_id / id")

    url = (raw.get("url") or "").strip()
    handle = normalize_handle(raw.get("author_handle")) or extract_handle_from_url(url)
    if not url:
        h_clean = handle.lstrip("@") or "i"
        url = f"https://x.com/{h_clean}/status/{tweet_id}"

    created_at = (raw.get("created_at") or raw.get("timestamp") or "").strip()
    if not created_at:
        created_at = snowflake_to_iso(tweet_id) or ""

    media_urls = _json_list(raw.get("media_urls"))
    local_media = _json_list(raw.get("local_media"))
    video_urls = _json_list(raw.get("video_urls"))
    has_video = int(bool(raw.get("has_video")) or bool(video_urls) or any("video_thumb" in str(u) for u in media_urls))
    text = (raw.get("text") or "").strip()
    is_truncated = int(bool(raw.get("is_truncated")) or text.endswith("Show more") or text.endswith("顯示更多"))

    sync_status = (raw.get("sync_status") or "active").strip()
    if sync_status not in VALID_SYNC_STATUSES:
        sync_status = "active"

    return {
        "tweet_id": tweet_id,
        "url": url,
        "author_name": (raw.get("author_name") or "").strip(),
        "author_handle": handle,
        "created_at": created_at,
        "text": text,
        "quoted_text": (raw.get("quoted_text") or "").strip(),
        "quoted_author": normalize_handle(raw.get("quoted_author")),
        "media_urls": json.dumps(media_urls, ensure_ascii=False),
        "local_media": json.dumps(local_media, ensure_ascii=False),
        "video_urls": json.dumps(video_urls, ensure_ascii=False),
        "has_video": has_video,
        "external_links": _json_dumps_list(raw.get("external_links")),
        "like_count": int(raw.get("like_count") or 0),
        "retweet_count": int(raw.get("retweet_count") or 0),
        "reply_count": int(raw.get("reply_count") or 0),
        "view_count": int(raw.get("view_count") or 0),
        "category": (raw.get("category") or "").strip(),
        "models": _json_dumps_list(raw.get("models")),
        "content_kind": (raw.get("content_kind") or "").strip(),
        "tags": _json_dumps_list(raw.get("tags")),
        "title": (raw.get("title") or "").strip(),
        "summary": (raw.get("summary") or "").strip(),
        "bookmark_order": raw.get("bookmark_order"),
        "is_truncated": is_truncated,
        "enriched_at": (raw.get("enriched_at") or "").strip(),
        "enrich_status": (raw.get("enrich_status") or "").strip(),
        "sync_status": sync_status,
    }


def upsert_bookmark(conn: sqlite3.Connection, raw: dict[str, Any]) -> str:
    """Insert or smart-merge a bookmark record. Returns 'inserted' or 'updated'."""
    rec = _normalize_record(raw)
    existing = conn.execute(
        "SELECT * FROM bookmarks WHERE tweet_id = ?", (rec["tweet_id"],)
    ).fetchone()

    if existing is None:
        conn.execute(
            """INSERT INTO bookmarks (
                tweet_id, url, author_name, author_handle, created_at,
                text, quoted_text, quoted_author,
                media_urls, local_media, video_urls, has_video, external_links,
                like_count, retweet_count, reply_count, view_count,
                category, models, content_kind, tags, title, summary,
                bookmark_order, is_truncated, enriched_at, enrich_status, sync_status
            ) VALUES (
                :tweet_id, :url, :author_name, :author_handle, :created_at,
                :text, :quoted_text, :quoted_author,
                :media_urls, :local_media, :video_urls, :has_video, :external_links,
                :like_count, :retweet_count, :reply_count, :view_count,
                :category, :models, :content_kind, :tags, :title, :summary,
                :bookmark_order, :is_truncated, :enriched_at, :enrich_status, :sync_status
            )""",
            rec,
        )
        return "inserted"

    # Smart merge: preserve richer existing fields and upgrade when incoming is fuller
    merged_text = existing["text"] or ""
    if rec["text"] and (not merged_text or (len(rec["text"]) > len(merged_text) and not rec["is_truncated"]) or (existing["is_truncated"] and not rec["is_truncated"])):
        merged_text = rec["text"]
    elif not merged_text:
        merged_text = rec["text"]

    def pick_str(new_val: str, old_val: str | None) -> str:
        return new_val if new_val else (old_val or "")

    def pick_curated_str(new_val: str, old_val: str | None) -> str:
        return (old_val or "") if (old_val or "").strip() else new_val

    def pick_json_list(new_json: str, old_json: str | None, prefer_existing: bool = False) -> str:
        old_l = _json_list(old_json)
        new_l = _json_list(new_json)
        if prefer_existing and old_l:
            return json.dumps(old_l, ensure_ascii=False)
        if new_l and len(new_l) >= len(old_l):
            return json.dumps(new_l, ensure_ascii=False)
        return json.dumps(old_l or new_l, ensure_ascii=False)

    merged_truncated = rec["is_truncated"] if rec["text"] == merged_text else existing["is_truncated"]
    if existing["enriched_at"] and not rec["enriched_at"]:
        merged_truncated = existing["is_truncated"]

    conn.execute(
        """UPDATE bookmarks SET
            url = :url,
            author_name = :author_name,
            author_handle = :author_handle,
            created_at = :created_at,
            text = :text,
            quoted_text = :quoted_text,
            quoted_author = :quoted_author,
            media_urls = :media_urls,
            local_media = :local_media,
            video_urls = :video_urls,
            has_video = :has_video,
            external_links = :external_links,
            like_count = :like_count,
            retweet_count = :retweet_count,
            reply_count = :reply_count,
            view_count = :view_count,
            category = :category,
            models = :models,
            content_kind = :content_kind,
            tags = :tags,
            title = :title,
            summary = :summary,
            bookmark_order = :bookmark_order,
            is_truncated = :is_truncated,
            enriched_at = :enriched_at,
            enrich_status = :enrich_status,
            updated_at = datetime('now')
        WHERE tweet_id = :tweet_id""",
        {
            "tweet_id": rec["tweet_id"],
            "url": pick_str(rec["url"], existing["url"]),
            "author_name": pick_str(rec["author_name"], existing["author_name"]),
            "author_handle": pick_str(rec["author_handle"], existing["author_handle"]),
            "created_at": pick_str(rec["created_at"], existing["created_at"]),
            "text": merged_text,
            "quoted_text": pick_str(rec["quoted_text"], existing["quoted_text"]),
            "quoted_author": pick_str(rec["quoted_author"], existing["quoted_author"]),
            "media_urls": pick_json_list(rec["media_urls"], existing["media_urls"]),
            "local_media": pick_json_list(rec["local_media"], existing["local_media"], prefer_existing=True),
            "video_urls": pick_json_list(rec["video_urls"], existing["video_urls"]),
            "has_video": max(rec["has_video"], int(existing["has_video"] or 0)),
            "external_links": pick_json_list(rec["external_links"], existing["external_links"]),
            "like_count": max(rec["like_count"], int(existing["like_count"] or 0)),
            "retweet_count": max(rec["retweet_count"], int(existing["retweet_count"] or 0)),
            "reply_count": max(rec["reply_count"], int(existing["reply_count"] or 0)),
            "view_count": max(rec["view_count"], int(existing["view_count"] or 0)),
            "category": pick_curated_str(rec["category"], existing["category"]),
            "models": pick_json_list(rec["models"], existing["models"], prefer_existing=True),
            "content_kind": pick_curated_str(rec["content_kind"], existing["content_kind"]),
            "tags": pick_json_list(rec["tags"], existing["tags"], prefer_existing=True),
            "title": pick_curated_str(rec["title"], existing["title"]),
            "summary": pick_curated_str(rec["summary"], existing["summary"]),
            "bookmark_order": rec["bookmark_order"] if rec["bookmark_order"] is not None else existing["bookmark_order"],
            "is_truncated": merged_truncated,
            "enriched_at": pick_str(rec["enriched_at"], existing["enriched_at"]),
            "enrich_status": pick_str(rec["enrich_status"], existing["enrich_status"]),
        },
    )
    return "updated"


def upsert_many(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> dict[str, int]:
    """Batch upsert bookmarks inside a single transaction."""
    inserted = 0
    updated = 0
    for item in items:
        action = upsert_bookmark(conn, item)
        if action == "inserted":
            inserted += 1
        else:
            updated += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated, "processed": inserted + updated}


def get_known_ids(conn: sqlite3.Connection, complete_only: bool = True) -> list[str]:
    """Return tweet IDs already stored in DB.

    When complete_only=True, excludes legacy empty-text shells so incremental sync
    can naturally backfill missing text/media on re-scan.
    """
    if complete_only:
        rows = conn.execute(
            "SELECT tweet_id FROM bookmarks WHERE length(trim(text)) > 0 OR enrich_status = 'unavailable'"
        ).fetchall()
    else:
        rows = conn.execute("SELECT tweet_id FROM bookmarks").fetchall()
    return [r[0] for r in rows]


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite bookmark row into a JSON-serializable dict."""
    d = dict(row)
    for col in ("media_urls", "local_media", "video_urls", "external_links", "models", "tags"):
        d[col] = _json_list(d.get(col))
    d["has_video"] = bool(d.get("has_video"))
    d["is_truncated"] = bool(d.get("is_truncated"))
    return d


def query_bookmarks(
    conn: sqlite3.Connection,
    search: str = "",
    category: str = "",
    model: str = "",
    content_kind: str = "",
    since: str = "",
    status: str = "active",
    has_media: bool | None = None,
    sort: str = "newest",
    limit: int = 60,
    offset: int = 0,
    include_facets: bool = True,
) -> dict[str, Any]:
    """Query bookmarks with FTS5 search, multi-dimensional filters, sorting, and facets."""
    where_clauses: list[str] = []
    params: list[Any] = []

    if status and status != "all":
        where_clauses.append("b.sync_status = ?")
        params.append(status)

    if since:
        where_clauses.append("b.created_at >= ?")
        params.append(since)

    if has_media is True:
        where_clauses.append("(b.media_urls != '[]' OR b.local_media != '[]')")
    elif has_media is False:
        where_clauses.append("(b.media_urls = '[]' AND b.local_media = '[]')")

    # Scope for facets (before category/model/kind/search drill-down)
    facet_where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    facet_params = list(params)

    if category == "__uncategorized__":
        where_clauses.append("(b.category IS NULL OR b.category = '')")
    elif category:
        where_clauses.append("b.category = ?")
        params.append(category)

    if model:
        where_clauses.append("EXISTS (SELECT 1 FROM json_each(b.models) WHERE value = ?)")
        params.append(model)

    if content_kind:
        where_clauses.append("b.content_kind = ?")
        params.append(content_kind)

    search_clean = (search or "").strip()
    join_sql = ""
    if search_clean:
        tokens = [t for t in re.split(r"\s+", search_clean) if t]
        has_cjk = any(re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t) for t in tokens)
        if has_fts(conn) and not has_cjk:
            fts_expr = _build_fts_query(search_clean)
            join_sql = "JOIN bookmarks_fts f ON f.rowid = b.id"
            where_clauses.append("bookmarks_fts MATCH ?")
            params.append(fts_expr)
        else:
            for tok in tokens:
                like = f"%{tok}%"
                where_clauses.append(
                    "(b.title LIKE ? OR b.summary LIKE ? OR b.text LIKE ? OR b.quoted_text LIKE ? OR b.author_name LIKE ? OR b.author_handle LIKE ? OR b.models LIKE ? OR b.tags LIKE ?)"
                )
                params.extend([like] * 8)

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    order_map = {
        "newest": "b.created_at DESC, b.id DESC",
        "oldest": "b.created_at ASC, b.id ASC",
        "bookmark": "CASE WHEN b.bookmark_order IS NULL THEN 1 ELSE 0 END, b.bookmark_order ASC, b.created_at DESC",
        "likes": "b.like_count DESC, b.created_at DESC",
    }
    order_sql = order_map.get(sort, order_map["newest"])

    total = conn.execute(
        f"SELECT COUNT(*) FROM bookmarks b {join_sql} {where_sql}",
        params,
    ).fetchone()[0]

    query_params = list(params)
    limit_sql = ""
    if limit and limit > 0:
        limit_sql = "LIMIT ? OFFSET ?"
        query_params.extend([int(limit), int(offset)])

    rows = conn.execute(
        f"SELECT b.* FROM bookmarks b {join_sql} {where_sql} ORDER BY {order_sql} {limit_sql}",
        query_params,
    ).fetchall()

    facets: dict[str, Any] = {}
    if include_facets:
        cat_rows = conn.execute(
            f"SELECT COALESCE(NULLIF(b.category, ''), '未分類') AS k, COUNT(*) AS c FROM bookmarks b {facet_where} GROUP BY k ORDER BY c DESC",
            facet_params,
        ).fetchall()
        kind_rows = conn.execute(
            f"SELECT COALESCE(NULLIF(b.content_kind, ''), '未指定') AS k, COUNT(*) AS c FROM bookmarks b {facet_where} GROUP BY k ORDER BY c DESC",
            facet_params,
        ).fetchall()
        model_counter: Counter[str] = Counter()
        model_rows = conn.execute(
            f"SELECT b.models FROM bookmarks b {facet_where} {'AND' if facet_where else 'WHERE'} b.models != '[]'",
            facet_params,
        ).fetchall()
        for mr in model_rows:
            for m in _json_list(mr[0]):
                model_counter[m] += 1
        facets = {
            "categories": [{"name": r[0], "count": r[1]} for r in cat_rows],
            "kinds": [{"name": r[0], "count": r[1]} for r in kind_rows],
            "models": [{"name": k, "count": v} for k, v in model_counter.most_common(30)],
        }

    return {
        "items": [row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "facets": facets,
    }


def update_sync_status(conn: sqlite3.Connection, tweet_ids: list[str], status: str) -> int:
    """Update sync_status for one or more tweet IDs."""
    if status not in VALID_SYNC_STATUSES:
        raise ValueError(f"Invalid sync_status: {status}")
    ids = list(dict.fromkeys(str(tid).strip() for tid in tweet_ids if str(tid).strip()))
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE bookmarks SET sync_status = ?, updated_at = datetime('now') WHERE tweet_id IN ({placeholders})",
        [status, *ids],
    )
    conn.commit()
    return cur.rowcount


def get_unbookmark_queue(conn: sqlite3.Connection, limit: int = 0) -> dict[str, Any]:
    """Return pending_unbookmark items ordered oldest-updated first."""
    total_pending = conn.execute(
        "SELECT COUNT(*) FROM bookmarks WHERE sync_status = 'pending_unbookmark'"
    ).fetchone()[0]
    if limit and limit > 0:
        rows = conn.execute(
            "SELECT * FROM bookmarks WHERE sync_status = 'pending_unbookmark' ORDER BY updated_at ASC, id ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bookmarks WHERE sync_status = 'pending_unbookmark' ORDER BY updated_at ASC, id ASC"
        ).fetchall()
    items = [row_to_dict(r) for r in rows]
    return {"items": items, "count": len(items), "total_pending": total_pending}


def ack_unbookmark_results(
    conn: sqlite3.Connection,
    succeeded: list[str],
    failed: list[Any] | None = None,
) -> dict[str, Any]:
    """Acknowledge unbookmark execution results, moving confirmed IDs to 'unbookmarked'."""
    clean_ok = list(dict.fromkeys(str(tid).strip() for tid in succeeded if str(tid).strip()))
    changed = 0
    if clean_ok:
        placeholders = ",".join("?" for _ in clean_ok)
        cur = conn.execute(
            f"UPDATE bookmarks SET sync_status = 'unbookmarked', updated_at = datetime('now') "
            f"WHERE sync_status = 'pending_unbookmark' AND tweet_id IN ({placeholders})",
            clean_ok,
        )
        conn.commit()
        changed = cur.rowcount
    remaining = conn.execute(
        "SELECT COUNT(*) FROM bookmarks WHERE sync_status = 'pending_unbookmark'"
    ).fetchone()[0]
    return {
        "updated": changed,
        "succeeded": len(clean_ok),
        "failed": len(failed or []),
        "remaining": remaining,
    }


def purge_unbookmarked(
    conn: sqlite3.Connection,
    tweet_ids: list[str] | None = None,
    media_dir: Path | None = None,
) -> dict[str, int]:
    """Permanently delete unbookmarked rows and optionally clean up unreferenced local media."""
    if tweet_ids is not None:
        clean_ids = list(dict.fromkeys(str(tid).strip() for tid in tweet_ids if str(tid).strip()))
        if not clean_ids:
            return {"deleted": 0, "media_deleted": 0}
        placeholders = ",".join("?" for _ in clean_ids)
        target_rows = conn.execute(
            f"SELECT tweet_id, local_media FROM bookmarks WHERE sync_status = 'unbookmarked' AND tweet_id IN ({placeholders})",
            clean_ids,
        ).fetchall()
        cur = conn.execute(
            f"DELETE FROM bookmarks WHERE sync_status = 'unbookmarked' AND tweet_id IN ({placeholders})",
            clean_ids,
        )
    else:
        target_rows = conn.execute(
            "SELECT tweet_id, local_media FROM bookmarks WHERE sync_status = 'unbookmarked'"
        ).fetchall()
        cur = conn.execute("DELETE FROM bookmarks WHERE sync_status = 'unbookmarked'")

    conn.commit()
    deleted_count = cur.rowcount

    media_deleted = 0
    if media_dir and deleted_count > 0:
        base_media = Path(media_dir).resolve()
        candidates: set[str] = set()
        for row in target_rows:
            for item in _json_list(row["local_media"]):
                s = str(item).strip()
                if s and not s.startswith("http"):
                    candidates.add(Path(s).name)
        if candidates:
            referenced: set[str] = set()
            ref_rows = conn.execute(
                "SELECT local_media FROM bookmarks WHERE local_media != '[]'"
            ).fetchall()
            for r in ref_rows:
                for item in _json_list(r["local_media"]):
                    s = str(item).strip()
                    if s and not s.startswith("http"):
                        referenced.add(Path(s).name)
            for fname in candidates - referenced:
                try:
                    cand = (base_media / fname).resolve()
                    if cand.is_relative_to(base_media) and cand.is_file():
                        cand.unlink()
                        media_deleted += 1
                except OSError:
                    pass

    return {"deleted": deleted_count, "media_deleted": media_deleted}


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return overall database statistics."""
    row = conn.execute(
        """SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN length(trim(text)) > 0 THEN 1 ELSE 0 END) AS with_text,
            SUM(CASE WHEN media_urls != '[]' OR local_media != '[]' THEN 1 ELSE 0 END) AS with_media,
            SUM(CASE WHEN local_media != '[]' THEN 1 ELSE 0 END) AS with_local_media,
            SUM(CASE WHEN enriched_at != '' THEN 1 ELSE 0 END) AS enriched,
            SUM(CASE WHEN sync_status = 'pending_unbookmark' THEN 1 ELSE 0 END) AS pending_unbookmark,
            SUM(CASE WHEN sync_status = 'unbookmarked' THEN 1 ELSE 0 END) AS unbookmarked,
            MIN(NULLIF(created_at, '')) AS earliest,
            MAX(NULLIF(created_at, '')) AS latest
        FROM bookmarks"""
    ).fetchone()
    res = {k: (row[k] or 0) if k not in {"earliest", "latest"} else row[k] for k in row.keys()}
    cat_rows = conn.execute(
        "SELECT COALESCE(NULLIF(category, ''), '未分類') AS k, COUNT(*) AS c FROM bookmarks GROUP BY k ORDER BY c DESC"
    ).fetchall()
    res["categories"] = {r[0]: r[1] for r in cat_rows}
    return res
