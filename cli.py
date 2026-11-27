#!/usr/bin/env python3
"""Unified CLI for X Bookmark Hub."""

import argparse
import json
import os
from pathlib import Path

from xbh.classifier import classify_all, summarize_with_llm
from xbh.db import DEFAULT_DB_PATH, init_db, get_stats, query_bookmarks
from xbh.enricher import cache_media, enrich_bookmarks
from xbh.exporter import export_obsidian_vault, export_offline_html
from xbh.importers import import_json_bookmarks, import_markbook_db


def main() -> None:
    parser = argparse.ArgumentParser(description="X Bookmark Hub CLI")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite DB")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="Show database statistics")

    p_mb = sub.add_parser("import-markbook", help="Import legacy markbook.db")
    p_mb.add_argument("source_db", help="Path to markbook.db")

    p_js = sub.add_parser("import-json", help="Import JSON bookmarks file")
    p_js.add_argument("json_file", help="Path to JSON file")
    p_js.add_argument("--copy-assets-from", default=None, help="Directory containing relative assets/")

    p_en = sub.add_parser("enrich", help="Enrich truncated/empty tweets via FxTwitter API")
    p_en.add_argument("--since", default="", help="Only enrich tweets with created_at >= YYYY-MM-DD")
    p_en.add_argument("--limit", type=int, default=100, help="Max tweets per run")
    p_en.add_argument("--delay", type=float, default=0.35, help="Delay between API requests (seconds)")

    p_cm = sub.add_parser("cache-media", help="Download media thumbnails to data/media/")
    p_cm.add_argument("--since", default="", help="Only cache media for tweets >= YYYY-MM-DD")
    p_cm.add_argument("--limit", type=int, default=300, help="Max tweets to cache media for")

    p_cl = sub.add_parser("classify", help="Run zero-cost rule-based classification")
    p_cl.add_argument("--overwrite", action="store_true", help="Overwrite existing categories")

    p_llm = sub.add_parser("summarize", help="Generate Traditional Chinese titles/summaries via OpenAI-compatible API")
    p_llm.add_argument("--since", default="", help="Only summarize tweets >= YYYY-MM-DD")
    p_llm.add_argument("--limit", type=int, default=30, help="Max tweets to summarize")
    p_llm.add_argument("--api-base", default=os.getenv("XBH_LLM_API_BASE", "https://api.openai.com/v1"))
    p_llm.add_argument("--api-key", default=os.getenv("XBH_LLM_API_KEY", ""))
    p_llm.add_argument("--model", default=os.getenv("XBH_LLM_MODEL", "gpt-4o-mini"))

    p_eh = sub.add_parser("export-html", help="Export filtered bookmarks to standalone offline HTML")
    p_eh.add_argument("--since", default="", help="Filter created_at >= YYYY-MM-DD")
    p_eh.add_argument("--category", default="", help="Filter category")
    p_eh.add_argument("--model", default="", help="Filter model tag")
    p_eh.add_argument("--search", default="", help="FTS5 search query")
    p_eh.add_argument("--out", default="exports/bookmarks.html", help="Output HTML path")

    p_eo = sub.add_parser("export-obsidian", help="Export filtered bookmarks to Obsidian Vault")
    p_eo.add_argument("--since", default="", help="Filter created_at >= YYYY-MM-DD")
    p_eo.add_argument("--category", default="", help="Filter category")
    p_eo.add_argument("--model", default="", help="Filter model tag")
    p_eo.add_argument("--search", default="", help="FTS5 search query")
    p_eo.add_argument("--out", default="exports/obsidian-vault", help="Output Obsidian folder path")

    args = parser.parse_args()
    db_path = Path(args.db)
    media_dir = db_path.parent / "media"
    conn = init_db(db_path)

    try:
        if args.cmd == "stats":
            print(json.dumps(get_stats(conn), ensure_ascii=False, indent=2))
        elif args.cmd == "import-markbook":
            res = import_markbook_db(Path(args.source_db), conn)
            print(json.dumps(res, ensure_ascii=False))
        elif args.cmd == "import-json":
            assets_src = Path(args.copy_assets_from) if args.copy_assets_from else None
            res = import_json_bookmarks(Path(args.json_file), conn, copy_assets_from=assets_src, media_dir=media_dir)
            print(json.dumps(res, ensure_ascii=False))
        elif args.cmd == "enrich":
            res = enrich_bookmarks(conn, since=args.since, limit=args.limit, delay=args.delay)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.cmd == "cache-media":
            res = cache_media(conn, media_dir=media_dir, since=args.since, limit=args.limit)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.cmd == "classify":
            res = classify_all(conn, overwrite=args.overwrite)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.cmd == "summarize":
            if not args.api_key:
                raise SystemExit("Please provide --api-key or set XBH_LLM_API_KEY")
            res = summarize_with_llm(conn, api_base=args.api_base, api_key=args.api_key, model=args.model, since=args.since, limit=args.limit)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.cmd == "export-html":
            data = query_bookmarks(conn, search=args.search, category=args.category, model=args.model, since=args.since, limit=0, include_facets=False)
            res = export_offline_html(data["items"], Path(args.out), media_dir=media_dir)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.cmd == "export-obsidian":
            data = query_bookmarks(conn, search=args.search, category=args.category, model=args.model, since=args.since, limit=0, include_facets=False)
            res = export_obsidian_vault(data["items"], Path(args.out), media_dir=media_dir)
            print(json.dumps(res, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
