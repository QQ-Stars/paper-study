# -*- coding: utf-8 -*-
"""取翻译/OCR 落库内容中含 $ 的真实片段，用于定位 MarkdownView 正则问题。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import db  # noqa: E402

con = db.connect()

print("===== translations 表：含 $$ 的论文 =====")
rows = con.execute(
    "SELECT t.paper_id, t.content FROM translations t WHERE t.content LIKE '%$$%' LIMIT 2"
).fetchall()
for r in rows:
    pid, content = r[0], r[1]
    print(f"\n--- {pid}（{len(content)} 字符）---")
    i = content.find("$$")
    print(repr(content[max(0, i - 120):i + 400]))

print("\n\n===== translations 表：仅含单个 $ 的片段 =====")
rows2 = con.execute(
    "SELECT paper_id, content FROM translations WHERE content LIKE '%$%' AND content NOT LIKE '%$$%' LIMIT 1"
).fetchall()
for r in rows2:
    pid, content = r[0], r[1]
    print(f"\n--- {pid} ---")
    i = content.find("$")
    print(repr(content[max(0, i - 120):i + 300]))

print("\n\n===== ocr_markdown 表：含 $ 的片段 =====")
try:
    rows3 = con.execute(
        "SELECT paper_id, content FROM ocr_markdown WHERE content LIKE '%$%' LIMIT 2"
    ).fetchall()
    for r in rows3:
        pid, content = r[0], r[1]
        print(f"\n--- {pid}（{len(content)} 字符）---")
        i = content.find("$")
        print(repr(content[max(0, i - 120):i + 400]))
except Exception as e:
    print(f"ocr_markdown 查询失败: {e}")

con.close()
