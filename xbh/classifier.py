"""Two-layer classifier: zero-cost deterministic rules + optional OpenAI-compatible LLM summarizer."""

import json
import re
import sqlite3
from typing import Any
import urllib.request

from xbh.db import _json_list

MODEL_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("MiniMax H3", "h3", re.compile(r"(minimax[\s_-]*h3|pi[\s_-]*h3|open[\s_-]*h3|hyperflow\s*h3|pika[\s_-]*h3|fasth3|mtvedit[\s_-]*h3|sulphur[\s_-]*3|10eros[\s_-]*max|ref2va|h3-character-swap|\bh3\b)", re.I)),
    ("MiniMax H3-Image", "h3_img", re.compile(r"(minimax[\s_-]*h3[\s_-]*image|p[\s_-]*h3[\s_-]*image|h3image)", re.I)),
    ("Hailuo 海螺", "h3", re.compile(r"(hailuo|海螺)", re.I)),
    ("MAGI-2", "video", re.compile(r"(magi[\s_-]*2|sand\.ai|magi2)", re.I)),
    ("Seedance", "video", re.compile(r"(seedance|即夢|即梦|jimeng)", re.I)),
    ("Kling 可靈", "video", re.compile(r"(kling|可靈|可灵)", re.I)),
    ("Veo", "video", re.compile(r"\bveo[\s_-]*[234]?\b", re.I)),
    ("Sora", "video", re.compile(r"\bsora[\s_-]*[12]?\b", re.I)),
    ("Runway", "video", re.compile(r"(runway|gen-?[34])", re.I)),
    ("Pika", "video", re.compile(r"\bpika\b", re.I)),
    ("Wan 萬相", "video", re.compile(r"(wan[\s_-]*2\.[12]|萬相|万相)", re.I)),
    ("LTX", "video", re.compile(r"\bltx\b", re.I)),
    ("Vidu", "video", re.compile(r"\bvidu\b", re.I)),
    ("Hunyuan 混元", "video", re.compile(r"(hunyuan|混元)", re.I)),
    ("Muse", "video", re.compile(r"\bmuse\b", re.I)),
    ("Gemini Omni / 4", "video", re.compile(r"(gemini\s*omni|gemini\s*4)", re.I)),
    ("ReCaVSR / RelayVSR", "video", re.compile(r"(recavsr|relayvsr)", re.I)),
    ("LongCat Avatar", "video", re.compile(r"longcat-video", re.I)),
    ("Midjourney", "image", re.compile(r"(midjourney|\bmj\s*v?\d)", re.I)),
    ("Flux", "image", re.compile(r"\bflux\b", re.I)),
    ("GPT Image", "image", re.compile(r"(gpt[\s_-]*image)", re.I)),
    ("Seedream", "image", re.compile(r"seedream", re.I)),
    ("Qwen-Image", "image", re.compile(r"qwen[\s_-]*image", re.I)),
    ("Fibo / RMBG", "image", re.compile(r"(fibo-2\.0|rmbg[\s_-]*3)", re.I)),
    ("Nano Banana / Imagen", "image", re.compile(r"(nano\s*banana|nanobanana|\bimagen\b)", re.I)),
    ("ComfyUI", "workflow", re.compile(r"comfyui", re.I)),
    ("SAM 3 / 3D Body", "workflow", re.compile(r"(sam\s*3\.?1|sam\s*3d)", re.I)),
    ("RGBMoCap / InstantHMR", "workflow", re.compile(r"(rgb\s*mocap|instanthmr|kimodo)", re.I)),
    ("Depth Pro 2", "workflow", re.compile(r"depth\s*pro\s*2", re.I)),
    ("3DGS / Blender", "workflow", re.compile(r"(3dgs|gaussian\s*splat|idol-turbo|blender)", re.I)),
]

KIND_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("tutorial", re.compile(r"(工作流|workflow|教學|教程|提示詞|prompt|怎麼|如何|comfyui|node|節點|节点|實戰|保姆|手把手|步驟|技巧|github\.com)", re.I)),
    ("review", re.compile(r"(對比|对比|橫評|横评|評測|测评|實測|实测|比較|比较|\bvs\b|優缺點|优缺点|基準|benchmark|盲測|盲测)", re.I)),
    ("news", re.compile(r"(開源|开源|發布|发布|上線|上线|推出|更新|開放|开放|announced|releasing|open-source|now available|introducing)", re.I)),
    ("question", re.compile(r"(\?|？|請問|请问|求助|有人知道|怎麼選|怎么选)")),
]


