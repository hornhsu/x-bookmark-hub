chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.source !== "xbh-proxy" || msg.action !== "fetch") return;
  (async () => {
    try {
      let apiKey = msg.apiKey || "";
      if (!apiKey) {
        const saved = await chrome.storage.local.get(["apiKey"]);
        apiKey = saved.apiKey || "";
      }
      const headers = Object.assign({}, msg.headers || {});
      if (apiKey && !headers["X-API-Key"]) {
        headers["X-API-Key"] = apiKey;
      }
      const resp = await fetch(msg.url, {
        method: msg.method || "GET",
        headers,
        body: msg.body,
      });
      const text = await resp.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (_) {}
      sendResponse({ ok: resp.ok, status: resp.status, text, data });
    } catch (err) {
      sendResponse({ ok: false, status: 0, error: err.message || String(err) });
    }
  })();
  return true;
});
