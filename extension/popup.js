const serverInput = document.getElementById("serverUrl");
const testConnBtn = document.getElementById("testConnBtn");
const apiKeyInput = document.getElementById("apiKeyInput");
const modeSelect = document.getElementById("modeSelect");
const thresholdInput = document.getElementById("thresholdInput");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const queueCountEl = document.getElementById("queueCount");
const refreshQueueBtn = document.getElementById("refreshQueueBtn");
const unbookmarkLimitInput = document.getElementById("unbookmarkLimit");
const unbookmarkBtn = document.getElementById("unbookmarkBtn");
const stopUnbookmarkBtn = document.getElementById("stopUnbookmarkBtn");
const statusBox = document.getElementById("status");
const openUiLink = document.getElementById("openUiLink");

let isUnbookmarking = false;
let unbookmarkStopRequested = false;

function setStatus(text, cls = "") {
  statusBox.textContent = text;
  statusBox.className = cls;
}

function normalizedServer() {
  return (serverInput.value || "http://127.0.0.1:7799").trim().replace(/\/+$/, "");
}

function currentApiKey() {
  return (apiKeyInput.value || "").trim();
}

async function ensureOriginPermission(serverUrl) {
  let u;
  try {
    u = new URL(serverUrl);
  } catch {
    throw new Error(`無效的 Server 位址：${serverUrl}`);
  }
  if (u.hostname === "localhost" || u.hostname === "127.0.0.1") {
    return true;
  }
  const originPattern = `${u.protocol}//${u.hostname}/*`;
  const has = await chrome.permissions.contains({ origins: [originPattern] });
  if (has) return true;
  const granted = await chrome.permissions.request({ origins: [originPattern] });
  if (!granted) {
    throw new Error(`未授權擴充功能存取 ${originPattern}`);
  }
  return true;
}

async function proxyRequest(serverUrl, path, { method = "GET", headers = {}, body = undefined } = {}) {
  const res = await new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      {
        source: "xbh-proxy",
        action: "fetch",
        url: `${serverUrl}${path}`,
        method,
        headers,
        body,
        apiKey: currentApiKey(),
      },
      (resp) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(resp);
      }
    );
  });
  if (!res) throw new Error("背景代理無回應");
  if (res.status === 401) throw new Error("API Key 錯誤或未填入 (HTTP 401)");
  if (!res.ok) throw new Error(res.error || `HTTP ${res.status}`);
  return res.data;
}

async function refreshQueueCount() {
  const serverUrl = normalizedServer();
  try {
    const data = await proxyRequest(serverUrl, "/api/unbookmark-queue?limit=1");
    const total = Number(data.total_pending ?? data.count ?? 0);
    queueCountEl.textContent = String(total);
    if (!isUnbookmarking) {
      unbookmarkBtn.disabled = total <= 0;
    }
    return total;
  } catch (err) {
    queueCountEl.textContent = String(err.message || "").includes("401") ? "需驗證" : "離線";
    if (!isUnbookmarking) unbookmarkBtn.disabled = true;
    return -1;
  }
}

chrome.storage.local.get(["serverUrl", "apiKey", "mode", "threshold", "unbookmarkLimit"], (saved) => {
  if (saved.serverUrl) serverInput.value = saved.serverUrl;
  if (saved.apiKey) apiKeyInput.value = saved.apiKey;
  if (saved.mode) modeSelect.value = saved.mode;
  if (saved.threshold) thresholdInput.value = saved.threshold;
  if (saved.unbookmarkLimit) unbookmarkLimitInput.value = saved.unbookmarkLimit;
  openUiLink.href = normalizedServer();
  refreshQueueCount();
});