def classify_bookmark(item: dict[str, Any]) -> dict[str, Any]:
    """Classify a single bookmark using deterministic rules."""
    blob = " ".join(
        [
            item.get("title") or "",
            item.get("text") or "",
            item.get("quoted_text") or "",
            " ".join(_json_list(item.get("tags"))),
        ]
    ).strip()

    if not blob:
        return {"category": item.get("category") or "", "models": _json_list(item.get("models")), "content_kind": item.get("content_kind") or ""}

    matched_models: list[str] = []
    domains: set[str] = set()
    for label, domain, pat in MODEL_RULES:
        if pat.search(blob):
            if label not in matched_models:
                matched_models.append(label)
            domains.add(domain)

    # Generic image/video terms
    is_gen_video = bool(re.search(r"(影片生成|视频生成|video\s*gen|i2v|t2v|r2v|ai\s*视频|ai\s*影片)", blob, re.I))
    is_gen_image = bool(re.search(r"(生圖|生图|ai\s*繪圖|ai\s*绘画|圖片生成|图像生成|t2i|i2i|lora|人像特寫|寫實皮膚)", blob, re.I))

    category = ""
    if "h3" in domains or "h3_img" in domains:
        category = "MiniMax H3 / Hailuo"
    elif "video" in domains or is_gen_video:
        category = "影片生成模型"
    elif "image" in domains or is_gen_image:
        category = "圖片生成模型"
    elif "workflow" in domains:
        category = "工作流與技巧評測"

    content_kind = ""
    for kind, pat in KIND_RULES:
        if pat.search(blob):
            content_kind = kind
            break
    if not content_kind and category:
        content_kind = "showcase"

    return {
        "category": category,
        "models": matched_models,
        "content_kind": content_kind,
    }


def classify_all(conn: sqlite3.Connection, overwrite: bool = False) -> dict[str, int]:
    """Run rule classification across bookmarks in DB."""
    rows = conn.execute(
        "SELECT id, tweet_id, title, text, quoted_text, category, models, content_kind, tags FROM bookmarks"
    ).fetchall()

    updated = 0
    categorized = 0
    for r in rows:
        item = dict(r)
        res = classify_bookmark(item)
        existing_cat = (item.get("category") or "").strip()
        existing_models = _json_list(item.get("models"))
        existing_kind = (item.get("content_kind") or "").strip()

        new_cat = res["category"] if (overwrite or not existing_cat) else existing_cat
        merged_models = list(dict.fromkeys([*existing_models, *res["models"]]))
        new_kind = res["content_kind"] if (overwrite or not existing_kind) else existing_kind

        if new_cat:
            categorized += 1

        if (
            new_cat != existing_cat
            or merged_models != existing_models
            or new_kind != existing_kind
        ):
            conn.execute(
                """UPDATE bookmarks SET
                    category = ?,
                    models = ?,
                    content_kind = ?,
                    updated_at = datetime('now')
                WHERE id = ?""",
                (new_cat, json.dumps(merged_models, ensure_ascii=False), new_kind, r["id"]),
            )
            updated += 1

    conn.commit()
    return {"scanned": len(rows), "updated": updated, "categorized": categorized}


def summarize_with_llm(
    conn: sqlite3.Connection,
    api_base: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    since: str = "",
    limit: int = 30,
) -> dict[str, int]:
    """Generate Traditional Chinese titles and concise summaries via an OpenAI-compatible API."""
    where = ["length(trim(text)) > 0", "title = ''"]
    params: list[Any] = []
    if since:
        where.append("created_at >= ?")
        params.append(since)

    rows = conn.execute(
        f"SELECT tweet_id, author_handle, text, quoted_text FROM bookmarks WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?",
        [*params, int(limit)],
    ).fetchall()

    if not rows:
        return {"targeted": 0, "summarized": 0}

    endpoint = api_base.rstrip("/") + "/chat/completions"
    summarized = 0
    batch_size = 10

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        prompt_items = []
        for r in batch:
            txt = (r["text"] or "")[:600]
            qt = (r["quoted_text"] or "")[:250]
            prompt_items.append(f'ID={r["tweet_id"]} ({r["author_handle"]}): {txt}' + (f" [Quote: {qt}]" if qt else ""))

        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一個技術書籤編輯。請為每則 X 貼文撰寫繁體中文標題（title，30字內）與重點摘要（summary，1-2句）。"
                        "只回傳 JSON 陣列：[{\"tweet_id\":\"...\",\"title\":\"...\",\"summary\":\"...\"}]"
                    ),
                },
                {"role": "user", "content": "\n\n".join(prompt_items)},
            ],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(content)
        for entry in parsed:
            tid = str(entry.get("tweet_id") or "")
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or "").strip()
            if tid and (title or summary):
                conn.execute(
                    "UPDATE bookmarks SET title = COALESCE(NULLIF(?, ''), title), summary = COALESCE(NULLIF(?, ''), summary), updated_at = datetime('now') WHERE tweet_id = ?",
                    (title, summary, tid),
                )
                summarized += 1
        conn.commit()

    return {"targeted": len(rows), "summarized": summarized}
