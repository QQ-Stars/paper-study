"""采集流水线：发现 → 去重 → LLM分类 → (下载开放PDF) → 入库 → (可选讲解)。"""
import json
from . import db, llm, util, config, extract
from .sources.semanticscholar import SemanticScholar
from .sources.arxiv import Arxiv

SOURCES = {"semanticscholar": SemanticScholar, "arxiv": Arxiv}


def ingest(query, sources, years, limit, min_rel=0.0, explain=False):
    con = db.connect()
    found = added = skipped = 0
    for sname in sources:
        if sname not in SOURCES:
            print(f"  ! 未知数据源: {sname}"); continue
        print(f"\n== 数据源: {sname} ==")
        try:
            stubs = list(SOURCES[sname]().search(query, years, limit))
        except Exception as e:
            print(f"  ! 数据源 {sname} 检索失败（跳过该源）: {e}")
            continue
        for stub in stubs:
            found += 1
            tn = db.title_norm(stub.title)
            if db.exists(con, arxiv_id=stub.arxiv_id, title_norm_v=tn):
                skipped += 1
                continue
            try:
                attrs = llm.classify(stub, query)
                if query and attrs.relevance is not None and attrs.relevance < min_rel:
                    skipped += 1
                    print(f"  - 相关度低({attrs.relevance:.2f})跳过: {stub.title[:50]}")
                    continue
                slug = util.make_slug(stub)
                pdf_path = None
                if stub.pdf_url:
                    try:
                        dest = config.PDF_DIR / f"{slug}.pdf"
                        util.download_pdf(stub.pdf_url, dest)
                        pdf_path = f"data/pdfs/{slug}.pdf"
                    except Exception:
                        pdf_path = None
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
                print(f"  + {pdf_flag} [{stub.venue} {stub.year}] {stub.title[:58]}  ({attrs.type}/{attrs.topic})")
                if explain and pdf_path:
                    try:
                        text = extract.first_pages(config.ROOT / pdf_path, 8, stub.abstract)
                        # 讲解生成留待 explainer 模块(P3)；此处先占位
                    except Exception:
                        pass
            except Exception as ex:
                skipped += 1
                print(f"  ! 跳过: {stub.title[:46]} -> {ex}")
    con.close()
    print(f"\n========== 完成：发现 {found} · 新增 {added} · 跳过 {skipped} ==========")
    return added
