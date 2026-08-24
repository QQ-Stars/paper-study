"""SQLite 读写（与 Node 共享同一个 app.db）。"""
import json
import sqlite3
import re
from pathlib import Path
from . import config


def _configure_connection(con, *, writable: bool):
    con.row_factory = sqlite3.Row
    if writable:
        con.execute("PRAGMA journal_mode=WAL")
    else:
        con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def connect():
    con = sqlite3.connect(config.DB_PATH)
    return _configure_connection(con, writable=True)


def connect_readonly():
    db_path = Path(config.DB_PATH).expanduser().resolve()
    con = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    return _configure_connection(con, writable=False)


def _ensure_batch_runs_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS batch_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT NOT NULL,
        finished_at TEXT NOT NULL DEFAULT (datetime('now')),
        total       INTEGER NOT NULL DEFAULT 0,
        done        INTEGER NOT NULL DEFAULT 0,
        failed      INTEGER NOT NULL DEFAULT 0,
        skipped     INTEGER NOT NULL DEFAULT 0,
        detail      TEXT NOT NULL DEFAULT '{}')""")


def record_batch_run(
    con,
    kind: str,
    *,
    total: int,
    done: int,
    failed: int,
    skipped: int,
    detail=None,
):
    """记录一次已结束的批处理；detail 以 JSON 文本持久化。"""
    _ensure_batch_runs_table(con)
    cursor = con.execute(
        """INSERT INTO batch_runs(kind, total, done, failed, skipped, detail)
           VALUES(?,?,?,?,?,?)""",
        (
            kind,
            total,
            done,
            failed,
            skipped,
            json.dumps(detail if detail is not None else {}, ensure_ascii=False),
        ),
    )
    con.commit()
    return cursor.lastrowid


def last_batch_run(con, kind: str):
    """返回指定类型最近一次批处理；尚无记录时返回 None。"""
    _ensure_batch_runs_table(con)
    row = con.execute(
        """SELECT id, kind, finished_at, total, done, failed, skipped, detail
           FROM batch_runs WHERE kind=? ORDER BY id DESC LIMIT 1""",
        (kind,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["detail"] = json.loads(result["detail"])
    return result


def title_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9一-龥]+", "", (s or "").lower())


# 会议名归一化（与前端 ui-redesign 的会议筛选保持一致）：统一大小写/常见别名，
# 避免 NeurIPS 与 NEURIPS、arXiv 与 arXiv.org 被当成两个会议。
VENUE_CANON = {
    "neurips": "NeurIPS", "nips": "NeurIPS",
    "cvpr": "CVPR", "iccv": "ICCV", "eccv": "ECCV", "wacv": "WACV",
    "icml": "ICML", "iclr": "ICLR", "aaai": "AAAI", "ijcai": "IJCAI",
    "acl": "ACL", "emnlp": "EMNLP", "naacl": "NAACL", "coling": "COLING",
    "tmlr": "TMLR", "tpami": "TPAMI", "corr": "arXiv",
}
# 会议「全名 → 缩写」子串匹配（顺序敏感：更具体的在前）
VENUE_FULL = [
    ("empirical methods in natural language", "EMNLP"),
    ("north american chapter", "NAACL"),
    ("findings of the association for computational linguistics", "ACL Findings"),
    ("association for computational linguistics", "ACL"),
    ("computer vision and pattern recognition", "CVPR"),
    ("european conference on computer vision", "ECCV"),
    ("winter conference on applications of computer vision", "WACV"),
    ("international conference on computer vision", "ICCV"),
    ("learning representations", "ICLR"),
    ("international conference on machine learning", "ICML"),
    ("neural information processing systems", "NeurIPS"),
    ("international joint conference on artificial intelligence", "IJCAI"),
    ("aaai conference on artificial intelligence", "AAAI"),
    ("advancement of artificial intelligence", "AAAI"),
    ("acm multimedia", "ACM MM"),
    ("international conference on multimedia", "ACM MM"),
]


def norm_venue(v):
    if not v:
        return v
    s = str(v).strip()
    k = s.lower()
    if k in VENUE_CANON:                             # 缩写大小写变体
        return VENUE_CANON[k]
    if k.startswith("arxiv"):                        # arXiv / arXiv.org / arXiv preprint…
        return "arXiv"
    for sub, abbr in VENUE_FULL:                     # 全名 → 缩写
        if sub in k:
            return abbr
    return s


def exists(con, arxiv_id=None, title_norm_v=None) -> bool:
    if arxiv_id and con.execute("SELECT 1 FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone():
        return True
    if title_norm_v and con.execute("SELECT 1 FROM papers WHERE title_norm=?", (title_norm_v,)).fetchone():
        return True
    return False


def known_categories(con):
    """库中已有的研究方向(type)与子主题(topic)，按使用频次降序——给大模型当“可复用类别表”。"""
    def col(name):
        rows = con.execute(
            f"SELECT {name} FROM papers WHERE {name} IS NOT NULL AND TRIM({name})!='' "
            f"GROUP BY {name} ORDER BY COUNT(*) DESC, {name}").fetchall()
        return [r[0] for r in rows]
    return col("type"), col("topic")


def ensure_vectors_table(con):
    # 自带建表，避免 agent 先于 node 应用新 schema 时找不到表
    con.execute("""CREATE TABLE IF NOT EXISTS paper_vectors (
        paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
        dim      INTEGER,
        vector   BLOB)""")


def ensure_edges_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS cite_edges (
        src_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        dst_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        PRIMARY KEY (src_id, dst_id))""")


