"""中文题名批量生成（LLM）。

  python -m agent title-translations [--limit N]

为 title_zh 缺失的论文逐条调用大模型翻译英文题名，写回 papers.title_zh。
与旧 Node 版 lib/title-translations.js 行为对齐：同样的系统提示、同样的
结果清洗规则、同样的「仅在仍缺失时写入」幂等落库。

事件协议（每行一条 JSON → stdout，与前端 titleTranslationsContract 一致）：
  {"type":"progress","stage":"batch","total":N}
  {"type":"progress","stage":"item","state":"start|done|skipped|failed",
   "index":i,"total":N,"id":..[, "title":..][, "title_zh":..][, "error":..]}
  {"type":"result","ok":true,"summary":{"total":N,"done":M,"failed":[...],"cancelled":false}}
"""
import json
import sys

from . import config, db, llm

TITLE_TRANSLATION_SYSTEM = "\n".join([
    "你是专业的学术论文题名翻译助手。",
    "把英文论文题名翻译为忠实、精炼、自然的简体中文学术题名。",
    "保留必要的模型名、数据集名、缩写与专有名词。",
    "只返回一行中文题名，不要引号、Markdown、标签或解释。",
])


def _emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def clean_title_translation(value):
    """清洗模型输出：只接受单行、含汉字、不超长的纯文本题名（对齐旧 Node 实现）。"""
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:]).replace("```", "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return ""
    text = lines[0]
    for prefix in ("中文标题：", "中文题名：", "中文翻译：", "译名：",
                   "中文标题:", "中文题名:", "中文翻译:", "译名:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.strip().strip("“”\"'‘’").strip()
    if not any("\u3400" <= ch <= "\u9fff" for ch in text) or len(text) > 300:
        return ""
    return text


def _translate_title(title):
    resp = llm.client().chat.completions.create(
        model=config.MODEL,
        messages=[
            {"role": "system", "content": TITLE_TRANSLATION_SYSTEM},
            {"role": "user", "content": title},
        ],
        temperature=0.1,
    )
    return (resp.choices[0].message.content or "").strip()


def run_batch(limit=0):
    """为缺 title_zh 的论文批量生成中文题名。limit=0 表示全部。"""
    con = db.connect()
    rows = con.execute(
        "SELECT id,title FROM papers "
        "WHERE title_zh IS NULL OR TRIM(title_zh)='' ORDER BY id"
    ).fetchall()
    if limit and limit > 0:
        rows = rows[:limit]
    summary = {"total": len(rows), "done": 0, "failed": [], "cancelled": False}
    _emit({"type": "progress", "stage": "batch", "total": len(rows)})
    for index, row in enumerate(rows, start=1):
        paper_id, title = row["id"], row["title"] or ""
        _emit({"type": "progress", "stage": "item", "state": "start",
               "index": index, "total": len(rows), "id": paper_id, "title": title})
        try:
            title_zh = clean_title_translation(_translate_title(title))
            if not title_zh:
                raise ValueError("大模型未返回有效中文题名")
            changed = con.execute(
                "UPDATE papers SET title_zh=? WHERE id=? "
                "AND (title_zh IS NULL OR TRIM(title_zh)='')",
                (title_zh, paper_id),
            ).rowcount
            con.commit()
            if changed:
                summary["done"] += 1
            _emit({"type": "progress", "stage": "item",
                   "state": "done" if changed else "skipped",
                   "index": index, "total": len(rows), "id": paper_id,
                   "title_zh": title_zh})
        except Exception as e:
            failure = {"id": paper_id, "title": title, "error": str(e)}
            summary["failed"].append(failure)
            _emit({"type": "progress", "stage": "item", "state": "failed",
                   "index": index, "total": len(rows), **failure})
    con.close()
    result = {"type": "result", "ok": not bool(summary["failed"]), "summary": summary}
    if summary["failed"]:
        result["error"] = "部分标题翻译失败，请检查模型连接"
    _emit(result)
