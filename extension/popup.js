const serverInput = document.getElementById("serverUrl");
const modeSelect = document.getElementById("modeSelect");
const thresholdInput = document.getElementById("thresholdInput");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusBox = document.getElementById("status");
const openUiLink = document.getElementById("openUiLink");

function setStatus(text, cls = "") {
  statusBox.textContent = text;
  statusBox.className = cls;
}

function normalizedServer() {
  return (serverInput.value || "http://127.0.0.1:7799").trim().replace(/\/+$/, "");
}

chrome.storage.local.get(["serverUrl", "mode", "threshold"], (saved) => {
  if (saved.serverUrl) serverInput.value = saved.serverUrl;
  if (saved.mode) modeSelect.value = saved.mode;
  if (saved.threshold) thresholdInput.value = saved.threshold;
  openUiLink.href = normalizedServer();
});

serverInput.addEventListener("change", () => {
  const s = normalizedServer();
  openUiLink.href = s;
  chrome.storage.local.set({ serverUrl: s });
});
modeSelect.addEventListener("change", () => chrome.storage.local.set({ mode: modeSelect.value }));
thresholdInput.addEventListener("change", () => chrome.storage.local.set({ threshold: Number(thresholdInput.value) || 15 }));

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
  const mode = modeSelect.value;
  const threshold = Math.max(3, Number(thresholdInput.value) || 15);
  chrome.storage.local.set({ serverUrl, mode, threshold });

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
      args: [{ serverUrl, mode, threshold }],
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
