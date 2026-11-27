#!/usr/bin/env python3
"""Zero-dependency HTTP server for X Bookmark Hub (Extension API + Web UI)."""

import argparse
from datetime import datetime
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import urllib.parse

from xbh.classifier import classify_all, classify_bookmark
from xbh.db import (
    DEFAULT_DB_PATH,
    ack_unbookmark_results,
    get_known_ids,
    get_stats,
    get_unbookmark_queue,
    init_db,
    query_bookmarks,
    update_sync_status,
    upsert_many,
)
from xbh.exporter import export_obsidian_vault, export_offline_html

ROOT_DIR = Path(__file__).resolve().parent
WEB_INDEX = ROOT_DIR / "web" / "index.html"
EXPORTS_DIR = ROOT_DIR / "exports"


class BookmarkHubServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying shared DB connection and directories."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        db_path: Path,
        api_key: str = "",
        exports_dir: Path | str | None = None,
    ):
        self.db_path = Path(db_path)
        self.media_dir = self.db_path.parent / "media"
        self.exports_dir = Path(exports_dir) if exports_dir else EXPORTS_DIR
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.conn = init_db(self.db_path)
        self.db_lock = threading.Lock()
        self.api_key = api_key
        super().__init__(server_address, RequestHandlerClass)

    def server_close(self) -> None:
        try:
            with self.db_lock:
                self.conn.close()
        except Exception:
            pass
        super().server_close()


