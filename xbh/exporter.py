"""Exporters for standalone offline HTML and monthly-archived Obsidian Vaults."""

from collections import Counter
import html
import json
from pathlib import Path
import re
import shutil
from typing import Any

CONTENT_KIND_LABELS = {
    "tutorial": "教學／工作流",
    "showcase": "作品／展示",
    "review": "評測／對比",
    "news": "發布／新聞",
    "question": "提問",
}


def _safe_slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]+", "_", (text or "").strip()).strip("_") or "item"


def _copy_item_media(items: list[dict[str, Any]], src_media_dir: Path, dst_media_dir: Path, copy_media: bool) -> dict[str, list[str]]:
    """Copy local media assets if available and return per-tweet resolved relative paths."""
    if copy_media:
        dst_media_dir.mkdir(parents=True, exist_ok=True)
    resolved_map: dict[str, list[str]] = {}
    for it in items:
        tid = str(it.get("tweet_id") or it.get("id") or "")
        locals_ = it.get("local_media") or []
        remotes = it.get("media_urls") or []
        out_list: list[str] = []
        if locals_:
            for rel in locals_:
                rel_clean = str(rel).replace("\\", "/").lstrip("./")
                fname = Path(rel_clean).name
                src_file = src_media_dir / fname
                if copy_media and src_file.exists() and src_file.is_file():
                    dst_file = dst_media_dir / fname
                    if not dst_file.exists():
                        shutil.copy2(src_file, dst_file)
                    out_list.append(f"media/{fname}")
                elif rel_clean.startswith("http"):
                    out_list.append(rel_clean)
                else:
                    out_list.append(f"media/{fname}")
        elif remotes:
            out_list = list(remotes)
        resolved_map[tid] = out_list
    return resolved_map


