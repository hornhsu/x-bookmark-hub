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
  const apiKey = currentApiKey();
  const reqHeaders = Object.assign({}, headers);
  if (apiKey && !reqHeaders["X-API-Key"]) {
    reqHeaders["X-API-Key"] = apiKey;
  }
  const resp = await fetch(`${serverUrl}${path}`, {
    method,
    headers: reqHeaders,
    body,
  });
  if (resp.status === 401) throw new Error("API Key 錯誤或未填入 (HTTP 401)");
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {}
  if (!resp.ok) throw new Error((data && data.error) || text || `HTTP ${resp.status}`);
  return data;
}

async function pingBackgroundWorker() {
  const attempt = () =>
    new Promise((resolve) => {
      chrome.runtime.sendMessage({ source: "xbh-proxy", action: "ping" }, (resp) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
          return;
        }
        resolve({ ok: Boolean(resp && resp.ok), error: "" });
      });
    });
  let res = await attempt();
  if (!res.ok && String(res.error || "").includes("Receiving end does not exist")) {
    await new Promise((r) => setTimeout(r, 200));
    res = await attempt();
  }
  return res;
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
    const sw = await pingBackgroundWorker();
    if (!sw.ok) {
      setStatus(
        `伺服器連線正常（驗證：${health.auth_required ? "已啟用" : "未啟用"}｜共 ${stats.total} 則書籤）\n` +
          "⚠️ 背景代理尚未註冊：請到 chrome://extensions/ 點擊「重新載入 (↻)」以啟用 v1.2.0 背景代理。",
        "err"
      );
    } else {
      setStatus(`連線成功！（驗證：${health.auth_required ? "已啟用" : "未啟用"}）\n資料庫共 ${stats.total} 則書籤`, "ok");
    }
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

