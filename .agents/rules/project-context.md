# X Bookmark Hub Workspace Context

- **Repository**: `https://github.com/hornhsu/x-bookmark-hub` (branch `main`, MIT License).
- **Architecture**: Zero-dependency Python stdlib HTTP server (`server.py` on port `7799`), SQLite + FTS5 (`xbh/db.py`), CLI (`cli.py`), Manifest V3 Chrome Extension (`extension/`), and static Web UI (`web/index.html`).
- **Handoff & Roadmap**: See [HANDOFF.md](../../HANDOFF.md) for full context on Phase 1–2 completion, local migrated data in `data/bookmarks.db`, and detailed implementation specs for Phase 3 (unbookmark sync queue) and Phase 4 (VM/Docker + Service Worker proxy).
- **Tests**: Run `python -m unittest discover -s tests -v` after any code change.