def export_offline_html(
    items: list[dict[str, Any]],
    out_path: Path,
    media_dir: Path,
    copy_media: bool = True,
    title: str = "X 書籤精選整理",
) -> dict[str, Any]:
    """Export filtered bookmarks to a self-contained single-file offline HTML page."""
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    dst_media_dir = out_file.parent / "media"
    resolved_media = _copy_item_media(items, Path(media_dir), dst_media_dir, copy_media=copy_media)

    export_items = []
    cat_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    dates = []

    for idx, it in enumerate(items):
        tid = str(it.get("tweet_id") or it.get("id") or "")
        cat = it.get("category") or "未分類"
        kind = it.get("content_kind") or ""
        models = it.get("models") or []
        cat_counts[cat] += 1
        if kind:
            kind_counts[kind] += 1
        for m in models:
            model_counts[m] += 1
        d = (it.get("created_at") or "")[:10]
        if d:
            dates.append(d)

        export_items.append(
            {
                "tweet_id": tid,
                "url": it.get("url") or "",
                "author_name": it.get("author_name") or "",
                "author_handle": it.get("author_handle") or "",
                "created_at": it.get("created_at") or "",
                "text": it.get("text") or "",
                "quoted_text": it.get("quoted_text") or "",
                "quoted_author": it.get("quoted_author") or "",
                "media": resolved_media.get(tid, []),
                "has_video": bool(it.get("has_video")),
                "like_count": int(it.get("like_count") or 0),
                "category": cat,
                "models": models,
                "content_kind": kind,
                "tags": it.get("tags") or [],
                "title": it.get("title") or "",
                "summary": it.get("summary") or "",
                "bookmark_order": it.get("bookmark_order") if it.get("bookmark_order") is not None else idx,
            }
        )

    dates.sort()
    date_range = f"{dates[0]} ~ {dates[-1]}" if dates else "N/A"
    meta = {
        "categories": [{"name": k, "count": v} for k, v in cat_counts.most_common()],
        "kinds": [{"id": k, "label": CONTENT_KIND_LABELS.get(k, k), "count": v} for k, v in kind_counts.most_common()],
        "models": [{"name": k, "count": v} for k, v in model_counts.most_common(30)],
    }

    data_json = json.dumps(export_items, ensure_ascii=False).replace("</", "<\\/")
    meta_json = json.dumps(meta, ensure_ascii=False).replace("</", "<\\/")

    doc = f"""<!doctype html>
<html lang="zh-Hant" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --bg:#0e1117; --card:#171c25; --elev:#212734; --line:#2b3344; --fg:#e8edf5; --dim:#93a0b5; --acc:#4ea1ff; --h3:#ff8f4c; }}
    [data-theme="light"] {{ --bg:#f4f6fb; --card:#fff; --elev:#edf1f8; --line:#d8dfec; --fg:#181c25; --dim:#5c667a; --acc:#2b5ce0; --h3:#d9480f; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--fg); font:14.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif; }}
    header {{ position:sticky; top:0; z-index:10; background:color-mix(in srgb,var(--bg) 92%,transparent); backdrop-filter:blur(8px); border-bottom:1px solid var(--line); padding:14px 20px; }}
    .wrap {{ max-width:1380px; margin:0 auto; }}
    .top {{ display:flex; flex-wrap:wrap; gap:10px; justify-content:space-between; align-items:center; margin-bottom:10px; }}
    h1 {{ margin:0; font-size:1.25rem; }}
    .sub {{ font-size:0.84rem; color:var(--dim); }}
    .controls {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
    input[type=search], select, button.btn {{ background:var(--elev); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:7px 11px; font-size:0.88rem; }}
    input[type=search] {{ width:min(320px,80vw); }}
    button.btn {{ cursor:pointer; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:7px; align-items:center; }}
    .chip {{ background:var(--elev); color:var(--dim); border:1px solid var(--line); border-radius:999px; padding:3px 10px; font-size:0.79rem; cursor:pointer; }}
    .chip.on {{ background:var(--acc); color:#041222; border-color:var(--acc); font-weight:600; }}
    .chip.h3.on {{ background:var(--h3); border-color:var(--h3); color:#fff; }}
    main {{ max-width:1380px; margin:0 auto; padding:18px 20px 56px; }}
    .info {{ display:flex; justify-content:space-between; color:var(--dim); font-size:0.85rem; margin-bottom:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(350px,1fr)); gap:16px; }}
    .grid.masonry {{ display:block; columns:350px; column-gap:16px; }}
    .grid.masonry .card {{ break-inside:avoid; margin-bottom:16px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }}
    .media {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:2px; background:#05070a; }}
    .media img {{ width:100%; max-height:260px; object-fit:cover; display:block; cursor:zoom-in; }}
    .body {{ padding:13px 15px; display:flex; flex-direction:column; gap:8px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:8px; font-size:0.79rem; color:var(--dim); align-items:center; }}
    .badge {{ background:color-mix(in srgb,var(--acc) 18%,transparent); color:var(--acc); border-radius:6px; padding:1px 7px; font-size:0.75rem; font-weight:600; }}
    .badge.v {{ background:color-mix(in srgb,var(--h3) 20%,transparent); color:var(--h3); }}
    .title {{ margin:0; font-size:1rem; line-height:1.4; }}
    .sum {{ margin:0; font-size:0.88rem; color:var(--dim); }}
    .txt {{ white-space:pre-wrap; word-break:break-word; font-size:0.88rem; }}
    .q {{ border-left:3px solid var(--acc); padding:6px 10px; background:var(--elev); font-size:0.82rem; color:var(--dim); white-space:pre-wrap; border-radius:0 6px 6px 0; }}
    .tags {{ display:flex; flex-wrap:wrap; gap:5px; }}
    .tag {{ background:var(--elev); border:1px solid var(--line); border-radius:6px; padding:1px 7px; font-size:0.74rem; color:var(--dim); }}
    a {{ color:var(--acc); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .lb {{ position:fixed; inset:0; background:rgba(0,0,0,.88); display:none; align-items:center; justify-content:center; z-index:100; padding:24px; }}
    .lb.open {{ display:flex; }}
    .lb img {{ max-width:94vw; max-height:90vh; border-radius:8px; }}
  </style>
</head>
<body>
<header><div class="wrap">
  <div class="top">
    <div>
      <h1>{html.escape(title)}</h1>
      <div class="sub">時間範圍：{html.escape(date_range)} ｜ 共 {len(export_items)} 則書籤（離線版）</div>
    </div>
    <div class="controls">
      <input type="search" id="q" placeholder="搜尋內文、標題、模型、作者..." />
      <select id="sort"><option value="newest">最新貼文</option><option value="likes">最多讚</option><option value="bookmark">收藏順序</option></select>
      <button class="btn" id="layoutBtn" type="button">切換瀑布流</button>
      <button class="btn" id="themeBtn" type="button">深淺色</button>
    </div>
  </div>
  <div class="chips" id="catChips"></div>
  <div class="chips" id="kindChips"></div>
  <div class="chips" id="modelChips"></div>
</div></header>
<main>
  <div class="info"><span id="count"></span><button class="chip" id="resetBtn" style="display:none">清除篩選</button></div>
  <div class="grid" id="grid"></div>
</main>
<div class="lb" id="lb"><img id="lbImg" src="" alt="" /></div>
<script id="data-json" type="application/json">{data_json}</script>
<script id="meta-json" type="application/json">{meta_json}</script>
<script>
const DATA = JSON.parse(document.getElementById("data-json").textContent);
const META = JSON.parse(document.getElementById("meta-json").textContent);
const KIND_LABEL = {json.dumps(CONTENT_KIND_LABELS, ensure_ascii=False)};
const state = {{ cat: "", kind: "", model: "", q: "", sort: "newest", masonry: false }};
const esc = s => String(s || "").replace(/[&<>"']/g, c => ({{ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }})[c]);

function renderChips() {{
  document.getElementById("catChips").innerHTML = `<span class="sub">分類：</span><button class="chip ${{!state.cat?'on':''}}" data-k="cat" data-v="">全部 (${{DATA.length}})</button>` +
    META.categories.map(c => `<button class="chip ${{state.cat===c.name?'on':''}}" data-k="cat" data-v="${{esc(c.name)}}">${{esc(c.name)}} (${{c.count}})</button>`).join("");
  document.getElementById("kindChips").innerHTML = META.kinds.length ? `<span class="sub">類型：</span><button class="chip ${{!state.kind?'on':''}}" data-k="kind" data-v="">全部</button>` +
    META.kinds.map(k => `<button class="chip ${{state.kind===k.id?'on':''}}" data-k="kind" data-v="${{esc(k.id)}}">${{esc(k.label)}} (${{k.count}})</button>`).join("") : "";
  document.getElementById("modelChips").innerHTML = META.models.length ? `<span class="sub">模型：</span>` +
    META.models.map(m => `<button class="chip h3 ${{state.model===m.name?'on':''}}" data-k="model" data-v="${{esc(m.name)}}">${{esc(m.name)}} (${{m.count}})</button>`).join("") : "";
  document.querySelectorAll("[data-k]").forEach(btn => btn.onclick = () => {{
    const k = btn.dataset.k, v = btn.dataset.v;
    state[k] = (state[k] === v && v !== "") ? "" : v;
    renderChips(); render();
  }});
}}

function render() {{
  const q = state.q.trim().toLowerCase();
  let rows = DATA.filter(x => {{
    if (state.cat && x.category !== state.cat) return false;
    if (state.kind && x.content_kind !== state.kind) return false;
    if (state.model && !(x.models || []).includes(state.model)) return false;
    if (q) {{
      const hay = [x.title, x.summary, x.text, x.quoted_text, x.author_name, x.author_handle, ...(x.models||[]), ...(x.tags||[])].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }}
    return true;
  }});
  rows = [...rows].sort((a,b) => state.sort === "likes" ? b.like_count - a.like_count : state.sort === "bookmark" ? a.bookmark_order - b.bookmark_order : String(b.created_at).localeCompare(String(a.created_at)));
  document.getElementById("count").textContent = `顯示 ${{rows.length}} / ${{DATA.length}} 則`;
  document.getElementById("resetBtn").style.display = (state.cat || state.kind || state.model || q) ? "inline-block" : "none";
  const grid = document.getElementById("grid");
  grid.className = state.masonry ? "grid masonry" : "grid";
  grid.innerHTML = rows.map(x => `
    <article class="card">
      ${{x.media.length ? `<div class="media">${{x.media.slice(0,4).map(m => `<img loading="lazy" src="${{esc(m)}}" alt="">`).join("")}}</div>` : ""}}
      <div class="body">
        <div class="meta">
          <span class="badge">${{esc(x.category)}}</span>
          ${{x.content_kind ? `<span class="tag">${{esc(KIND_LABEL[x.content_kind]||x.content_kind)}}</span>` : ""}}
          ${{x.has_video ? `<span class="badge v">▶ 影片</span>` : ""}}
          <span>${{esc((x.created_at||"").slice(0,10))}}</span>
          ${{x.like_count ? `<span>♥ ${{x.like_count}}</span>` : ""}}
          <a href="${{esc(x.url)}}" target="_blank" style="margin-left:auto">原推 ↗</a>
        </div>
        ${{x.title ? `<h2 class="title">${{esc(x.title)}}</h2>` : ""}}
        ${{x.summary ? `<p class="sum">${{esc(x.summary)}}</p>` : ""}}
        <div class="txt">${{esc(x.text || "（無文字）")}}</div>
        ${{x.quoted_text ? `<div class="q">${{x.quoted_author ? esc(x.quoted_author)+"：" : ""}}${{esc(x.quoted_text)}}</div>` : ""}}
        <div class="tags">${{(x.models||[]).map(m => `<span class="tag">${{esc(m)}}</span>`).join("")}} <span class="tag">${{esc(x.author_handle)}}</span></div>
      </div>
    </article>`).join("");
  grid.querySelectorAll("img").forEach(img => img.onclick = () => {{
    document.getElementById("lbImg").src = img.src;
    document.getElementById("lb").classList.add("open");
  }});
}}

document.getElementById("q").oninput = e => {{ state.q = e.target.value; render(); }};
document.getElementById("sort").onchange = e => {{ state.sort = e.target.value; render(); }};
document.getElementById("layoutBtn").onclick = () => {{ state.masonry = !state.masonry; render(); }};
document.getElementById("themeBtn").onclick = () => {{ const r = document.documentElement; r.dataset.theme = r.dataset.theme === "dark" ? "light" : "dark"; }};
document.getElementById("resetBtn").onclick = () => {{ state.cat=""; state.kind=""; state.model=""; state.q=""; document.getElementById("q").value=""; renderChips(); render(); }};
document.getElementById("lb").onclick = () => document.getElementById("lb").classList.remove("open");
document.addEventListener("keydown", e => {{ if (e.key === "Escape") document.getElementById("lb").classList.remove("open"); }});
renderChips(); render();
</script></body></html>"""

    out_file.write_text(doc, encoding="utf-8")
    return {"path": str(out_file), "count": len(export_items), "bytes": out_file.stat().st_size}