serverInput.addEventListener("change", () => {
  const s = normalizedServer();
  openUiLink.href = s;
  chrome.storage.local.set({ serverUrl: s });
  refreshQueueCount();
});
apiKeyInput.addEventListener("change", () => {
  chrome.storage.local.set({ apiKey: currentApiKey() });
  refreshQueueCount();
});
modeSelect.addEventListener("change", () => chrome.storage.local.set({ mode: modeSelect.value }));
thresholdInput.addEventListener("change", () => chrome.storage.local.set({ threshold: Number(thresholdInput.value) || 15 }));
unbookmarkLimitInput.addEventListener("change", () => {
  const val = Math.min(100, Math.max(1, Number(unbookmarkLimitInput.value) || 30));
  unbookmarkLimitInput.value = String(val);
  chrome.storage.local.set({ unbookmarkLimit: val });
});
refreshQueueBtn.addEventListener("click", async () => {
  try {
    await ensureOriginPermission(normalizedServer());
    await refreshQueueCount();
  } catch (e) {
    setStatus(`連線權限失敗：${e.message}`, "err");
  }
});
testConnBtn.addEventListener("click", async () => {
  const serverUrl = normalizedServer();
  const apiKey = currentApiKey();
  chrome.storage.local.set({ serverUrl, apiKey });
  try {
    await ensureOriginPermission(serverUrl);
    const health = await proxyRequest(serverUrl, "/api/health");
    const stats = await proxyRequest(serverUrl, "/api/stats");
    setStatus(`連線成功！（驗證：${health.auth_required ? "已啟用" : "未啟用"}）\n資料庫共 ${stats.total} 則書籤`, "ok");
    await refreshQueueCount();
  } catch (e) {
    setStatus(`連線失敗：${e.message}`, "err");
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.source !== "xbh") return;
  if (msg.type === "progress") {
    setStatus(msg.text);
  } else if (msg.type === "done") {
    setStatus(msg.text, "ok");
    startBtn.disabled = false;
    stopBtn.disabled = true;
  } else if (msg.type === "error") {
    setStatus(`錯誤：${msg.text}`, "err");
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
});

startBtn.addEventListener("click", async () => {
  const serverUrl = normalizedServer();
  const apiKey = currentApiKey();
  const mode = modeSelect.value;
  const threshold = Math.max(3, Number(thresholdInput.value) || 15);
  chrome.storage.local.set({ serverUrl, apiKey, mode, threshold });

  try {
    await ensureOriginPermission(serverUrl);
  } catch (e) {
    setStatus(`授權失敗：${e.message}`, "err");
    return;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !tab.url.includes("x.com")) {
    setStatus("請先在目前分頁打開 x.com/i/bookmarks 或 x.com/i/history", "err");
    return;
  }

  startBtn.disabled = true;
  stopBtn.disabled = false;
  setStatus("初始化同步中...");

  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (cfg) => {
        window.__xbhConfig = cfg;
        window.__xbhStop = false;
      },
      args: [{ serverUrl, apiKey, mode, threshold }],
    });
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"],
    });
  } catch (e) {
    setStatus(`注入腳本失敗：${e.message}`, "err");
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
});

stopBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      window.__xbhStop = true;
    },
  });
  setStatus("正在停止並傳送已收集的書籤...");
});

