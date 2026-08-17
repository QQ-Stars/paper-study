# -*- coding: utf-8 -*-
"""检查 DeepSeek-OCR 输出的完整结构：特殊 token 占比、正文起始位置。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, extract  # noqa: E402

pdf_path = sys.argv[1]
max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 1

cfg = extract._ocr_settings()
images = extract._ocr_page_images(pdf_path, cfg["dpi"], max_pages)
print(f"页数: {len(images)}")
started = time.time()
text = extract._ocr_transcribe(images[:1], cfg)
print(f"耗时 {time.time() - started:.1f}s, 总长 {len(text)} 字符")

brace = text.count("}")
print(f"花括号 }} 数量: {brace} ({brace * 100 // max(1, len(text))}%)")

# 找到第一个非花括号连续段的起点
i = 0
while i < len(text) and text[i] in "}\n\r ":
    i += 1
print(f"前导特殊字符长度: {i}")
print("\n----- 跳过前导后的前 1200 字符 -----")
print(text[i:i + 1200])
print("----- 中段 1200 字符 -----")
mid = len(text) // 2
print(text[mid:mid + 1200])
print("----- 末尾 800 字符 -----")
print(text[-800:])
