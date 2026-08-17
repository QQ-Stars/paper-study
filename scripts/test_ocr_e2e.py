# -*- coding: utf-8 -*-
"""端到端验证：OCR 转换 → 落库 → 缓存读取（不经后端 HTTP，直接 agent 层）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import db  # noqa: E402

con = db.connect()
row = con.execute("SELECT id, title FROM papers WHERE title LIKE '%Diffusion for Combating%'").fetchone()
if not row:
    print("!! 未找到测试论文")
    sys.exit(1)
pid, title = row[0], row[1]
print(f"测试论文: {pid}\n  {title}")

# 1. 转换前查询缓存（应为空）
before = db.get_ocr_markdown(con, pid)
print(f"\n[1] 转换前 DB 缓存: {'无' if not before else str(len(before)) + ' 字符'}")

# 2. 执行 OCR（ocrmd 命令同链路，含落库）
from agent import ocrmd  # noqa: E402
import contextlib, io  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    md = ocrmd.ocr_to_markdown(pid)
print(f"[2] OCR 完成: {len(md)} 字符")

# 3. 转换后查询缓存（应已落库）
after = db.get_ocr_markdown(con, pid)
print(f"[3] 转换后 DB 缓存: {'无' if not after else str(len(after)) + ' 字符'}")
assert after and after.strip() == md.strip(), "落库内容与转换结果不一致!"
print("    落库内容与转换结果一致 ✓")

# 4. 模拟讲解管道缓存读取路径（explain._explain_core 的 DB 优先分支同款调用）
cached = db.get_ocr_markdown(con, pid)
from agent import config  # noqa: E402
print(f"[4] 管道缓存读取: {len(cached[:config.EXPLAIN_MAX_CHARS])} 字符"
      f"（EXPLAIN_MAX_CHARS={config.EXPLAIN_MAX_CHARS}, PDF_TEXT_PROVIDER={config.PDF_TEXT_PROVIDER}）")

# 5. 表结构
cols = con.execute("PRAGMA table_info(ocr_markdown)").fetchall()
print(f"[5] ocr_markdown 表结构: {[c[1] for c in cols]}")

print("\n----- 落库 Markdown 前 400 字符 -----")
print(after[:400])
con.close()
print("\n端到端验证通过 ✓")
