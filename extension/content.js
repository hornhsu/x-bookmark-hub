(async () => {
  const cfg = window.__xbhConfig || {
    serverUrl: "http://127.0.0.1:7799",
    mode: "incremental",
    threshold: 15,
  };
  const SERVER = cfg.serverUrl.replace(/\/+$/, "");
  const BATCH_SIZE = 30;
  const SCROLL_PAUSE_MS = 1600;
  const MAX_NO_NEW = 6;
  const TWITTER_EPOCH = 1288834974657n;

  function notify(type, text) {
    try {
      chrome.runtime.sendMessage({ source: "xbh", type, text });
    } catch (_) {}
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function snowflakeToIso(idStr) {
    try {
      const id = BigInt(idStr);
      if (id < (1n << 22n)) return "";
      const ms = Number((id >> 22n) + TWITTER_EPOCH);
      return new Date(ms).toISOString();
    } catch {
      return "";
    }
  }

  function isPromotedCard(el) {
    const txt = (el.innerText || "").trim();
    const lines = txt.split("\n").map((s) => s.trim()).filter(Boolean);
    const tail = lines.slice(-3);
    if (tail.some((l) => l === "Ad" || l === "Promoted" || l === "廣告" || l === "推广")) {
      return true;
    }
    return false;
  }

  function extractFromCard(card, orderIdx) {
    try {
      if (isPromotedCard(card)) return null;

      // Locate canonical status link (prioritize link wrapping <time>, then first status link/data-href)
      let statusPath = "";
      const timeEl = card.querySelector("time");
      if (timeEl) {
        const timeLink = timeEl.closest('a[href*="/status/"]');
        if (timeLink) statusPath = timeLink.getAttribute("href") || "";
      }
      if (!statusPath) {
        const candidates = card.querySelectorAll('a[href*="/status/"], [data-href*="/status/"]');
        for (const el of candidates) {
          const href = el.getAttribute("href") || el.getAttribute("data-href") || "";
          if (/^\/[^\/]+\/status\/\d+/.test(href) && !href.includes("/photo/") && !href.includes("/analytics")) {
            statusPath = href;
            break;
          }
        }
      }

      const match = statusPath.match(/^\/([^\/]+)\/status\/(\d+)/);
      if (!match) return null;

      const authorHandle = "@" + match[1];
      const tweetId = match[2];
      const url = `https://x.com/${match[1]}/status/${tweetId}`;

      // Created timestamp
      const createdAt = (timeEl && timeEl.getAttribute("datetime")) || snowflakeToIso(tweetId);

      // Author display name
      let authorName = "";
      const userNameBox = card.querySelector('[data-testid="User-Name"]');
      if (userNameBox) {
        const firstLine = (userNameBox.innerText || "").split("\n")[0].trim();
        if (firstLine && !firstLine.startsWith("@")) authorName = firstLine;
      }
      if (!authorName) {
        for (const el of card.querySelectorAll("span, div")) {
          const cls = String(el.getAttribute("class") || "");
          if (cls.includes("font-bold")) {
            const t = (el.innerText || "").trim();
            if (t && !t.startsWith("@")) {
              authorName = t;
              break;
            }
          }
        }
      }

      // Text & Quoted text (supports both legacy tweetText and /i/history Tailwind markup)
      let text = "";
      let quotedText = "";
      const tweetTextEls = [...card.querySelectorAll('[data-testid="tweetText"]')];
      if (tweetTextEls.length > 0) {
        text = (tweetTextEls[0].innerText || "").trim();
        if (tweetTextEls.length > 1) {
          quotedText = (tweetTextEls[1].innerText || "").trim();
        }
      } else {
        const preEls = [...card.querySelectorAll("*")].filter((el) => {
          const cls = String(el.getAttribute("class") || "");
          return cls.includes("whitespace-pre") && (el.innerText || "").trim().length > 0;
        });
        if (preEls.length > 0) {
          text = (preEls[0].innerText || "").trim();
          if (preEls.length > 1) {
            const second = (preEls[1].innerText || "").trim();
            if (second !== text && !text.includes(second)) {
              quotedText = second;
            }
          }
        }
      }

      // Media URLs (photos + video posters + card images, excluding avatars/emojis)
      const mediaSet = new Set();
      for (const img of card.querySelectorAll('img[src*="pbs.twimg.com/"]')) {
        const src = img.getAttribute("src") || "";
        if (
          src &&
          !src.includes("profile_images") &&
          !src.includes("/emoji/") &&
          !src.includes("semantic_web_img")
        ) {
          mediaSet.add(src);
        }
      }
      for (const vid of card.querySelectorAll("video[poster]")) {
        const poster = vid.getAttribute("poster") || "";
        if (poster) mediaSet.add(poster);
      }
      const mediaUrls = [...mediaSet];
      const hasVideo =
        Boolean(card.querySelector("video")) ||
        mediaUrls.some((u) => u.includes("video_thumb"));

      // External links
      const externalLinks = [];
      const seenLinks = new Set();
      for (const a of card.querySelectorAll('a[href^="https://t.co/"]')) {
        if (!seenLinks.has(a.href)) {
          seenLinks.add(a.href);
          externalLinks.push({
            text: (a.innerText || "").trim().slice(0, 140),
            url: a.href,
          });
        }
      }

      // Engagement metrics when present
      function getMetric(testid) {
        const el = card.querySelector(`[data-testid="${testid}"]`);
        if (!el) return 0;
        const aria = el.getAttribute("aria-label") || el.innerText || "";
        const m = aria.replace(/,/g, "").match(/\d+/);
        return m ? parseInt(m[0], 10) : 0;
      }

      return {
        tweet_id: tweetId,
        url,
        author_name: authorName,
        author_handle: authorHandle,
        created_at: createdAt,
        text,
        quoted_text: quotedText,
        media_urls: mediaUrls,
        has_video: hasVideo,
        external_links: externalLinks,
        like_count: getMetric("like"),
        retweet_count: getMetric("retweet"),
        reply_count: getMetric("reply"),
        bookmark_order: orderIdx,
      };
    } catch {
      return null;
    }
  }

  async function proxyRequest(path, { method = "GET", headers = {}, body = undefined } = {}) {
    const sendOnce = () =>
      new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          {
            source: "xbh-proxy",
            action: "fetch",
            url: `${SERVER}${path}`,
            method,
            headers,
            body,
            apiKey: cfg.apiKey || "",
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

    let res;
    try {
      res = await sendOnce();
    } catch (err) {
      if (String(err.message || "").includes("Receiving end does not exist")) {
        await sleep(200);
        try {
          res = await sendOnce();
        } catch (retryErr) {
          if (String(retryErr.message || "").includes("Receiving end does not exist")) {
            throw new Error("背景代理未啟動，請先到 chrome://extensions/ 點擊 X Bookmark Hub 的「重新載入 (↻)」按鈕後再試");
          }
          throw retryErr;
        }
      } else {
        throw err;
      }
    }
    if (!res) throw new Error(`無法透過背景代理連線到 Server (${SERVER})`);
    if (res.status === 401) {
      throw new Error("Server 回傳 401 Unauthorized，請在擴充功能填入正確的 API Key");
    }
    if (!res.ok) {
      throw new Error(res.error ? `無法連線到 Server (${SERVER})：${res.error}` : `Server ${res.status}: ${res.text || ""}`);
    }
    return res.data;
  }

  async function postBatch(batch) {
    if (!batch.length) return { inserted: 0, updated: 0 };
    return proxyRequest("/api/bookmarks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(batch),
    });
  }

  try {
    let knownIds = new Set();
    if (cfg.mode === "incremental") {
      notify("progress", "查詢資料庫已知書籤 ID...");
      try {
        const data = await proxyRequest("/api/bookmarks/known-ids?complete_only=1");
        knownIds = new Set((data && data.ids) || []);
      } catch (e) {
        if (String(e.message || "").includes("401")) throw e;
        throw new Error(`無法連線到 Server (${SERVER})，請確認服務已啟動且已授權存取（${e.message}）`);
      }
    }

    const collected = new Map();
    let pendingBatch = [];
    let totalSent = 0;
    let totalInserted = 0;
    let totalUpdated = 0;
    let noNewScrolls = 0;
    let consecutiveKnown = 0;
    let stopReason = "end_of_list";

    while (!window.__xbhStop) {
      const nodes = document.querySelectorAll('article, li[data-index]');
      let newThisPass = 0;

      for (const node of nodes) {
        // Avoid double-parsing an <li> that contains an <article>
        if (node.tagName === "LI" && node.querySelector("article")) continue;

        const item = extractFromCard(node, collected.size);
        if (!item || collected.has(item.tweet_id)) continue;

        collected.set(item.tweet_id, item);
        pendingBatch.push(item);
        newThisPass++;

        if (cfg.mode === "incremental" && knownIds.size > 0) {
          if (knownIds.has(item.tweet_id)) {
            consecutiveKnown++;
          } else {
            consecutiveKnown = 0;
          }
          if (consecutiveKnown >= cfg.threshold) {
            stopReason = "incremental_caught_up";
            break;
          }
        }
      }

      if (pendingBatch.length >= BATCH_SIZE) {
        const toSend = pendingBatch.splice(0, pendingBatch.length);
        const r = await postBatch(toSend);
        totalSent += toSend.length;
        totalInserted += r.inserted || 0;
        totalUpdated += r.updated || 0;
      }

      notify(
        "progress",
        `已掃描 ${collected.size} 則｜已傳送 ${totalSent} 則（新增 ${totalInserted} / 更新 ${totalUpdated}）` +
          (cfg.mode === "incremental" ? `\n連續已知：${consecutiveKnown}/${cfg.threshold}` : "")
      );

      if (stopReason === "incremental_caught_up") break;

      if (newThisPass === 0) {
        noNewScrolls++;
        if (noNewScrolls >= MAX_NO_NEW) {
          stopReason = "end_of_list";
          break;
        }
        await sleep(2200);
      } else {
        noNewScrolls = 0;
      }

      window.scrollBy(0, Math.floor(window.innerHeight * 0.9));
      await sleep(SCROLL_PAUSE_MS);
    }

    if (window.__xbhStop) stopReason = "manual_stop";

    if (pendingBatch.length > 0) {
      const r = await postBatch(pendingBatch);
      totalSent += pendingBatch.length;
      totalInserted += r.inserted || 0;
      totalUpdated += r.updated || 0;
    }

    const reasonLabel = {
      incremental_caught_up: "增量同步完成（已銜接舊紀錄）",
      end_of_list: "已捲動至書籤底部",
      manual_stop: "已手動停止",
    }[stopReason] || stopReason;

    notify(
      "done",
      `${reasonLabel}\n共掃描 ${collected.size} 則｜新增 ${totalInserted} 則｜更新 ${totalUpdated} 則`
    );
  } catch (e) {
    notify("error", e.message || String(e));
  }
})();
