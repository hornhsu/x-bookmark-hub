# X Bookmark Hub

本機優先（Local-First）的 X / Twitter 書籤同步、資料補完、全文檢索與知識庫匯出工具。  
零強制第三方 Python 套件依賴（僅需 Python 3.10+ 標準函式庫），搭配 Manifest V3 Chrome 擴充功能直接相容新版 `x.com/i/history` 與 `x.com/i/bookmarks` 頁面。

## 系統架構

```text
┌─────────────────────┐      Batch POST       ┌──────────────────────────┐
│  x.com/i/history    │ ───────────────────▶  │  server.py (Port 7799)   │
│  Chrome Extension   │ ◀───────────────────  │  ThreadingHTTPServer     │
│  (增量 / 全量同步)   │   known-ids (增量比對) │  + Web UI (web/index.html)│
└─────────────────────┘                       └────────────┬─────────────┘
                                                           │
                                                           ▼
┌─────────────────────┐      FxTwitter API    ┌──────────────────────────┐
│  cli.py enrich      │ ───────────────────▶  │  data/bookmarks.db       │
│  cli.py cache-media │   補齊全文與圖片快取   │  SQLite + FTS5 全文檢索   │
│  cli.py classify    │                       │  data/media/ 本機縮圖     │
└─────────────────────┘                       └────────────┬─────────────┘
                                                           │
                                                           ▼
                                              ┌──────────────────────────┐
                                              │  一鍵匯出 (exports/)      │
                                              │  - 帶圖單檔離線 HTML      │
                                              │  - Obsidian 月度知識庫    │
                                              └──────────────────────────┘
```

## 快速開始

### 1. 啟動本機伺服器與 Web 介面

```bash
python server.py
```

啟動後打開瀏覽器前往：<http://127.0.0.1:7799>

### 2. 安裝 Chrome 擴充功能（Load Unpacked）

1. 開啟 `chrome://extensions/`
2. 開啟右上角 **開發人員模式（Developer mode）**
3. 點選 **載入未封裝項目（Load unpacked）**，選擇本專案的 [`extension/`](extension/) 資料夾
4. 前往已登入的 <https://x.com/i/bookmarks>（會自動導向 `x.com/i/history`），點選擴充圖示：
   - **增量同步（預設）**：從頂部往下捲動，連續遇到 15 筆資料庫已有且內容完整的貼文即自動停止。
   - **全量掃描**：一路捲動到底並邊抓邊分批寫入，適合首次完整建庫或補齊空資料。

### 3. 補齊長文、下載本機圖片與分類（CLI）

```bash
# 查看目前資料庫統計
python cli.py stats

# 透過 FxTwitter 公開端點補齊被 "Show more" 截斷的全文、引用與互動數據
python cli.py enrich --since 2026-05-26 --limit 300

# 下載貼文附圖與影片縮圖到 data/media/（離線可看）
python cli.py cache-media --limit 500

# 執行零成本規則分類（標記模型名稱、主題分類與內容類型）
python cli.py classify

# 匯出離線 HTML 與 Obsidian Vault
python cli.py export-html --since 2026-05-26 --out exports/bookmarks.html
python cli.py export-obsidian --since 2026-05-26 --out exports/obsidian-vault
```

## 從舊資料匯入

```bash
# 匯入舊版 markbook.db（自動由 Snowflake ID 反算貼文日期與 handle）
python cli.py import-markbook "D:\Work\markbook\db\markbook.db"

# 匯入 JSON 書籤（支援 bookmarks_raw.json / bookmarks_filtered.json）
python cli.py import-json "C:\path\to\bookmarks_filtered.json" --copy-assets-from "C:\path\to"
```

## 開發進度（Roadmap）

- **Phase 1（已完成）**：MV3 擴充（相容 `/i/history` DOM、邊捲邊傳、增量同步）、`FxTwitter` 補完器、本機圖片快取、規則分類 + 選配 LLM 摘要。
- **Phase 2（已完成）**：內建 Web UI（FTS5 毫秒搜尋、模型 × 類型雙維度篩選、瀑布流／卡片切換、Lightbox 放大）、一鍵匯出離線 HTML 與按月歸檔 Obsidian Vault。
- **Phase 3（已完成）**：網頁多選與待取消收藏佇列檢視 → Chrome 擴充功能單背景分頁低風險限速（隨機 2.5–5 秒延遲、429／未登入自動中止）執行雙向取消收藏同步。
- **Phase 4（已完成）**：MV3 Service Worker (`background.js`) 跨域代理（解決遠端 HTTP Mixed Content）、全端點 `XBH_API_KEY` 驗證、`Dockerfile` / `docker-compose.yml` 與 [遠端部署指南](docs/DEPLOY.md)。

## 授權

本專案採用 [MIT License](LICENSE) 授權。
