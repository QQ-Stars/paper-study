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


def insert_paper(con, row: dict):
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
