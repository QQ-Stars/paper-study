"""CLI 入口： python -m agent ingest --query "..." --sources semanticscholar,arxiv --max 30"""
import argparse
from . import pipeline, llm, config


def main():
    ap = argparse.ArgumentParser(prog="agent", description="论文采集 Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="按方向抓取并入库")
    ing.add_argument("--query", required=True, help="检索方向，如 'multimodal hallucination'")
    ing.add_argument("--sources", default="semanticscholar", help="逗号分隔: semanticscholar,arxiv")
    ing.add_argument("--years", default="2024-2026", help="年份区间, 如 2024-2026")
    ing.add_argument("--max", type=int, default=20, help="每个源最多抓多少")
    ing.add_argument("--min-relevance", type=float, default=0.0, help="相关度阈值(0~1)")
    ing.add_argument("--explain", action="store_true", help="同时生成讲解(P3)")

    sub.add_parser("ping", help="测试大模型连通性")
    sub.add_parser("purge", help="删除采集来的论文（保留 seed 种子 38 篇）")

    args = ap.parse_args()
    if args.cmd == "ping":
        print(f"provider={config.PROVIDER} model={config.MODEL}")
        print("回复:", llm.ping())
        return
    if args.cmd == "purge":
        from . import db
        con = db.connect()
        n = con.execute("DELETE FROM papers WHERE source != 'seed'").rowcount
        con.commit()
        con.close()
        print("已删除采集论文:", n, "（seed 种子保留）")
        return
    if args.cmd == "ingest":
        parts = args.years.split("-")
        years = (int(parts[0]), int(parts[1])) if len(parts) == 2 else None
        pipeline.ingest(args.query, [s.strip() for s in args.sources.split(",") if s.strip()],
                        years, args.max, args.min_relevance, args.explain)


if __name__ == "__main__":
    main()