function waitTabComplete(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve(ok);
    };
    const listener = (updatedId, info) => {
      if (updatedId === tabId && info.status === "complete") {
        finish(true);
      }
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function cancellableSleep(ms) {
  let elapsed = 0;
  while (elapsed < ms && !unbookmarkStopRequested) {
    const step = Math.min(200, ms - elapsed);
    await new Promise((r) => setTimeout(r, step));
    elapsed += step;
  }
}

async function unbookmarkInStatusPage(tweetId) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const removeRe = /remove\s*bookmark|移除書籤|取消書籤|取消收藏|ブックマークを削除/i;
  const addRe = /^(bookmark|加入書籤|書籤|收藏|ブックマーク)$/i;
  const rateRe = /rate limit exceeded|too many requests|something went wrong\.?\s*try reloading/i;
  const toastErrRe = /rate limit|too many|something went wrong|try again|稍後再試|發生錯誤/i;
  const deletedRe = /this page doesn.?t exist|this post was deleted|this account doesn.?t exist|unable to view this post|此頁面不存在|此貼文已遭刪除|無法查看此貼文/i;

  function findFocalArticle() {
    const articles = [...document.querySelectorAll("article")];
    if (!articles.length) return null;
    for (const art of articles) {
      for (const t of art.querySelectorAll("time")) {
        const link = t.closest('a[href*="/status/"]');
        if (link && (link.getAttribute("href") || "").includes(`/status/${tweetId}`)) {
          return art;
        }
      }
    }
    for (const art of articles) {
      if (art.getAttribute("tabindex") === "-1") return art;
    }
    for (const art of articles) {
      const link = art.querySelector(`a[href*="/status/${tweetId}"]`);
      if (link) return art;
    }
    return articles.length === 1 ? articles[0] : null;
  }

  function findButton(root, testId, labelRe) {
    const byId = root.querySelector(`[data-testid="${testId}"]`);
    if (byId) return byId;
    for (const btn of root.querySelectorAll("button[aria-label]")) {
      const lbl = (btn.getAttribute("aria-label") || "").trim();
      if (labelRe.test(lbl)) return btn;
    }
    return null;
  }

  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (location.pathname.startsWith("/i/flow/login") || location.pathname === "/login") {
      return { status: "abort", reason: "未偵測到 X 登入狀態，已立即中止" };
    }
    const bodyText = (document.body?.innerText || "").trim();
    if (rateRe.test(bodyText)) {
      return { status: "abort", reason: "偵測到 X 限流或錯誤頁面（429），已立即中止" };
    }
    const hasLoginBtn = Boolean(document.querySelector('[data-testid="loginButton"]'));
    const hasSwitcher = Boolean(document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]'));
    const articleCount = document.querySelectorAll("article").length;
    if (hasLoginBtn && !hasSwitcher && articleCount === 0) {
      return { status: "abort", reason: "未偵測到 X 登入狀態，已立即中止" };
    }

    const focal = findFocalArticle();
    if (focal) {
      const removeBtn = findButton(focal, "removeBookmark", removeRe);
      if (removeBtn) {
        removeBtn.click();
        const confirmDeadline = Date.now() + 4500;
        while (Date.now() < confirmDeadline) {
          const toastEl = document.querySelector('[data-testid="toast"]');
          if (toastEl && toastErrRe.test(toastEl.innerText || "")) {
            return { status: "abort", reason: "X 回報錯誤或限流提示，已立即中止" };
          }
          const nowAdd = findButton(focal, "bookmark", addRe);
          const stillRemove = findButton(focal, "removeBookmark", removeRe);
          if (nowAdd || !stillRemove) {
            return { status: "succeeded", reason: "removed" };
          }
          await sleep(200);
        }
        return { status: "failed", reason: "點擊後未確認狀態切換" };
      }
      const addBtn = findButton(focal, "bookmark", addRe);
      if (addBtn) {
        return { status: "succeeded", reason: "already_unbookmarked" };
      }
    } else if (deletedRe.test(bodyText)) {
      return { status: "failed", reason: "貼文不存在或無法檢視" };
    }

    await sleep(300);
  }
  return { status: "failed", reason: "載入逾時，找不到書籤按鈕" };
}

stopUnbookmarkBtn.addEventListener("click", () => {
  unbookmarkStopRequested = true;
  stopUnbookmarkBtn.disabled = true;
  setStatus("正在安全停止取消收藏...");
});

