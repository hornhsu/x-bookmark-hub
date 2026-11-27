# AGENTS.md — X Bookmark Hub

## Project Overview
Local-first X/Twitter bookmark sync, enrichment, FTS5 search, and offline HTML / Obsidian exporter (`Python 3.10+ stdlib` + `Manifest V3 Chrome Extension`).

## Mandatory Conventions
1. **Read `HANDOFF.md` first** before starting Phase 3, Phase 4, or modifying database/extension schemas.
2. **Zero mandatory third-party Python dependencies**: Keep `server.py`, `cli.py`, `xbh/`, and `tests/` runnable using only the Python standard library unless the user explicitly approves adding a dependency.
3. **Clean-room MIT code**: Never copy code from `D:\Work\markbook` (which has no LICENSE). All code here is MIT-licensed original work.
4. **Never commit runtime data**: Keep `data/`, `exports/`, `.env`, and `*.db` out of Git (`.gitignore` already excludes them).
5. **Commit hygiene**: Keep Phase 3 (two-way unbookmark queue) and Phase 4 (VM/Docker + MV3 Service Worker proxy) in separate focused commits.
6. **Verification**: Always run `python -m unittest discover -s tests -v` and confirm all tests pass before committing.
