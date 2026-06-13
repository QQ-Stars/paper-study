"""SQLite 读写（与 Node 共享同一个 app.db）。"""
import sqlite3
import re
from . import config


def connect():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def title_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9一-龥]+", "", (s or "").lower())


# 会议名归一化（与前端 public/app.js 的 normVenue 保持一致）：统一大小写/常见别名，
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
    con.commit()


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
