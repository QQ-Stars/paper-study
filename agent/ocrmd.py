# -*- coding: utf-8 -*-
"""PDF → Markdown（OCR）：调用 extract._ocr_full_text（方案 A：DeepSeek-OCR 官方
Markdown 提示词 + grounding 标记清理）。进度 → stderr（OCRPG::i/n），Markdown → stdout。
不落库（纯转换，供阅读页即时查看）；讲解/翻译链路若启用 pdfTextProvider=ocr 也走同一函数。"""
import json
import sys

from . import db, extract
from .explain import _find_pdf


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def ocr_to_markdown(pid: str) -> str:
    con = db.connect()
    row = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    con.close()
    if not row:
        _p(f"ERR::论文不存在: {pid}")
        raise SystemExit(2)
    r = dict(row)
    _p(f"STAGE::load::{(r.get('title') or '')[:48]}")

    pdf = _find_pdf(r)
    if not pdf:
        _p("PDFMISS::未找到本地PDF，无法执行 OCR")
        raise SystemExit(4)

    try:
        pages = extract.page_count(pdf)
    except Exception:
        pages = "?"
    _p(f"STAGE::ocr::OCR 转 Markdown（共 {pages} 页，逐页调用 OCR 模型）…")

    try:
        md = extract._ocr_full_text(str(pdf), paper_id=pid)
    except Exception as e:
        _p(f"ERR::OCR 失败: {e}")
        raise SystemExit(3)

    if not (md or "").strip():
        _p("ERR::OCR 结果为空（请检查 OCR API 配置）")
        raise SystemExit(3)

    _p(f"DONE::{len(md)}")
    sys.stdout.write(md)
    sys.stdout.flush()
    return md


def ocr_batch(limit: int = 0) -> dict:
    """批量 PDF→Markdown(OCR)：只处理「有本地 PDF 且尚无 OCR 落库记录」的论文；无 PDF 跳过计数。
    每篇成功即落库（ocr_markdown 表 + ocrMarkdownDir 目录 .md，随 set_ocr_markdown 自动完成），
    可随时中断/重复执行（只补仍缺的）。进度 → stderr BATCH::/ITEM::；汇总 JSON → stdout
    （与 explain_batch 同契约：ok/total/done/failed/skipped_no_pdf）。"""
    con = db.connect()
    rows = con.execute("SELECT * FROM papers ORDER BY datetime(created_at) DESC").fetchall()
    targets, skipped = [], []
    for row in rows:
        r = dict(row)
        if not _find_pdf(r):
            skipped.append(r["id"])              # 无本地 PDF → 无法 OCR → 跳过
            continue
        try:
            cached = db.get_ocr_markdown(con, r["id"])
        except Exception:
            cached = None
        if cached and cached.strip():
            continue                              # 已有 OCR 结果 → 不重复转换
        targets.append(r)
    if limit and limit > 0:
        targets = targets[:limit]
    total = len(targets)
    _p(f"BATCH::total::{total}::skip::{len(skipped)}")
    done, failed = 0, []
    for i, r in enumerate(targets, 1):
        title = (r.get("title") or "")[:60]
        _p(f"ITEM::{i}::{total}::start::{r['id']}::{title}")
        try:
            pdf = _find_pdf(r)
            md = extract._ocr_full_text(str(pdf), paper_id=r["id"])
            if md and md.strip():
                done += 1
                _p(f"ITEM::{i}::{total}::done::{r['id']}::{len(md)}")
            else:
                failed.append(r["id"])
                _p(f"ITEM::{i}::{total}::fail::{r['id']}::OCR 结果为空")
        except Exception as e:
            failed.append(r["id"])
            _p(f"ITEM::{i}::{total}::fail::{r['id']}::{str(e)[:120]}")
    con.close()
    _p(f"BATCH::finish::done={done}::fail={len(failed)}::skip={len(skipped)}")
    out = {"ok": True, "total": total, "done": done, "failed": failed, "skipped_no_pdf": skipped}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return out
