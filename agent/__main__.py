"""CLI 入口。
  python -m agent ingest --query "..." --sources arxiv --max 30 [--expand --deep]
  python -m agent search --query "..." --sources arxiv,openalex --max 20 [--expand]   # 仅返回候选(JSON→stdout, 进度→stderr)
  python -m agent ingest-selected [--deep]   # 从 stdin 读勾选候选JSON，下载+入库
  python -m agent ping | purge
"""
import argparse
import sys
import json
from . import pipeline, llm, config


def _add_search_args(p):
    p.add_argument("--query", required=True, help="检索方向（中文/英文皆可）")
    p.add_argument("--sources", default="arxiv", help="逗号分隔: arxiv,semanticscholar,openalex,dblp")
    p.add_argument("--years", default="2024-2026")
    p.add_argument("--max", type=int, default=20)
    p.add_argument("--min-relevance", type=float, default=0.0)
    p.add_argument("--expand", action="store_true")
    p.add_argument("--expand-n", type=int, default=6)


def _years(s):
    parts = s.split("-")
    return (int(parts[0]), int(parts[1])) if len(parts) == 2 else None


def main():
    ap = argparse.ArgumentParser(prog="agent", description="论文采集 Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="一步式：抓取+分类+下载+入库")
    _add_search_args(ing)
    ing.add_argument("--explain", action="store_true")
    ing.add_argument("--deep", action="store_true", help="深度分类: 读取PDF正文")

    se = sub.add_parser("search", help="第一阶段：仅返回候选(不下载)")
    _add_search_args(se)
    se.add_argument("--queries", default="", help="JSON 数组，直接指定检索词(跳过扩展)")

    ex = sub.add_parser("expand", help="仅生成扩展检索词(JSON→stdout)")
    ex.add_argument("--query", required=True)
    ex.add_argument("--expand-n", type=int, default=6)

    isel = sub.add_parser("ingest-selected", help="第二阶段：从 stdin 读勾选候选并入库")
    isel.add_argument("--deep", action="store_true")

    vv = sub.add_parser("verify-venue", help="会议核实：查权威库（stdin 读候选JSON，结果→stdout）")
    vv.add_argument("--sources", default="dblp,semanticscholar", help="核实源(优先级顺序): dblp,semanticscholar,openalex")

    exp = sub.add_parser("explain", help="为已入库论文生成讲解(LLM)，写入 papers.explainer，md→stdout")
    exp.add_argument("--id", required=True, help="论文 id (slug)")
    exp.add_argument("--deep", action="store_true", help="读取本地PDF正文(更准，更慢)")

    tr = sub.add_parser("translate", help="全文翻译(LLM)：PDF→去参考文献→分块译中文，写入 translations，md→stdout")
    tr.add_argument("--id", required=True, help="论文 id (slug)")

    rc = sub.add_parser("recommend", help="相似论文推荐(S2 Recommendations)：据库内一篇 → 候选JSON→stdout")
    rc.add_argument("--id", required=True, help="作为种子的库内论文 id")
    rc.add_argument("--limit", type=int, default=14)

    em = sub.add_parser("embed", help="建立/更新论文向量索引(本地嵌入)")
    em.add_argument("--scope", choices=["all", "missing"], default="missing")

    ss = sub.add_parser("semsearch", help="语义检索：--query 任意中/英自然语言 → 排序结果JSON→stdout")
    ss.add_argument("--query", required=True)
    ss.add_argument("--k", type=int, default=30)

    sub.add_parser("ping", help="测试大模型连通性")
    sub.add_parser("purge", help="删除采集来的论文（保留 seed 种子 38 篇）")

    args = ap.parse_args()

    if args.cmd == "ping":
        print(f"provider={config.PROVIDER} model={config.MODEL}")
        print("回复:", llm.ping())
    elif args.cmd == "purge":
        from . import db
        con = db.connect()
        n = con.execute("DELETE FROM papers WHERE source != 'seed'").rowcount
        con.commit(); con.close()
        print("已删除采集论文:", n, "（seed 种子保留）")
    elif args.cmd == "ingest":
        srcs = [s.strip() for s in args.sources.split(",") if s.strip()]
        pipeline.ingest(args.query, srcs, _years(args.years), args.max, args.min_relevance,
                        args.explain, args.deep, args.expand, args.expand_n)
    elif args.cmd == "expand":
        sys.stdout.write(json.dumps(llm.expand_queries(args.query, args.expand_n), ensure_ascii=False))
        sys.stdout.flush()
    elif args.cmd == "search":
        srcs = [s.strip() for s in args.sources.split(",") if s.strip()]
        qs = json.loads(args.queries) if args.queries.strip() else None
        cands = pipeline.search(args.query, srcs, _years(args.years), args.max,
                                args.min_relevance, args.expand, args.expand_n, qs)
        sys.stdout.write(json.dumps(cands, ensure_ascii=False))
        sys.stdout.flush()
    elif args.cmd == "ingest-selected":
        raw = sys.stdin.read()
        if raw[:1] == "﻿":          # 剥离可能的 UTF-8 BOM
            raw = raw[1:]
        cands = json.loads(raw) if raw.strip() else []
        pipeline.ingest_candidates(cands, args.deep)
    elif args.cmd == "verify-venue":
        from . import verify
        raw = sys.stdin.read()
        if raw[:1] == "﻿":          # 剥离可能的 UTF-8 BOM
            raw = raw[1:]
        cands = json.loads(raw) if raw.strip() else []
        srcs = [s.strip() for s in args.sources.split(",") if s.strip()]
        res = verify.verify_venues(cands, srcs)
        sys.stdout.write(json.dumps(res, ensure_ascii=False))
        sys.stdout.flush()
    elif args.cmd == "explain":
        from . import explain
        explain.explain_paper(args.id, args.deep)
    elif args.cmd == "translate":
        from . import translate
        translate.translate_paper(args.id)
    elif args.cmd == "recommend":
        from . import recommend
        recommend.recommend_paper(args.id, args.limit)
    elif args.cmd == "embed":
        from . import embed
        embed.reindex(args.scope)
    elif args.cmd == "semsearch":
        from . import embed
        embed.semsearch(args.query, args.k)


if __name__ == "__main__":
    main()