def insert_paper(con, row: dict):
    if row.get("venue"):
        row["venue"] = norm_venue(row["venue"])
    cols = ",".join(row.keys())
    ph = ",".join(["?"] * len(row))
    con.execute(f"INSERT OR IGNORE INTO papers({cols}) VALUES({ph})", list(row.values()))
    con.commit()


def set_explainer(con, pid: str, md: str):
    con.execute("UPDATE papers SET explainer=?, updated_at=datetime('now') WHERE id=?", (md, pid))
    # 讲解是语义检索的嵌入文本来源 → 讲解变了就丢弃旧向量，下次语义检索按新讲解自动重嵌该篇。
    try:
        con.execute("DELETE FROM paper_vectors WHERE paper_id=?", (pid,))
    except Exception:
        pass
    con.commit()
    try:
        config.artifact_path("explainer", pid).write_text(md or "", encoding="utf-8")
    except Exception:
        pass


def set_translation(con, pid: str, md: str):
    # 自带建表，避免 agent 先于 node 应用新 schema 时找不到表
    con.execute("""CREATE TABLE IF NOT EXISTS translations (
        paper_id   TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
        content    TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT (datetime('now')))""")
    con.execute("""INSERT INTO translations(paper_id, content, updated_at) VALUES(?,?,datetime('now'))
                   ON CONFLICT(paper_id) DO UPDATE SET content=excluded.content, updated_at=datetime('now')""",
                (pid, md))
    con.commit()
    try:
        config.artifact_path("translation", pid).write_text(md or "", encoding="utf-8")
    except Exception:
        pass


def _ensure_ocr_markdown_table(con):
    # 独立表（不动 papers 表结构，避免触发后端 alembic schema revision 门禁）；
    # 自带建表，与 translations 同模式。
    con.execute("""CREATE TABLE IF NOT EXISTS ocr_markdown (
        paper_id   TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
        content    TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')))""")


def set_ocr_markdown(con, pid: str, md: str):
    """保存 PDF→Markdown(OCR) 结果；重复转换覆盖并刷新 updated_at。"""
    _ensure_ocr_markdown_table(con)
    con.execute("""INSERT INTO ocr_markdown(paper_id, content, created_at, updated_at)
                   VALUES(?,?,datetime('now'),datetime('now'))
                   ON CONFLICT(paper_id) DO UPDATE SET content=excluded.content, updated_at=datetime('now')""",
                (pid, md))
    con.commit()
    # 与讲解/翻译一致：同步落 .md 文件（目录由设置页 ocrMarkdownDir 配置，留空回退 data/ocr_markdown）
    try:
        config.artifact_path("ocr_markdown", pid).write_text(md or "", encoding="utf-8")
    except Exception:
        pass


def get_ocr_markdown(con, pid: str):
    """读已保存的 OCR Markdown；无记录返回 None。"""
    _ensure_ocr_markdown_table(con)
    row = con.execute("SELECT content FROM ocr_markdown WHERE paper_id=?", (pid,)).fetchone()
    return row[0] if row and row[0] else None
