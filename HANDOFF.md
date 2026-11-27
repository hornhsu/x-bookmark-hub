# X Bookmark Hub — 專案交接與開發指南（HANDOFF）

本文件彙整本專案的建立背景、已完成項目（Phase 1 + Phase 2）、現有本機資料資產，以及下一階段（Phase 3 + Phase 4）的實作規格。當你把 Antigravity 工作區切換到 `D:\Work\x-bookmark-hub` 開啟新對話時，請先閱讀本文件即可無縫接手。

---

## 1. 背景與技術決策摘要

1. **為何改採全新 MIT Clean-Room 重寫（而非直接 Fork / Mirror `midnitebliss/markbook`）**
   - 原專案 `midnitebliss/markbook` 最後更新於 9 個月前，且**未附任何開源授權（無 `LICENSE`）**，依法預設為 All Rights Reserved。
   - X 書籤頁已從 `x.com/i/bookmarks` 導向 `x.com/i/history`，`markbook` 舊版選擇器（`[data-testid="tweetText"]`、`<time>`、`[data-testid="tweetPhoto"]`）已全面失效，導致舊資料庫 `D:\Work\markbook\db\markbook.db` 中 8,180 筆資料僅存 URL／`tweet_id`，內文與圖片皆為空。
   - 本專案以 **MIT License** 從零撰寫，保留了 `markbook` **唯一成功繞過 X Content-Security-Policy (CSP) 的核心優勢**——透過 Manifest V3 `chrome.scripting.executeScript` 在隔離環境讀取 DOM 並傳給本機 HTTP Server，同時全面重寫解析器、增量同步、SQLite FTS5、補完管線與 Web UI。
2. **零強制第三方 Python 依賴**
   - 考慮到工作電腦／VM 環境限制，全專案後端僅使用 Python 3.10+ 標準函式庫（`http.server.ThreadingHTTPServer`、`sqlite3` FTS5、`urllib.request`、`concurrent.futures`、`unittest`），不需額外 `pip install`。
3. **吸收外部高效工作流經驗（對照 `D:\Work\markbook\x.json` 與 `video-gen-vault`）**
   - **頁內 JS 高速捲動**：由 Chrome Extension 直接在瀏覽器分頁執行捲動迴圈，每 30 筆即時批次 POST，不經 AI 回合往返、不耗 AI Token。
   - **兩段式資料補完（`xbh/enricher.py`）**：透過公開端點 `https://api.fxtwitter.com/i/status/<id>` 補齊被 `Show more` 截斷的全文、引用貼文、讚數與高畫質媒體網址。
   - **雙維度檢視與月度 Vault**：結合「模型 × 內容型態（教學/作品/評測/新聞/提問）」雙維度篩選、繁中標題/摘要、瀑布流/卡片切換，以及按 `YYYY-MM/` 歸檔的 Obsidian Vault。

---

## 2. 已完成項目（Phase 1 + Phase 2）

### 檔案清冊
- `server.py`：零依賴 `ThreadingHTTPServer`（預設 `127.0.0.1:7799`，可透過 `--host` / `--port` / `--db` / `XBH_API_KEY` 設定）。
- `web/index.html`：內建單頁 Web UI（FTS5 搜尋、分類／模型／內容類型三層篩選、時間篩選、排序、瀑布流/網格切換、深淺色切換、Lightbox 大圖、標記待取消收藏、一鍵匯出離線 HTML / Obsidian Vault）。
- `extension/`（MV3 Chrome 擴充功能，於 `chrome://extensions/` 以「載入未封裝項目」載入）：
  - `manifest.json`、`popup.html`、`popup.js`、`content.js`
  - 相容 `x.com/i/history`（`article` 與 `li[data-index]`、`whitespace-pre`、`font-bold`）與傳統 DOM，並由 Tweet Snowflake ID 反算正確 `created_at`。
  - 支援 **增量同步（Incremental Sync）**：啟動先查 `GET /api/bookmarks/known-ids?complete_only=1`（刻意排除無內文的舊殼資料），連續遇到門檻筆數（預設 15 筆）皆已在庫且有完整內容時自動停止。
- `xbh/db.py`：SQLite WAL + FTS5（ASCII 走 FTS5 `MATCH`，含 CJK 字元自動切換精準子字串 `LIKE`）＋不覆寫既有人工/AI 整理欄位的智慧 `UPSERT`。
- `xbh/importers.py`：支援匯入舊版 `markbook.db` 與 `bookmarks_raw.json` / `bookmarks_filtered.json`（含 `assets/` 搬移）。
- `xbh/enricher.py`：`FxTwitter` 補完器 + 多執行緒本機圖片快取（存入 `data/media/`）。
- `xbh/classifier.py`：第一層零成本規則分類（模型標籤 + 領域分類 + 內容類型）＋ 第二層選配 OpenAI 相容介面 LLM 增量繁中標題摘要。
- `xbh/exporter.py`：單檔帶圖離線 HTML 匯出器 + 按月歸檔 Obsidian Vault 匯出器。
- `cli.py`：統一命令列工具（`stats` / `import-markbook` / `import-json` / `enrich` / `cache-media` / `classify` / `summarize` / `export-html` / `export-obsidian`）。
- `tests/test_core.py`：`unittest` 單元與即時 HTTP API 整合測試。