function navigateTabAndWait(tabId, targetUrl, tweetId, timeoutMs = 20000) {
  return new Promise((resolve) => {
    let done = false;
    const isTargetUrl = (u) => {
      if (!u || !u.includes("x.com")) return false;
      if (!tweetId) return true;
      return u.includes(`/status/${tweetId}`) || u.includes("/login") || u.includes("/i/flow/login");
    };
    const finish = async (ok) => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      if (ok) {
        await new Promise((r) => setTimeout(r, 600));
      }
      resolve(ok);
    };
    const listener = (updatedId, info, tab) => {
      if (updatedId === tabId && info.status === "complete" && isTargetUrl(tab?.url || "")) {
        finish(true);
      }
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.update(tabId, { url: targetUrl }).catch(() => finish(false));
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

// X web client's own internal GraphQL endpoint (the same request x.com sends when you click
// the bookmark button with your logged-in session). This is NOT the paid X developer API.
// queryId / bearer are discovered from the loaded x.com bundle at runtime; constants are fallbacks only.
const FALLBACK_DELETE_BOOKMARK_QID = "Wlmlj2-xzyS1GN3a6cj-mQ";
const FALLBACK_WEB_BEARER =
  "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA";

// Injected into an x.com tab (MAIN world). Returns { status: succeeded|failed|abort|fallback, reason, rateRemaining }.
async function unbookmarkViaGraphQL(tweetId, fallbackQid, fallbackBearer) {
  if (location.pathname.startsWith("/i/flow/login") || location.pathname === "/login") {
    return { status: "abort", reason: "未偵測到 X 登入狀態，已立即中止" };
  }
  const ct0 = (document.cookie.match(/(?:^|;\s*)ct0=([^;]+)/) || [])[1] || "";
  if (!ct0) {
    return { status: "abort", reason: "未偵測到 X 登入狀態（缺少 ct0），已立即中止" };
  }

  if (!window.__xbhGqlCfg) {
    const found = { queryId: "", bearer: "", source: "fallback" };
    try {
      const srcs = [...document.querySelectorAll("script[src]")]
        .map((s) => s.src)
        .filter((u) => /abs\.twimg\.com\/responsive-web\/.+\/main\.[^/]+\.js/.test(u));
      for (const src of srcs) {
        const txt = await (await fetch(src)).text();
        const q = txt.match(/queryId:"([^"]+)",operationName:"DeleteBookmark"/);
        const b = txt.match(/"Bearer (AAAAAAAAAAAAAAAAAAAAA[^"]+)"/) || txt.match(/"(AAAAAAAAAAAAAAAAAAAAA[A-Za-z0-9%]{40,})"/);
        if (q) found.queryId = q[1];
        if (b) found.bearer = b[1];
        if (found.queryId) {
          found.source = "bundle";
          break;
        }
      }
    } catch (_) {}
    if (!found.queryId) found.queryId = fallbackQid;
    if (!found.bearer) found.bearer = fallbackBearer;
    window.__xbhGqlCfg = found;
  }
  const cfg = window.__xbhGqlCfg;

  let resp;
  try {
    resp = await fetch(`/i/api/graphql/${cfg.queryId}/DeleteBookmark`, {
      method: "POST",
      credentials: "include",
      headers: {
        authorization: `Bearer ${cfg.bearer}`,
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "content-type": "application/json",
      },
      body: JSON.stringify({ variables: { tweet_id: tweetId }, queryId: cfg.queryId }),
    });
  } catch (err) {
    return { status: "fallback", reason: `API 連線失敗：${err.message || err}` };
  }

  const remainingHdr = resp.headers.get("x-rate-limit-remaining");
  const rateRemaining = remainingHdr === null ? null : Number(remainingHdr);
  if (resp.status === 429) {
    return { status: "abort", reason: "X 回傳 429 限流，已立即中止" };
  }
  let text = "";
  let data = null;
  try {
    text = await resp.text();
    data = text ? JSON.parse(text) : null;
  } catch (_) {}
  if (!resp.ok) {
    return { status: "fallback", reason: `API HTTP ${resp.status}（queryId 來源：${cfg.source}）` };
  }
  if (data && data.data && data.data.tweet_bookmark_delete === "Done") {
    return { status: "succeeded", reason: "removed_api", rateRemaining };
  }
  const errMsg = ((data && data.errors) || [])
    .map((e) => `${e.code || ""} ${e.message || ""}`.trim())
    .join("; ");
  if (/rate limit|\b88\b/i.test(errMsg)) {
    return { status: "abort", reason: "X 回報限流，已立即中止" };
  }
  if (/not bookmarked|haven.?t bookmarked|not in .*bookmark|bookmark.*not found/i.test(errMsg)) {
    return { status: "succeeded", reason: "already_unbookmarked", rateRemaining };
  }
  if (/no status found|deleted|\b144\b|not found/i.test(errMsg)) {
    return { status: "failed", reason: `貼文不存在或無法檢視（${errMsg.slice(0, 60)}）` };
  }
  return { status: "fallback", reason: `API 非預期回應：${(errMsg || text || "").slice(0, 80)}` };
}

// Injected into a status page (MAIN world). Reports bookmark state of the focal tweet.
// wantCoords=true: returns as soon as state is known, with viewport coords of the "Bookmarked" button.
// wantCoords=false: keeps polling until the button flips to not-bookmarked (or timeout).
async function probeBookmark(tweetId, timeoutMs, wantCoords) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const removeRe = /remove\s*bookmark|bookmarked|移除書籤|取消書籤|已加入書籤|已收藏|取消收藏|从书签中移除|ブックマークを削除/i;
  const addRe = /(?:^|[\s.。])(bookmark|加入書籤|加入书签|書籤|书签|收藏|ブックマーク)(?:$|[\s.。])/i;
  const rateRe = /rate limit exceeded|too many requests|something went wrong\.?\s*try reloading/i;
  const toastOkRe = /removed from your bookmarks|已從你的?書籤中?移除|已从你的?书签中?移除|ブックマークから削除/i;
  const deletedRe = /this page doesn.?t exist|this post was deleted|this account doesn.?t exist|unable to view this post|此頁面不存在|此貼文已遭刪除|無法查看此貼文/i;

  function findFocalContainer() {
    const articles = [...document.querySelectorAll("article")];
    for (const art of articles) {
      for (const t of art.querySelectorAll("time")) {
        const link = t.closest('a[href*="/status/"]');
        if (link && (link.getAttribute("href") || "").includes(`/status/${tweetId}`)) return art;
      }
    }
    for (const art of articles) {
      if (art.getAttribute("tabindex") === "-1") return art;
    }
    for (const art of articles) {
      if (art.querySelector(`a[href*="/status/${tweetId}"]`)) return art;
    }
    if (articles.length === 1) return articles[0];
    if (articles.length === 0) {
      const primary = document.querySelector('[data-testid="primaryColumn"], main');
      if (primary && primary.querySelector('button[aria-label], [role="button"][aria-label]')) return primary;
    }
    return null;
  }

  function findButton(root, testId, labelRe, excludeRe = null) {
    const byId = root.querySelector(`[data-testid="${testId}"]`);
    if (byId) return byId;
    for (const btn of root.querySelectorAll('button[aria-label], [role="button"][aria-label]')) {
      const lbl = (btn.getAttribute("aria-label") || "").trim();
      if (excludeRe && excludeRe.test(lbl)) continue;
      if (labelRe.test(lbl)) return btn;
    }
    return null;
  }

  let last = null;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (location.pathname.startsWith("/i/flow/login") || location.pathname === "/login") {
      return { state: "abort", reason: "未偵測到 X 登入狀態，已立即中止" };
    }
    const bodyText = (document.body?.innerText || "").trim();
    if (rateRe.test(bodyText)) {
      return { state: "abort", reason: "偵測到 X 限流或錯誤頁面（429），已立即中止" };
    }
    const toastEl = document.querySelector('[data-testid="toast"]');
    if (!wantCoords && toastEl && toastOkRe.test(toastEl.innerText || "")) {
      return { state: "not_bookmarked", label: "toast" };
    }
    const focal = findFocalContainer();
    if (focal) {
      const rm = findButton(focal, "removeBookmark", removeRe);
      if (rm) {
        const label = (rm.getAttribute("aria-label") || "").trim();
        if (!wantCoords) {
          last = { state: "bookmarked", label };
        } else {
          rm.scrollIntoView({ block: "center" });
          await sleep(300);
          const r = rm.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) {
            return { state: "bookmarked", label, x: r.left + r.width / 2, y: r.top + r.height / 2 };
          }
        }
      } else {
        const add = findButton(focal, "bookmark", addRe, removeRe);
        if (add) return { state: "not_bookmarked", label: (add.getAttribute("aria-label") || "").trim() };
      }
    } else if (deletedRe.test(bodyText)) {
      return { state: "failed", reason: "貼文不存在或無法檢視" };
    }
    await sleep(300);
  }
  if (last) return last;
  return {
    state: "failed",
    reason: `載入逾時 (${location.pathname}, articles=${document.querySelectorAll("article").length})`,
  };
}

