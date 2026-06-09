"""采集流水线：发现 → 去重 → LLM分类 → (下载开放PDF) → 入库 → (可选讲解)。"""
import json
import math
from . import db, llm, util, config, extract
from .sources.semanticscholar import SemanticScholar
from .sources.arxiv import Arxiv
from .sources.openalex import OpenAlex
from .sources.dblp import DBLP

SOURCES = {"semanticscholar": SemanticScholar, "arxiv": Arxiv, "openalex": OpenAlex, "dblp": DBLP}


def ingest(direction, sources, years, limit, min_rel=0.0, explain=False, deep=False, expand=False, expand_n=6):
    # 1) 智能扩展检索词（中文/模糊方向 → 多个精准英文检索词）
    queries = llm.expand_queries(direction, expand_n) if expand else [direction]
    print("🔎 检索词：")
    for q in queries:
        print("   ·", q)

    # 2) 多源 × 多词 收集候选，跨词去重
    seen = {}
    per = max(3, math.ceil(limit * 1.4 / max(1, len(queries))))
    for sname in sources:
        if sname not in SOURCES:
            print(f"  ! 未知数据源: {sname}"); continue
        src = SOURCES[sname]()
        for q in queries:
            try:
                for stub in src.search(q, years, per):
                    key = stub.arxiv_id or db.title_norm(stub.title)
                    if key and key not in seen:
                        seen[key] = stub
            except Exception as e:
                print(f"  ! {sname} / '{q}' 检索失败: {e}")
    print(f"\n候选去重后 {len(seen)} 篇，开始按方向「{direction}」分类入库…\n")

    # 3) 分类 + 相关性过滤 + 入库（用原始方向打分，最多采到 limit 篇）
    con = db.connect()
    found, added, skipped, n_cls = len(seen), 0, 0, 0
    cap = limit * 3
    for stub in seen.values():
        if added >= limit or n_cls >= cap:
            break
        tn = db.title_norm(stub.title)
        if db.exists(con, arxiv_id=stub.arxiv_id, title_norm_v=tn):
            skipped += 1
            continue
        try:
            slug = util.make_slug(stub)
            pdf_path = None
            if stub.pdf_url:
                try:
                    dest = config.PDF_DIR / f"{slug}.pdf"
                    util.download_pdf(stub.pdf_url, dest)
                    pdf_path = str(dest)
                except Exception:
                    pdf_path = None
            body = None
            if deep and pdf_path:
                try:
                    body = extract.first_pages(config.ROOT / pdf_path, 8, stub.abstract)
                except Exception:
                    body = None
            attrs = llm.classify(stub, direction, body)
            n_cls += 1
            if attrs.relevance is not None and attrs.relevance < min_rel:
                skipped += 1
                print(f"  - 相关度低({attrs.relevance:.2f}) 跳过: {stub.title[:48]}")
                continue
            row = {
                "id": slug, "source": stub.source, "source_id": stub.source_id,
                "arxiv_id": stub.arxiv_id, "doi": stub.doi, "s2_id": stub.s2_id,
                "title": stub.title, "title_norm": tn,
                "authors": json.dumps(stub.authors, ensure_ascii=False),
                "venue": stub.venue, "year": stub.year, "abstract": stub.abstract,
                "tldr": stub.tldr or attrs.tldr, "citations": stub.citations,
                "s2_fields": json.dumps(stub.fields, ensure_ascii=False),
                "url": stub.url, "pdf_url": stub.pdf_url, "pdf_path": pdf_path,
                "type": attrs.type, "topic": attrs.topic, "task": attrs.task,
                "models": json.dumps(attrs.models, ensure_ascii=False),
                "datasets": json.dumps(attrs.datasets, ensure_ascii=False),
                "contribution": attrs.contribution,
                "tags": json.dumps(attrs.tags, ensure_ascii=False),
                "relevance": attrs.relevance, "extracted_by": config.MODEL,
            }
            db.insert_paper(con, row)
            added += 1
            pdf_flag = "📄" if pdf_path else "  "
            print(f"  + {pdf_flag} [{stub.venue} {stub.year}] {stub.title[:56]}  ({attrs.type}/{attrs.topic}, rel={attrs.relevance})")
        except Exception as ex:
            skipped += 1
            print(f"  ! 跳过: {stub.title[:44]} -> {ex}")
    con.close()
    print(f"\n========== 完成：候选 {found} · 新增 {added} · 跳过 {skipped} ==========")
    return added
