# 遠端 VM / Docker / Tailscale 部署指南

本指南說明如何將 **X Bookmark Hub** 部署到遠端 Linux VM 或 Docker 主機，並從本機電腦的 Chrome 擴充功能進行跨機安全同步（無 Mixed Content 問題）。

---

## 1. Docker Compose 部署（建議）

### 準備環境變數
```bash
cp .env.example .env
```

編輯 `.env`，設定一組高強度 `XBH_API_KEY`：
```env
XBH_API_KEY=your-strong-random-token
XBH_BIND_HOST=127.0.0.1
XBH_PORT=7799
```
> 若希望直接由 Tailscale 網卡 IP 對外提供連線（例如 `100.64.x.x`），可將 `XBH_BIND_HOST` 改為該 Tailscale IP，或保持 `127.0.0.1` 並搭配 `tailscale serve`。

### 啟動服務與日常 CLI 操作
```bash
# 建置並背景啟動
docker compose up -d --build

# 查看狀態與健康檢查
docker compose ps
docker compose logs -f xbh

# 在容器內執行補完、圖片快取與分類
docker compose exec xbh python cli.py stats
docker compose exec xbh python cli.py enrich --limit 300
docker compose exec xbh python cli.py cache-media --limit 500
docker compose exec xbh python cli.py classify
```

資料與匯出檔皆透過 Volume 掛載保存在宿主機的 `./data` 與 `./exports` 目錄。

---

## 2. Linux VM 原生部署（Systemd，零 pip 依賴）

只要主機有 Python 3.10+ 即可直接啟動，不需建立 virtualenv 或 `pip install`。

建立 `/etc/systemd/system/x-bookmark-hub.service`：
```ini
[Unit]
Description=X Bookmark Hub Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/x-bookmark-hub
Environment="XBH_HOST=127.0.0.1"
Environment="XBH_PORT=7799"
Environment="XBH_API_KEY=your-strong-random-token"
ExecStart=/usr/bin/python3 /opt/x-bookmark-hub/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

啟用服務：
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now x-bookmark-hub
sudo systemctl status x-bookmark-hub
```

---

## 3. 搭配 Tailscale 跨機安全連線與擴充功能設定

### 伺服器端（二選一）
- **方案 A（HTTPS 反向代理，推薦）**：保持 `XBH_HOST=127.0.0.1`，執行：
  ```bash
  tailscale serve --bg 7799
  ```
  即可獲得 Tailnet 內部的 `https://<machine>.<tailnet>.ts.net` 位址。
- **方案 B（HTTP Tailscale IP 直連）**：將 `XBH_HOST` 或 `XBH_BIND_HOST` 設為 VM 的 Tailscale IP（例如 `100.64.0.10`）。
  本專案 Chrome 擴充功能透過 MV3 Service Worker (`extension/background.js`) 代理 API 請求，因此即使在 `https://x.com` 頁面下連線至 `http://100.64.0.10:7799`，也不會被瀏覽器以 Mixed Content 阻擋。

### Chrome 擴充功能與 Web UI 設定
1. 開啟擴充功能彈出視窗：
   - **Server 位址**：填入 `http://100.64.0.10:7799` 或 `https://<machine>.<tailnet>.ts.net`
   - **API Key**：填入與伺服器相同的 `XBH_API_KEY`
   - 點選 **「測試連線」**，依 Chrome 提示授權存取該遠端來源（`optional_host_permissions`）。
2. 打開 Web 管理介面時，若伺服器已啟用 `XBH_API_KEY`，介面會彈出金鑰輸入視窗並存於瀏覽器 `localStorage`（之後可在右上角 **「🔑 Key」** 更新或清除）。

---

## 4. 備份與還原

- **線上安全備份 SQLite**：
  ```bash
  sqlite3 data/bookmarks.db ".backup 'data/bookmarks.backup.db'"
  ```
- **完整遷移**：直接複製整個 `data/`（含 `bookmarks.db` 與 `media/`）至新主機相同路徑即可。