async function runInTab(tabId, func, args) {
  const exec = () => chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func, args });
  let res;
  try {
    res = await exec();
  } catch (_) {
    await new Promise((r) => setTimeout(r, 1000));
    res = await exec();
  }
  return res?.[0]?.result;
}

let debuggerTabId = null;

async function ensureDebugger(tabId) {
  if (debuggerTabId === tabId) return;
  await chrome.debugger.attach({ tabId }, "1.3");
  debuggerTabId = tabId;
  try {
    await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true });
  } catch (_) {}
}

async function detachDebugger() {
  if (debuggerTabId === null) return;
  const tabId = debuggerTabId;
  debuggerTabId = null;
  try {
    await chrome.debugger.detach({ tabId });
  } catch (_) {}
}

// Real (isTrusted) mouse click via Chrome DevTools Protocol.
async function trustedClick(tabId, x, y) {
  const send = (params) => chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", params);
  await send({ type: "mouseMoved", x, y });
  await send({ type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await new Promise((r) => setTimeout(r, 60));
  await send({ type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
}

function statusUrlFor(item) {
  const rawUrl = String(item.url || "").trim();
  return /^https:\/\/(?:x|twitter)\.com\/.+\/status\/\d+/.test(rawUrl) && rawUrl.includes(`/status/${item.tweet_id}`)
    ? rawUrl.replace(/^https:\/\/twitter\.com/, "https://x.com")
    : `https://x.com/i/status/${item.tweet_id}`;
}

async function unbookmarkViaDebugger(tabId, item) {
  await navigateTabAndWait(tabId, statusUrlFor(item), item.tweet_id, 20000);
  const pre = await runInTab(tabId, probeBookmark, [item.tweet_id, 15000, true]);
  if (!pre) return { status: "failed", reason: "無回傳結果" };
  if (pre.state === "abort") return { status: "abort", reason: pre.reason };
  if (pre.state === "not_bookmarked") return { status: "succeeded", reason: "already_unbookmarked" };
  if (pre.state !== "bookmarked") return { status: "failed", reason: pre.reason || "找不到書籤按鈕" };

  try {
    await ensureDebugger(tabId);
    await trustedClick(tabId, pre.x, pre.y);
  } catch (err) {
    return { status: "failed", reason: `真實點擊失敗：${err.message || err}` };
  }

  const post = await runInTab(tabId, probeBookmark, [item.tweet_id, 6000, false]);
  if (!post) return { status: "failed", reason: "點擊後無回傳結果" };
  if (post.state === "abort") return { status: "abort", reason: post.reason };
  if (post.state === "not_bookmarked") return { status: "succeeded", reason: "removed_click" };
  return { status: "failed", reason: `真實點擊後仍未切換 (${post.label || post.reason || ""})` };
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
  let removedCount = 0;
  let alreadyCount = 0;
  const failedList = [];
  let abortReason = "";
  let apiBroken = false;
  let apiFallbackReason = "";

  try {
    const qData = await proxyRequest(serverUrl, `/api/unbookmark-queue?limit=${batchLimit}`);
    const items = qData.items || [];
    if (!items.length) {
      setStatus("待取消收藏佇列為空", "ok");
      return;
    }

    bgTab = await chrome.tabs.create({ url: "about:blank", active: false });
    setStatus("開啟 X 背景分頁中...");
    await navigateTabAndWait(bgTab.id, "https://x.com/home", null, 20000);

    for (let i = 0; i < items.length; i++) {
      if (unbookmarkStopRequested) break;
      const item = items[i];
      const modeLabel = apiBroken ? "真實點擊" : "API";
      setStatus(`取消收藏中 (${i + 1}/${items.length})［${modeLabel}］：${item.tweet_id}...`);

      let stepRes = null;
      if (!apiBroken) {
        try {
          stepRes = await runInTab(bgTab.id, unbookmarkViaGraphQL, [
            item.tweet_id,
            FALLBACK_DELETE_BOOKMARK_QID,
            FALLBACK_WEB_BEARER,
          ]);
        } catch (err) {
          stepRes = { status: "fallback", reason: err.message || "API 腳本執行失敗" };
        }
        if (!stepRes) stepRes = { status: "fallback", reason: "API 無回傳結果" };
        if (stepRes.status === "fallback") {
          apiBroken = true;
          apiFallbackReason = stepRes.reason || "";
        }
      }
      if (apiBroken && (!stepRes || stepRes.status === "fallback")) {
        if (unbookmarkStopRequested) break;
        setStatus(`取消收藏中 (${i + 1}/${items.length})［真實點擊］：${item.tweet_id}...`);
        try {
          stepRes = await unbookmarkViaDebugger(bgTab.id, item);
        } catch (err) {
          stepRes = { status: "failed", reason: err.message || "真實點擊流程失敗" };
        }
      }

      if (stepRes.status === "succeeded") {
        succeededCount++;
        if (stepRes.reason === "already_unbookmarked") alreadyCount++;
        else removedCount++;
        await proxyRequest(serverUrl, "/api/unbookmark-ack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ succeeded: [item.tweet_id], failed: [] }),
        });
        if (typeof stepRes.rateRemaining === "number" && !Number.isNaN(stepRes.rateRemaining) && stepRes.rateRemaining <= 1) {
          abortReason = `X API 剩餘額度 ${stepRes.rateRemaining}，為避免限流已暫停`;
          break;
        }
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
    const okDetail = succeededCount > 0 ? `（實刪 ${removedCount} / 原已取消 ${alreadyCount}）` : "";
    const modeText = apiBroken ? `\n模式：API 不可用，已改用真實點擊（${apiFallbackReason.slice(0, 60)}）` : "\n模式：GraphQL API";
    const detailText = failedList.length
      ? "\n失敗原因：\n" + failedList.slice(0, 3).map((f) => `- ${f.tweet_id}: ${f.reason}`).join("\n")
      : "";
    const summary = `成功 ${succeededCount} 則${okDetail}｜失敗 ${failedList.length} 則${remLabel}${modeText}${detailText}`;
    if (abortReason) {
      setStatus(`${abortReason}\n${summary}`, "err");
    } else if (unbookmarkStopRequested) {
      setStatus(`已手動停止\n${summary}`, "ok");
    } else {
      setStatus(`批次取消收藏完成\n${summary}`, succeededCount === 0 && failedList.length > 0 ? "err" : "ok");
    }
  } catch (e) {
    setStatus(`取消收藏錯誤：${e.message || String(e)}`, "err");
  } finally {
    await detachDebugger();
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