def export_obsidian_vault(
    items: list[dict[str, Any]],
    out_dir: Path,
    media_dir: Path,
    copy_media: bool = True,
) -> dict[str, Any]:
    """Export bookmarks to a monthly-archived Obsidian Vault with model MOCs."""
    vault = Path(out_dir)
    idx_dir = vault / "00-索引"
    notes_root = vault / "notes"
    dst_media = vault / "media"
    idx_dir.mkdir(parents=True, exist_ok=True)
    notes_root.mkdir(parents=True, exist_ok=True)

    resolved_media = _copy_item_media(items, Path(media_dir), dst_media, copy_media=copy_media)

    cat_groups: dict[str, list[dict[str, Any]]] = {}
    model_groups: dict[str, list[dict[str, Any]]] = {}
    note_count = 0

    for it in items:
        tid = str(it.get("tweet_id") or it.get("id") or "")
        created = it.get("created_at") or ""
        date_str = created[:10] if len(created) >= 10 else "unknown"
        month_dir = created[:7] if len(created) >= 7 else "unknown"
        handle_slug = _safe_slug((it.get("author_handle") or "user").lstrip("@"))
        note_stem = f"{date_str}_{handle_slug}_{tid}"
        month_path = notes_root / month_dir
        month_path.mkdir(parents=True, exist_ok=True)

        title = it.get("title") or (it.get("text") or "").replace("\n", " ").strip()[:50] or f"Tweet {tid}"
        cat = it.get("category") or "未分類"
        kind = it.get("content_kind") or ""
        models = it.get("models") or []
        tags = it.get("tags") or []

        cat_groups.setdefault(cat, []).append({"stem": f"notes/{month_dir}/{note_stem}", "title": title, "date": date_str, "handle": it.get("author_handle") or "", "models": models})
        for m in models:
            model_groups.setdefault(m, []).append({"stem": f"notes/{month_dir}/{note_stem}", "title": title, "date": date_str, "handle": it.get("author_handle") or ""})

        lines = [
            "---",
            f'title: "{title.replace(chr(34), chr(92)+chr(34))}"',
            f'author: "{(it.get("author_handle") or "").replace(chr(34), chr(92)+chr(34))}"',
            f"date: {date_str}",
            f'category: "{cat}"',
            f"content_kind: {kind or 'general'}",
            f"models: {json.dumps(models, ensure_ascii=False)}",
            f"tags: {json.dumps(tags, ensure_ascii=False)}",
            f"likes: {int(it.get('like_count') or 0)}",
            f'url: "{it.get("url") or ""}"',
            "---",
            "",
            f"# {title}",
            "",
            f"- **作者**：{it.get('author_name') or ''} (`{it.get('author_handle') or ''}`)",
            f"- **日期**：{date_str} | **分類**：`{cat}`" + (f" | **類型**：`{CONTENT_KIND_LABELS.get(kind, kind)}`" if kind else ""),
            f"- **模型**：{' '.join(f'`{m}`' for m in models) or '—'}",
            f"- **原文**：<{it.get('url') or ''}>",
            "",
        ]
        if it.get("summary"):
            lines.extend(["## 重點摘要", "", it["summary"], ""])
        lines.extend(["## 原文內容", "", it.get("text") or "_（無文字內容）_", ""])
        if it.get("quoted_text"):
            lines.extend(["### 引用貼文", "", "> " + it["quoted_text"].replace("\n", "\n> "), ""])

        media_list = resolved_media.get(tid, [])
        if media_list:
            lines.extend(["## 圖片與縮圖", ""])
            for mref in media_list:
                rel = f"../../{mref}" if mref.startswith("media/") else mref
                lines.append(f"![]({rel})")
            lines.append("")

        (month_path / f"{note_stem}.md").write_text("\n".join(lines), encoding="utf-8")
        note_count += 1

    # Write per-model MOCs
    for m_name, refs in sorted(model_groups.items(), key=lambda kv: -len(kv[1])):
        m_lines = [f"# 模型索引：{m_name}", "", f"共 **{len(refs)}** 篇相關筆記。[[00-索引/總覽|← 返回總覽]]", ""]
        for ref in refs:
            m_lines.append(f"- `{ref['date']}` [[{ref['stem']}|{ref['title']}]] ({ref['handle']})")
        (idx_dir / f"模型_{_safe_slug(m_name)}.md").write_text("\n".join(m_lines), encoding="utf-8")

    # Write master MOC
    moc_lines = ["# X 書籤知識庫總覽", "", f"共收錄 **{note_count}** 篇書籤筆記。", "", "## 分類統計", ""]
    for cat, refs in sorted(cat_groups.items(), key=lambda kv: -len(kv[1])):
        moc_lines.append(f"- **{cat}**：{len(refs)} 篇")
    if model_groups:
        moc_lines.extend(["", "## 模型索引（MOC）", ""])
        for m_name, refs in sorted(model_groups.items(), key=lambda kv: -len(kv[1]))[:30]:
            moc_lines.append(f"- [[00-索引/模型_{_safe_slug(m_name)}|{m_name}]]（{len(refs)} 篇）")
    moc_lines.extend(["", "## 依分類瀏覽", ""])
    for cat, refs in sorted(cat_groups.items(), key=lambda kv: -len(kv[1])):
        moc_lines.extend([f"### {cat}（{len(refs)} 篇）", ""])
        for ref in refs:
            moc_lines.append(f"- `{ref['date']}` [[{ref['stem']}|{ref['title']}]] ({ref['handle']})")
        moc_lines.append("")
    (idx_dir / "總覽.md").write_text("\n".join(moc_lines), encoding="utf-8")

    return {"path": str(vault), "notes": note_count, "models": len(model_groups)}
