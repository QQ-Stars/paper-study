# -*- coding: utf-8 -*-
"""PDF → Markdown（OCR）：调用 extract._ocr_full_text（方案 A：DeepSeek-OCR 官方
Markdown 提示词 + grounding 标记清理）。进度 → stderr（OCRPG::i/n），Markdown → stdout。
成功结果会写入 ocr_markdown，供阅读页与讲解/翻译复用；失败不会写入或覆盖本地结果。"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, db, extract
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

    if not config.OCR_ENABLED:
        _p("OCRERR::OCR 已禁用，请在设置中启用 OCR")
        raise SystemExit(3)

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


def _ocr_one(r: dict):
    """单篇 OCR（供线程池调用）：quiet 模式下无逐页进度，落库在 _ocr_full_text 内部完成。
    每个线程自建 SQLite 连接（WAL + busy_timeout 兼容并发写）。"""
    pdf = _find_pdf(r)
    if not pdf:
        raise RuntimeError("未找到本地 PDF")
    md = extract._ocr_full_text(str(pdf), paper_id=r["id"], quiet=True)
    if not (md or "").strip():
        raise RuntimeError("OCR 结果为空")
    return len(md)


def ocr_batch(limit: int = 0) -> dict:
    """批量 PDF→Markdown(OCR)：只处理「有本地 PDF 且尚无 OCR 落库记录」的论文；无 PDF 跳过计数。
    篇级并发（config.OCR_BATCH_WORKERS，默认 2，设置页 ocrMaxConcurrency 可调），
    每篇成功即落库（ocr_markdown 表 + ocrMarkdownDir 目录 .md，随 set_ocr_markdown 自动完成），
    可随时中断/重复执行（只补仍缺的）。进度 → stderr BATCH::/ITEM::；汇总 JSON → stdout
    （与 explain_batch 同契约：ok/total/done/failed/skipped_no_pdf）。"""
    if not config.OCR_ENABLED:
        _p("OCRERR::OCR 已禁用，请在设置中启用 OCR")
        out = {
            "ok": False,
            "total": 0,
            "done": 0,
            "failed": [],
            "skipped_no_pdf": [],
            "error": "OCR 已禁用，请在设置中启用 OCR",
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
        return out
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
    con.close()
    if limit and limit > 0:
        targets = targets[:limit]
    total = len(targets)
    workers = min(config.OCR_BATCH_WORKERS, max(1, total))
    _p(f"BATCH::total::{total}::skip::{len(skipped)}::workers::{workers}")
    done, failed = 0, []
    if total == 0:
        _p("BATCH::finish::done=0::fail=0::skip=%d" % len(skipped))
    else:
        finished = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for i, r in enumerate(targets, 1):
                title = (r.get("title") or "")[:60]
                _p(f"ITEM::{i}::{total}::start::{r['id']}::{title}")
                futures[pool.submit(_ocr_one, r)] = (i, r)
            for future in as_completed(futures):
                i, r = futures[future]
                finished += 1
                try:
                    size = future.result()
                    done += 1
                    _p(f"ITEM::{i}::{total}::done::{r['id']}::{size}（已完成 {finished}/{total}）")
                except Exception as e:
                    failed.append(r["id"])
                    _p(f"ITEM::{i}::{total}::fail::{r['id']}::{str(e)[:120]}")
        _p(f"BATCH::finish::done={done}::fail={len(failed)}::skip={len(skipped)}")
    out = {"ok": True, "total": total, "done": done, "failed": failed, "skipped_no_pdf": skipped}
    run_con = db.connect()
    try:
        db.record_batch_run(
            run_con,
            "ocr",
            total=total,
            done=done,
            failed=len(failed),
            skipped=len(skipped),
            detail=out,
        )
    finally:
        run_con.close()
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return out
