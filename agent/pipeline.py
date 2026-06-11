"""采集流水线：发现 → 去重 → LLM分类 → (下载开放PDF) → 入库 → (可选讲解)。"""
import json
import math
import sys
from . import db, llm, util, config, extract
from .models import PaperStub
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
    kt, kp = db.known_categories(con)           # 已有研究方向/主题，供大模型复用
    kt, kp = list(kt), list(kp)
    theme = config.RESEARCH_THEME or direction
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
            attrs = llm.classify(stub, direction, body, known_types=kt, known_topics=kp, theme=theme)
            n_cls += 1
            if attrs.type and attrs.type not in kt:
                kt.append(attrs.type)
            if attrs.topic and attrs.topic not in kp:
                kp.append(attrs.topic)
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


# ============ 两阶段流程（R3）：进度走 stderr，结果走 stdout ============
def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def search(direction, sources, years, limit, min_rel=0.0, expand=False, expand_n=6, queries=None):
    """第一阶段：扩展→多源收集→去重→LLM分类打分。返回候选(不下载PDF)。"""
    queries = queries if queries else (llm.expand_queries(direction, expand_n) if expand else [direction])
    _p("STAGE::expand")
    _p("QUERIES::" + json.dumps(queries, ensure_ascii=False))
    seen = {}
    per = max(3, math.ceil(limit * 1.5 / max(1, len(queries))))
    _p("STAGE::search")
    for sname in sources:
        if sname not in SOURCES:
            continue
        src = SOURCES[sname]()
        for q in queries:
            try:
                for stub in src.search(q, years, per):
                    key = stub.arxiv_id or db.title_norm(stub.title)
                    if key and key not in seen:
                        seen[key] = stub
            except Exception as e:
                _p(f"SRCERR::{sname}::{e}")
    _p(f"FOUND::{len(seen)}")
    _p("STAGE::classify")
    con = db.connect()
    kt, kp = db.known_categories(con)           # 已有研究方向/主题，供大模型复用；本批新建的也即时并入
    kt, kp = list(kt), list(kp)
    theme = config.RESEARCH_THEME or direction
    cands, i, cap = [], 0, limit * 3
    for stub in seen.values():
        if len(cands) >= limit or i >= cap:
            break
        i += 1
        tn = db.title_norm(stub.title)
        in_lib = db.exists(con, arxiv_id=stub.arxiv_id, title_norm_v=tn)
        try:
            attrs = llm.classify(stub, direction, known_types=kt, known_topics=kp, theme=theme)
        except Exception as e:
            _p(f"CLSERR::{e}")
            continue
        if attrs.type and attrs.type not in kt:
            kt.append(attrs.type)
        if attrs.topic and attrs.topic not in kp:
            kp.append(attrs.topic)
        _p(f"CLASSIFIED::{i}::{stub.title[:48]}")
        if min_rel and attrs.relevance is not None and attrs.relevance < min_rel and not in_lib:
            continue
        cands.append({
            **stub.model_dump(),
            "type": attrs.type, "topic": attrs.topic, "task": attrs.task,
            "models": attrs.models, "datasets": attrs.datasets,
            "contribution": attrs.contribution, "llm_tldr": attrs.tldr, "tags": attrs.tags,
            "relevance": attrs.relevance, "in_library": in_lib,
        })
    con.close()
    _p(f"DONE::{len(cands)}")
    return cands


def ingest_candidates(cands, deep=False):
    """第二阶段：对用户勾选的候选下载PDF+入库（属性多在第一阶段算好）。
    若候选未带分类（如「相似论文」推荐来的），入库时现场补一次 LLM 分类，保持库内类别一致。"""
    con = db.connect()
    kt, kp = db.known_categories(con)           # 已有研究方向/主题，供大模型复用
    kt, kp = list(kt), list(kp)
    theme = config.RESEARCH_THEME or ""
    added = 0
    for c in cands:
        tn = db.title_norm(c.get("title", ""))
        if db.exists(con, arxiv_id=c.get("arxiv_id"), title_norm_v=tn):
            _p(f"DUP::{c.get('title','')[:46]}")
            continue
        try:
            stub = PaperStub(**{k: c.get(k) for k in PaperStub.model_fields if k in c})
            ctype, ctopic, ctask = c.get("type"), c.get("topic"), c.get("task")
            cmodels, cdatasets = c.get("models") or [], c.get("datasets") or []
            ccontrib, ctldr, ctags = c.get("contribution"), c.get("llm_tldr"), c.get("tags") or []
            crel = c.get("relevance")
            if not (ctype and str(ctype).strip()):       # 推荐/外部候选缺分类 → 现做
                try:
                    a = llm.classify(stub, theme, known_types=kt, known_topics=kp, theme=theme)
                    ctype, ctopic, ctask = a.type, a.topic, a.task
                    cmodels, cdatasets = a.models, a.datasets
                    ccontrib, ctldr, ctags = a.contribution, a.tldr, a.tags
                    crel = a.relevance
                    if a.type and a.type not in kt:
                        kt.append(a.type)
                    if a.topic and a.topic not in kp:
                        kp.append(a.topic)
                    _p(f"CLASSIFIED::{stub.title[:46]}")
                except Exception as e:
                    _p(f"CLSERR::{e}")
            slug = util.make_slug(stub)
            pdf_path = None
            if stub.pdf_url:
                try:
                    dest = config.PDF_DIR / f"{slug}.pdf"
                    util.download_pdf(stub.pdf_url, dest)
                    pdf_path = str(dest)
                except Exception:
                    pdf_path = None
            row = {
                "id": slug, "source": stub.source, "source_id": stub.source_id,
                "arxiv_id": stub.arxiv_id, "doi": stub.doi, "s2_id": stub.s2_id,
                "title": stub.title, "title_norm": tn,
                "authors": json.dumps(stub.authors, ensure_ascii=False),
                "venue": stub.venue, "year": stub.year, "abstract": stub.abstract,
                "tldr": stub.tldr or ctldr, "citations": stub.citations,
                "s2_fields": json.dumps(stub.fields, ensure_ascii=False),
                "url": stub.url, "pdf_url": stub.pdf_url, "pdf_path": pdf_path,
                "type": ctype, "topic": ctopic, "task": ctask,
                "models": json.dumps(cmodels, ensure_ascii=False),
                "datasets": json.dumps(cdatasets, ensure_ascii=False),
                "contribution": ccontrib,
                "tags": json.dumps(ctags, ensure_ascii=False),
                "relevance": crel, "extracted_by": config.MODEL,
            }
            db.insert_paper(con, row)
            added += 1
            _p(f"ADDED::{stub.title[:48]}")
        except Exception as e:
            _p(f"SKIP::{e}")
    con.close()
    _p(f"INGESTED::{added}")
    return added
