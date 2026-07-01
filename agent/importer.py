"""本地 PDF 批量导入：扫描得到的 PDF → 抽首页 → LLM 解析书目 →（可选 S2 补全）→ 分类 → 入库。

文件路径从 stdin 读（JSON 数组）；进度 → stderr，统计结果 → stdout。
PDF 会复制/移动进项目 PDF 目录，并按论文标题命名。"""
import json
import os
import sys
import difflib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import db, llm, util, config, extract, pdf_files
from .models import PaperStub


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _close(a, b):
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.88


def _enrich(stub):
    """用标题在 Semantic Scholar 找最匹配的一条，命中则返回其权威元数据 stub。"""
    from .sources.semanticscholar import SemanticScholar
    tn = db.title_norm(stub.title)
    try:
        for s in SemanticScholar().search(stub.title, None, 3):
            if _close(tn, db.title_norm(s.title)):
                return s
    except Exception as e:
        _p(f"ENRICHERR::{e}")
    return None


def _prep_one(path, enrich):
    """线程内：抽取首页 + LLM 解析书目（+ 可选 S2 补全）。不碰 DB/共享态。返回 (path, stub)。"""
    try:
        text = extract.first_pages(path, n=2)
    except Exception as e:
        _p(f"PDFERR::{os.path.basename(path)}::{e}")
        return path, None
    meta = llm.parse_pdf_meta(text)
    title = meta.get("title") or Path(path).stem.replace("_", " ").replace("-", " ")
    stub = PaperStub(source="localpdf", source_id=Path(path).stem[:80],
                     title=title, authors=meta.get("authors") or [],
                     year=meta.get("year"), abstract=meta.get("abstract"))
    if enrich and title:
        s = _enrich(stub)
        if s:                                   # 用 S2 权威元数据，来源仍标 localpdf
            if s.title:
                stub.title = s.title
            stub.venue = s.venue
            stub.year = s.year or stub.year
            stub.abstract = s.abstract or stub.abstract
            stub.tldr = s.tldr
            stub.citations = s.citations
            stub.arxiv_id = s.arxiv_id
            stub.doi = s.doi
            stub.s2_id = s.s2_id
            stub.url = s.url
            stub.pdf_url = s.pdf_url
            stub.fields = s.fields or []
    return path, stub


def import_pdfs(paths, enrich=True, workers=4):
    paths = [p for p in (paths or []) if p and os.path.isfile(p)]
    con = db.connect()
    kt, kp = db.known_categories(con)
    kt, kp = list(kt), list(kp)
    theme = config.RESEARCH_THEME or ""
    _p(f"TOTAL::{len(paths)}")

    # 1) 并行 抽取+解析+补全（IO/LLM 密集，并发提速）
    prepped, done = [], 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_prep_one, pth, enrich) for pth in paths]
        for fut in as_completed(futs):
            done += 1
            try:
                pth, stub = fut.result()
            except Exception as e:
                _p(f"PREPERR::{e}"); continue
            if stub and stub.title.strip():
                prepped.append((pth, stub))
                _p(f"PARSED::{done}::{len(paths)}::{stub.title[:46]}")
            else:
                _p(f"SKIP::{done}::{len(paths)}::{os.path.basename(pth)}")

    # 2) 串行 分类 + 入库（分类沿用库中已有类别，本批新建即时并入 → 自我收敛）
    added, dup, failed = 0, 0, 0
    for pth, stub in prepped:
        source_pdf = Path(pth).resolve()
        abspath = str(source_pdf)
        tn = db.title_norm(stub.title)
        slug = util.make_slug(stub)
        dest = pdf_files.unique_pdf_path(config.PDF_DIR, stub.title, paper_id=slug, source_path=source_pdf)
        # 已导入过同一个文件（pdf_path 命中）也算重复 → 重复扫描同一文件夹时幂等
        if con.execute("SELECT 1 FROM papers WHERE pdf_path IN (?,?)", (abspath, str(dest))).fetchone() \
                or db.exists(con, arxiv_id=stub.arxiv_id, title_norm_v=tn):
            dup += 1
            _p(f"DUP::{stub.title[:46]}")
            continue
        try:
            attrs = llm.classify(stub, theme, known_types=kt, known_topics=kp, theme=theme)
            if attrs.type and attrs.type not in kt:
                kt.append(attrs.type)
            if attrs.topic and attrs.topic not in kp:
                kp.append(attrs.topic)
            archived_pdf = pdf_files.archive_pdf(source_pdf, stub.title, slug, config.PDF_DIR)
            row = {
                "id": slug, "source": "localpdf", "source_id": stub.source_id,
                "arxiv_id": stub.arxiv_id, "doi": stub.doi, "s2_id": stub.s2_id,
                "title": stub.title, "title_norm": tn,
                "authors": json.dumps(stub.authors, ensure_ascii=False),
                "venue": stub.venue, "year": stub.year, "abstract": stub.abstract,
                "tldr": stub.tldr or attrs.tldr, "citations": stub.citations,
                "s2_fields": json.dumps(stub.fields, ensure_ascii=False),
                "url": stub.url, "pdf_url": stub.pdf_url,
                "pdf_path": str(archived_pdf),
                "type": attrs.type, "topic": attrs.topic, "task": attrs.task,
                "models": json.dumps(attrs.models, ensure_ascii=False),
                "datasets": json.dumps(attrs.datasets, ensure_ascii=False),
                "contribution": attrs.contribution,
                "tags": json.dumps(attrs.tags, ensure_ascii=False),
                "relevance": attrs.relevance, "extracted_by": config.MODEL,
            }
            db.insert_paper(con, row)
            added += 1
            _p(f"ADDED::{stub.title[:46]}")
        except Exception as e:
            failed += 1
            _p(f"CLSERR::{e}")
    con.close()
    _p(f"DONE::{added}")
    sys.stdout.write(json.dumps(
        {"ok": True, "added": added, "dup": dup, "failed": failed, "total": len(paths)},
        ensure_ascii=False))
    sys.stdout.flush()