### 目前已提供的 API 端點
- `GET /`：Web UI
- `GET /media/<filename>`、`GET /exports/<filename>`：本機圖片與匯出檔案服務（含路徑穿越防護）
- `GET /api/stats`：資料庫統計
- `GET /api/bookmarks/known-ids?complete_only=1`：供 Extension 增量比對
- `GET /api/bookmarks`：多維度篩選、搜尋與分頁
- `POST /api/bookmarks`：批次寫入並自動執行輕量規則分類
- `PATCH /api/bookmarks/<tweet_id>/status`：更新 `sync_status`（`active` / `pending_unbookmark` / `unbookmarked`）
- `GET /api/unbookmark-queue`：查詢待取消收藏清單（供 Phase 3 擴充使用）
- `POST /api/classify`：重跑規則分類
- `POST /api/export`：匯出目前篩選結果為離線 HTML 或 Obsidian Vault

---

## 3. 本機既有資料資產位置（未納入 Git 版控）

1. **已全部合併至本專案 `data/` 的資料（詳見 `python cli.py stats`）**：
   - `D:\Work\x-bookmark-hub\data\bookmarks.db` 與 `data/media/`（已由以下四份來源整合去重）：
     - `D:\Work\markbook\db\markbook.db`（8,180 筆舊 `tweet_id`，已補齊 Snowflake 日期與 `@handle`）
     - `C:\Users\admin\.gemini\antigravity\scratch\x-bookmarks\bookmarks_raw.json`（602 則 `2026-11-03 ~ 2026-11-26` 貼文）
     - `C:\Users\admin\.gemini\antigravity\scratch\x-bookmarks\bookmarks_filtered.json` + `assets/`（125 篇繁中 AI 摘要與分類 + 181 張本機圖片）
     - `C:\Users\admin\Documents\x-bookmarks\bookmarks.json`（9,128 則含內文貼文）與 `video-enriched.json`（721 則 FxTwitter 補完全文與互動數據）＋ `video-gen-vault/media/`（745 張本機圖片）
2. **後續同步建議**：
   - 歷史近萬則書籤已完成整合，日常只需打開 `python server.py` 並使用 Chrome Extension 跑 **增量同步（Incremental Sync）** 即可於幾秒內拉取最新書籤，再依需求跑 `python cli.py enrich` / `cache-media` / `classify`。

---

## 4. 階段實作狀態（Phase 3 + Phase 4 已完成）

> **Phase 3 與 Phase 4 皆已依藍圖完成並分開 Commit**：

### Phase 3：雙向同步取消收藏（已完成）
1. **後端補齊（`server.py` + `xbh/db.py`）**：
   - 新增 `POST /api/unbookmark-ack`、`POST /api/bookmarks/batch-status` 與 `GET /api/unbookmark-queue?limit=...`。
2. **Web UI 強化（`web/index.html`）**：
   - 新增卡片多選勾選框、浮動批次工具列（「全選本頁」、「批次標記取消收藏」、「批次還原保留」）與「待取消佇列」專屬檢視。
3. **Chrome Extension 取消收藏執行器（`extension/`）**：
   - `popup.html` / `popup.js` 支援單背景分頁造訪 `https://x.com/i/status/<tweet_id>` 並精準定位主貼文 `<article>` 執行取消收藏，具備 2.5–5 秒隨機延遲、增量即時 ack 與 429／未登入立即中止保護。

### Phase 4：VM / Docker 部署與遠端跨機同步（已完成）
1. **MV3 Service Worker 代理（解決 Mixed Content）**：
   - 新增 `extension/background.js` 並在 `manifest.json` 註冊 `service_worker` 與 `optional_host_permissions`，`content.js` 與 `popup.js` 改以 `chrome.runtime.sendMessage` 委託背景代理呼叫伺服器 API。
2. **安全性與遠端啟動**：
   - `server.py` 強化 `XBH_API_KEY` 驗證（涵蓋讀取與寫入端點及 `/media/*`、`/exports/*`，保留 `/` 與 `/api/health` 公開），Web UI 新增 API Key 輸入視窗存於 `localStorage`。
   - 提供 `Dockerfile`、`docker-compose.yml`、`.dockerignore`、`.env.example` 與 [`docs/DEPLOY.md`](docs/DEPLOY.md)。