class BookmarkHubHandler(BaseHTTPRequestHandler):
    """REST + static handler for X Bookmark Hub."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Keep stdout concise
        pass

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")

    def _send_json(self, payload: dict | list, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        data = path.read_bytes()
        ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._send_cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict | list:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _check_auth(self, allow_query: bool = False) -> bool:
        expected = self.server.api_key
        if not expected:
            return True
        provided = (self.headers.get("X-API-Key") or "").strip()
        if not provided:
            auth_hdr = (self.headers.get("Authorization") or "").strip()
            if auth_hdr.lower().startswith("bearer "):
                provided = auth_hdr[7:].strip()
        if not provided and allow_query:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            provided = (qs.get("api_key", [""])[0] or "").strip()
        if not provided or not hmac.compare_digest(provided, expected):
            self._send_json({"error": "Unauthorized", "auth_required": True}, HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _safe_child(self, base_dir: Path, rel_name: str) -> Path | None:
        clean = urllib.parse.unquote(rel_name).lstrip("/")
        if not clean or "/" in clean or "\\" in clean or ".." in clean:
            return None
        candidate = (base_dir / clean).resolve()
        try:
            if not candidate.is_relative_to(base_dir.resolve()):
                return None
        except ValueError:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            if not WEB_INDEX.exists():
                self._send_json({"error": "web/index.html missing"}, HTTPStatus.NOT_FOUND)
                return
            self._send_file(WEB_INDEX, "text/html; charset=utf-8")
            return

        if path == "/api/health":
            self._send_json({"status": "ok", "auth_required": bool(self.server.api_key)})
            return

        if path.startswith("/media/"):
            if not self._check_auth(allow_query=True):
                return
            f = self._safe_child(self.server.media_dir, path[len("/media/"):])
            if not f:
                self._send_json({"error": "Media not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_file(f)
            return

        if path.startswith("/exports/"):
            if not self._check_auth(allow_query=True):
                return
            rel = urllib.parse.unquote(path[len("/exports/"):]).lstrip("/")
            if not rel or ".." in rel:
                self._send_json({"error": "Invalid export path"}, HTTPStatus.BAD_REQUEST)
                return
            candidate = (self.server.exports_dir / rel).resolve()
            if not candidate.is_relative_to(self.server.exports_dir.resolve()) or not candidate.is_file():
                self._send_json({"error": "Export not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_file(candidate)
            return

        if not self._check_auth(allow_query=True):
            return

        if path == "/api/stats":
            with self.server.db_lock:
                data = get_stats(self.server.conn)
            self._send_json(data)
            return

        if path == "/api/bookmarks/known-ids":
            complete_only = (qs.get("complete_only", ["1"])[0] != "0")
            with self.server.db_lock:
                ids = get_known_ids(self.server.conn, complete_only=complete_only)
            self._send_json({"ids": ids, "count": len(ids)})
            return

        if path == "/api/bookmarks":
            q = qs.get("q", [""])[0]
            category = qs.get("category", [""])[0]
            model = qs.get("model", [""])[0]
            kind = qs.get("kind", [""])[0]
            since = qs.get("since", [""])[0]
            status = qs.get("status", ["active"])[0]
            sort = qs.get("sort", ["newest"])[0]
            limit = int(qs.get("limit", ["60"])[0] or 60)
            offset = int(qs.get("offset", ["0"])[0] or 0)
            with self.server.db_lock:
                data = query_bookmarks(
                    self.server.conn,
                    search=q,
                    category=category,
                    model=model,
                    content_kind=kind,
                    since=since,
                    status=status,
                    sort=sort,
                    limit=limit,
                    offset=offset,
                )
            self._send_json(data)
            return

        if path == "/api/unbookmark-queue":
            try:
                limit_val = int(qs.get("limit", ["0"])[0] or 0)
            except ValueError:
                self._send_json({"error": "Invalid limit parameter"}, HTTPStatus.BAD_REQUEST)
                return
            limit_val = max(0, limit_val)
            with self.server.db_lock:
                data = get_unbookmark_queue(self.server.conn, limit=limit_val)
            self._send_json(data)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._check_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/bookmarks":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
                return

            items = payload.get("items") if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                self._send_json({"error": "Expected JSON array"}, HTTPStatus.BAD_REQUEST)
                return

            enriched_items = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                copy_it = dict(it)
                if not copy_it.get("category"):
                    cls = classify_bookmark(copy_it)
                    if cls["category"]:
                        copy_it["category"] = cls["category"]
                    if cls["models"]:
                        copy_it["models"] = cls["models"]
                    if cls["content_kind"]:
                        copy_it["content_kind"] = cls["content_kind"]
                enriched_items.append(copy_it)

            with self.server.db_lock:
                res = upsert_many(self.server.conn, enriched_items)
                stats = get_stats(self.server.conn)
            self._send_json(
                {
                    "count": res["processed"],
                    "inserted": res["inserted"],
                    "updated": res["updated"],
                    "total": stats["total"],
                }
            )
            return

        if path == "/api/bookmarks/batch-status":
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(body, dict) or not isinstance(body.get("tweet_ids"), list) or not isinstance(body.get("sync_status"), str):
                self._send_json({"error": "Expected JSON object with tweet_ids (list) and sync_status (str)"}, HTTPStatus.BAD_REQUEST)
                return
            clean_ids = [str(tid).strip() for tid in body["tweet_ids"] if str(tid).strip()]
            status = body["sync_status"].strip()
            try:
                with self.server.db_lock:
                    changed = update_sync_status(self.server.conn, clean_ids, status)
            except ValueError as e:
                self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"sync_status": status, "requested": len(clean_ids), "updated": changed})
            return

        if path == "/api/unbookmark-ack":
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(body, dict):
                self._send_json({"error": "Expected JSON object"}, HTTPStatus.BAD_REQUEST)
                return
            succeeded = body.get("succeeded", [])
            failed = body.get("failed", [])
            if not isinstance(succeeded, list) or not isinstance(failed, list):
                self._send_json({"error": "succeeded and failed must be lists"}, HTTPStatus.BAD_REQUEST)
                return
            with self.server.db_lock:
                res = ack_unbookmark_results(self.server.conn, succeeded, failed)
            self._send_json(res)
            return

        if path == "/api/classify":
            with self.server.db_lock:
                res = classify_all(self.server.conn)
            self._send_json(res)
            return

        if path == "/api/export":
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(body, dict):
                self._send_json({"error": "Expected JSON object"}, HTTPStatus.BAD_REQUEST)
                return

            fmt = (body.get("format") or "html").lower()
            with self.server.db_lock:
                data = query_bookmarks(
                    self.server.conn,
                    search=body.get("q") or "",
                    category=body.get("category") or "",
                    model=body.get("model") or "",
                    content_kind=body.get("kind") or "",
                    since=body.get("since") or "",
                    status=body.get("status") or "active",
                    limit=0,
                    include_facets=False,
                )

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if fmt == "obsidian":
                out_dir = self.server.exports_dir / f"obsidian_{stamp}"
                res = export_obsidian_vault(data["items"], out_dir, media_dir=self.server.media_dir)
                self._send_json(res)
                return

            sub_dir = self.server.exports_dir / f"html_{stamp}"
            out_file = sub_dir / "bookmarks.html"
            res = export_offline_html(data["items"], out_file, media_dir=self.server.media_dir)
            res["download_url"] = f"/exports/html_{stamp}/bookmarks.html"
            self._send_json(res)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:
        if not self._check_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        # PATCH /api/bookmarks/<tweet_id>/status
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "bookmarks" and parts[3] == "status":
            tweet_id = urllib.parse.unquote(parts[2])
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
                return
            status = (body.get("sync_status") or "") if isinstance(body, dict) else ""
            try:
                with self.server.db_lock:
                    changed = update_sync_status(self.server.conn, [tweet_id], status)
            except ValueError as e:
                self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"tweet_id": tweet_id, "sync_status": status, "updated": changed})
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)


def create_server(
    host: str = "127.0.0.1",
    port: int = 7799,
    db_path: Path | str = DEFAULT_DB_PATH,
    api_key: str = "",
    exports_dir: Path | str | None = None,
) -> BookmarkHubServer:
    """Factory used by CLI startup and tests."""
    return BookmarkHubServer((host, port), BookmarkHubHandler, Path(db_path), api_key=api_key, exports_dir=exports_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="X Bookmark Hub HTTP server")
    parser.add_argument("--host", default=os.getenv("XBH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("XBH_PORT", "7799")))
    parser.add_argument("--db", default=os.getenv("XBH_DB_PATH", str(DEFAULT_DB_PATH)))
    parser.add_argument("--exports-dir", default=os.getenv("XBH_EXPORTS_DIR", str(EXPORTS_DIR)))
    parser.add_argument("--api-key", default=os.getenv("XBH_API_KEY", ""))
    args = parser.parse_args()

    if args.host == "0.0.0.0" and not args.api_key:
        print("WARNING: Binding to 0.0.0.0 without XBH_API_KEY! Set --api-key or XBH_API_KEY for remote deployments.")

    srv = create_server(args.host, args.port, Path(args.db), api_key=args.api_key, exports_dir=Path(args.exports_dir))
    print(f"X Bookmark Hub listening on http://{args.host}:{args.port} (DB: {args.db})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
