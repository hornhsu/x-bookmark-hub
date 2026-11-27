"""Unit and integration tests for X Bookmark Hub (Phase 1 + Phase 2)."""

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request

from server import create_server
from xbh.classifier import classify_all, classify_bookmark
from xbh.db import (
    get_known_ids,
    get_stats,
    init_db,
    query_bookmarks,
    snowflake_to_iso,
    update_sync_status,
    upsert_bookmark,
    upsert_many,
)
from xbh.exporter import export_obsidian_vault, export_offline_html
from xbh.importers import import_json_bookmarks, import_markbook_db


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "data" / "test.db"
        self.media_dir = self.db_path.parent / "media"
        self.conn = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_snowflake_decoding(self) -> None:
        iso = snowflake_to_iso("2125882946066485532")
        self.assertIsNotNone(iso)
        self.assertTrue(iso.startswith("2026-11-26T09:22:45"))
        self.assertIsNone(snowflake_to_iso("abc"))
        self.assertIsNone(snowflake_to_iso("123"))

    def test_smart_upsert_and_known_ids(self) -> None:
        # Empty legacy shell should not appear in complete_only known IDs
        upsert_bookmark(self.conn, {"tweet_id": "2125546356609790433", "url": "https://x.com/smardio/status/2125546356609790433"})
        self.conn.commit()
        self.assertEqual(get_known_ids(self.conn, complete_only=True), [])
        self.assertEqual(get_known_ids(self.conn, complete_only=False), ["2125546356609790433"])

        # Backfill full text + curated fields
        upsert_bookmark(
            self.conn,
            {
                "tweet_id": "2125546356609790433",
                "text": "MiniMax-H3 Turbo 2K workflow test",
                "author_name": "MAI",
                "title": "H3 Turbo 2K 實測",
                "category": "MiniMax H3 / Hailuo",
            },
        )
        self.conn.commit()
        self.assertEqual(get_known_ids(self.conn, complete_only=True), ["2125546356609790433"])

        # Subsequent empty/shorter update must NOT clobber curated fields
        upsert_bookmark(self.conn, {"tweet_id": "2125546356609790433", "text": "Short", "title": "", "category": ""})
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM bookmarks WHERE tweet_id = ?", ("2125546356609790433",)).fetchone()
        self.assertEqual(row["text"], "MiniMax-H3 Turbo 2K workflow test")
        self.assertEqual(row["title"], "H3 Turbo 2K 實測")
        self.assertEqual(row["category"], "MiniMax H3 / Hailuo")

    def test_fts5_search_with_punctuation_and_cjk(self) -> None:
        upsert_many(
            self.conn,
            [
                {
                    "tweet_id": "2125546356609790433",
                    "author_handle": "@smardio",
                    "text": "Testing MiniMax-H3 Turbo 2K 生成工作流",
                    "models": ["MiniMax H3"],
                },
                {
                    "tweet_id": "2125519521427681660",
                    "author_handle": "@johnAGI168",
                    "text": "MAGI-2 vs Seedance 2.5 comparison",
                    "models": ["MAGI-2", "Seedance"],
                },
            ],
        )
        r1 = query_bookmarks(self.conn, search="MiniMax-H3")
        self.assertEqual(r1["total"], 1)
        self.assertEqual(r1["items"][0]["tweet_id"], "2125546356609790433")

        r2 = query_bookmarks(self.conn, search="@johnAGI168")
        self.assertEqual(r2["total"], 1)
        self.assertEqual(r2["items"][0]["tweet_id"], "2125519521427681660")

        r3 = query_bookmarks(self.conn, search="工作流")
        self.assertEqual(r3["total"], 1)

    def test_classifier(self) -> None:
        c1 = classify_bookmark({"text": "MiniMax H3 ComfyUI workflow tutorial"})
        self.assertEqual(c1["category"], "MiniMax H3 / Hailuo")
        self.assertIn("MiniMax H3", c1["models"])
        self.assertIn("ComfyUI", c1["models"])
        self.assertEqual(c1["content_kind"], "tutorial")

        c2 = classify_bookmark({"text": "MAGI-2 vs Seedance 2.5 benchmark review"})
        self.assertEqual(c2["category"], "影片生成模型")
        self.assertEqual(c2["content_kind"], "review")

        upsert_bookmark(self.conn, {"tweet_id": "2125546356609790433", "text": "Qwen-Image 2.1 LoRA 發布開源"})
        self.conn.commit()
        res = classify_all(self.conn)
        self.assertGreaterEqual(res["updated"], 1)

    def test_importers_and_exporters(self) -> None:
        # Legacy markbook.db simulation
        legacy_db = self.root / "legacy_markbook.db"
        lc = sqlite3.connect(str(legacy_db))
        lc.execute(
            "CREATE TABLE bookmarks (id INTEGER PRIMARY KEY, tweet_id TEXT UNIQUE, url TEXT, text TEXT, author_name TEXT, author_handle TEXT, created_at TEXT, media_urls TEXT, like_count INTEGER, retweet_count INTEGER, reply_count INTEGER, category TEXT, raw_json TEXT)"
        )
        lc.execute(
            "INSERT INTO bookmarks (tweet_id, url, text, media_urls) VALUES (?, ?, ?, ?)",
            ("2125842510585192551", "https://x.com/sjmdongwugo/status/2125842510585192551", "", "[]"),
        )
        lc.commit()
        lc.close()

        imp_mb = import_markbook_db(legacy_db, self.conn)
        self.assertEqual(imp_mb["inserted"], 1)

        # JSON import with local asset copying
        src_assets = self.root / "src_proj"
        (src_assets / "assets").mkdir(parents=True)
        (src_assets / "assets" / "2125546356609790433_0.jpg").write_bytes(b"fakejpg")
        json_file = src_assets / "filt.json"
        json_file.write_text(
            json.dumps(
                [
                    {
                        "id": "2125546356609790433",
                        "url": "https://x.com/smardio/status/2125546356609790433",
                        "author_name": "MAI",
                        "author_handle": "@smardio",
                        "timestamp": "2026-11-25T11:05:16.814Z",
                        "text": "MiniMax-H3 Turbo 2K",
                        "category": "MiniMax H3 / Hailuo",
                        "models": ["MiniMax H3"],
                        "local_media": ["assets/2125546356609790433_0.jpg"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        imp_js = import_json_bookmarks(json_file, self.conn, copy_assets_from=src_assets, media_dir=self.media_dir)
        self.assertEqual(imp_js["assets_copied"], 1)

        items = query_bookmarks(self.conn, category="MiniMax H3 / Hailuo")["items"]
        html_out = self.root / "exports" / "out.html"
        res_html = export_offline_html(items, html_out, media_dir=self.media_dir)
        self.assertEqual(res_html["count"], 1)
        self.assertTrue(html_out.exists())
        self.assertTrue((html_out.parent / "media" / "2125546356609790433_0.jpg").exists())

        obs_out = self.root / "exports" / "vault"
        res_obs = export_obsidian_vault(items, obs_out, media_dir=self.media_dir)
        self.assertEqual(res_obs["notes"], 1)
        self.assertTrue((obs_out / "00-索引" / "總覽.md").exists())
        self.assertTrue((obs_out / "notes" / "2026-11" / "2026-11-25_smardio_2125546356609790433.md").exists())

    def test_import_opencode_json_and_media_by_id(self) -> None:
        # Seed existing curated title/summary to ensure merge preserves them
        upsert_bookmark(
            self.conn,
            {
                "tweet_id": "2125865347584860623",
                "text": "Vidu短文",
                "title": "Vidu 限時免費整理",
                "summary": "已整理的繁中摘要",
                "category": "影片生成模型",
            },
        )
        self.conn.commit()

        vault_media = self.root / "vault_media"
        vault_media.mkdir(parents=True)
        (vault_media / "2125865347584860623-1.jpg").write_bytes(b"imgbytes")

        oc_file = self.root / "video-enriched.json"
        oc_file.write_text(
            json.dumps(
                [
                    {
                        "id": "2125865347584860623",
                        "url": "https://x.com/songguoxiansen/status/2125865347584860623",
                        "handle": "songguoxiansen",
                        "author": "松果先森",
                        "when": "2026-11-26T06:00:00.000Z",
                        "ym": "2026-11",
                        "approx": True,
                        "text": "Vidu短文",
                        "links": ["https://t.co/SScnbUrJre"],
                        "media": [],
                        "models": ["vidu"],
                        "fx": {
                            "fullText": "Vidu视频模型免费，可以爽玩5天 https://example.com",
                            "likes": 24,
                            "retweets": 2,
                            "replies": 0,
                            "views": 3039,
                            "quote": {"author": "songguoxiansen", "text": "引用內容"},
                            "media": [
                                {
                                    "type": "video",
                                    "thumb": "https://pbs.twimg.com/amplify_video_thumb/1/img/a.jpg",
                                    "url": "https://video.twimg.com/amplify_video/1/vid/a.mp4",
                                }
                            ],
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        res = import_json_bookmarks(oc_file, self.conn, media_dir=self.media_dir, media_by_id_dir=vault_media)
        self.assertEqual(res["updated"], 1)
        self.assertEqual(res["assets_copied"], 1)

        row = self.conn.execute("SELECT * FROM bookmarks WHERE tweet_id = ?", ("2125865347584860623",)).fetchone()
        self.assertEqual(row["title"], "Vidu 限時免費整理")
        self.assertEqual(row["summary"], "已整理的繁中摘要")
        self.assertEqual(row["text"], "Vidu视频模型免费，可以爽玩5天 https://example.com")
        self.assertEqual(row["like_count"], 24)
        self.assertEqual(row["quoted_text"], "引用內容")
        self.assertEqual(row["has_video"], 1)
        self.assertEqual(row["enrich_status"], "ok")
        self.assertTrue(row["created_at"].startswith("2026-11-26T08:12:50"))
        self.assertEqual(json.loads(row["local_media"]), ["media/2125865347584860623-1.jpg"])
        self.assertEqual(json.loads(row["models"]), [])

    def test_live_http_server(self) -> None:
        srv_db = self.root / "srv" / "hub.db"
        srv = create_server("127.0.0.1", 0, srv_db)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"
            req = urllib.request.Request(
                f"{base}/api/bookmarks",
                data=json.dumps([{"tweet_id": "2125546356609790433", "text": "MiniMax-H3 test post", "author_handle": "@smardio"}]).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                post_res = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(post_res["inserted"], 1)

            with urllib.request.urlopen(f"{base}/api/bookmarks/known-ids") as resp:
                ids_res = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(ids_res["ids"], ["2125546356609790433"])

            with urllib.request.urlopen(f"{base}/api/bookmarks?q=MiniMax-H3") as resp:
                q_res = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(q_res["total"], 1)

            patch_req = urllib.request.Request(
                f"{base}/api/bookmarks/2125546356609790433/status",
                data=json.dumps({"sync_status": "pending_unbookmark"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PATCH",
            )
            with urllib.request.urlopen(patch_req) as resp:
                patch_res = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(patch_res["updated"], 1)

            with urllib.request.urlopen(f"{base}/api/unbookmark-queue") as resp:
                q_un = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(q_un["count"], 1)

            with urllib.request.urlopen(f"{base}/api/stats") as resp:
                st = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(st["pending_unbookmark"], 1)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