unbookmarkBtn.addEventListener("click", async () => {
  const serverUrl = normalizedServer();
  const apiKey = currentApiKey();
  const batchLimit = Math.min(100, Math.max(1, Number(unbookmarkLimitInput.value) || 30));
  chrome.storage.local.set({ serverUrl, apiKey, unbookmarkLimit: batchLimit });

  try {
    await ensureOriginPermission(serverUrl);
  } catch (e) {
    setStatus(`授權失敗：${e.message}`, "err");
    return;
  }

  isUnbookmarking = true;
  unbookmarkStopRequested = false;
  unbookmarkBtn.disabled = true;
  startBtn.disabled = true;
  stopUnbookmarkBtn.disabled = false;
  setStatus("讀取待取消收藏佇列中...");

  let bgTab = null;
  let succeededCount = 0;
  const failedList = [];
  let abortReason = "";

  try {
    const qData = await proxyRequest(serverUrl, `/api/unbookmark-queue?limit=${batchLimit}`);
    const items = qData.items || [];
    if (!items.length) {
      setStatus("待取消收藏佇列為空", "ok");
      return;
    }

    bgTab = await chrome.tabs.create({ url: "about:blank", active: false });

    for (let i = 0; i < items.length; i++) {
      if (unbookmarkStopRequested) break;
      const item = items[i];
      setStatus(`取消收藏中 (${i + 1}/${items.length})：${item.tweet_id}...`);

      await chrome.tabs.update(bgTab.id, { url: `https://x.com/i/status/${item.tweet_id}` });
      await waitTabComplete(bgTab.id, 15000);
      if (unbookmarkStopRequested) break;

      let stepRes;
      try {
        const execRes = await chrome.scripting.executeScript({
          target: { tabId: bgTab.id },
          func: unbookmarkInStatusPage,
          args: [item.tweet_id],
        });
        stepRes = execRes?.[0]?.result || { status: "failed", reason: "無回傳結果" };
      } catch (err) {
        stepRes = { status: "failed", reason: err.message || "腳本執行失敗" };
      }

      if (stepRes.status === "succeeded") {
        succeededCount++;
        await proxyRequest(serverUrl, "/api/unbookmark-ack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ succeeded: [item.tweet_id], failed: [] }),
        });
      } else if (stepRes.status === "abort") {
        abortReason = stepRes.reason || "觸發風控保護";
        failedList.push({ tweet_id: item.tweet_id, reason: abortReason });
        break;
      } else {
        failedList.push({ tweet_id: item.tweet_id, reason: stepRes.reason || "失敗" });
      }

      if (i < items.length - 1 && !unbookmarkStopRequested) {
        const delayMs = 2500 + Math.floor(Math.random() * 2501);
        setStatus(
          `已完成 ${succeededCount} 筆（失敗 ${failedList.length} 筆），隨機等待 ${(delayMs / 1000).toFixed(1)} 秒...`
        );
        await cancellableSleep(delayMs);
      }
    }

    if (failedList.length > 0) {
      await proxyRequest(serverUrl, "/api/unbookmark-ack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ succeeded: [], failed: failedList }),
      });
    }

    const remaining = await refreshQueueCount();
    const remLabel = remaining >= 0 ? `｜剩餘佇列 ${remaining} 則` : "";
    if (abortReason) {
      setStatus(`${abortReason}\n本次成功 ${succeededCount} 則｜失敗 ${failedList.length} 則${remLabel}`, "err");
    } else if (unbookmarkStopRequested) {
      setStatus(`已手動停止\n本次成功 ${succeededCount} 則｜失敗 ${failedList.length} 則${remLabel}`, "ok");
    } else {
      setStatus(`批次取消收藏完成\n成功 ${succeededCount} 則｜失敗 ${failedList.length} 則${remLabel}`, "ok");
    }
  } catch (e) {
    setStatus(`取消收藏錯誤：${e.message || String(e)}`, "err");
  } finally {
    if (bgTab && bgTab.id) {
      try {
        await chrome.tabs.remove(bgTab.id);
      } catch (_) {}
    }
    isUnbookmarking = false;
    unbookmarkStopRequested = false;
    startBtn.disabled = false;
    stopUnbookmarkBtn.disabled = true;
    await refreshQueueCount();
  }
});
