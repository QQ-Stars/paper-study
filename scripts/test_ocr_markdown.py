# -*- coding: utf-8 -*-
"""方案 A 完整测试：官方 Markdown 提示词 + grounding 标记清理。
用法: python scripts/test_ocr_markdown.py "<pdf路径>" [页数上限]"""
import re
import sys
import time
import urllib.error
import urllib.request
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, extract  # noqa: E402

GROUND_PROMPT = "<|grounding|>Convert the document to markdown."

# grounding 标记：<|ref|>title<|/ref|><|det|>[[x, y, w, h]]<|/det|>
_REF_RE = re.compile(r"<\|ref\|>.*?<\|/ref\|>")
_DET_RE = re.compile(r"<\|det\|>.*?<\|/det\|>")


def clean_grounding(md: str) -> str:
    md = _REF_RE.sub("", md)
    md = _DET_RE.sub("", md)
    # 清标记后残留的空行压缩、行尾空白去除
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def call(images_b64, cfg, user_prompt):
    content = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
               for b64 in images_b64]
    content.append({"type": "text", "text": user_prompt})
    payload = {"model": cfg["model"], "temperature": 0,
               "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if cfg["key"]:
        req.add_header("Authorization", "Bearer " + cfg["key"])
    with urllib.request.urlopen(req, timeout=600) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    return (doc.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def main():
    pdf_path = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    cfg = extract._ocr_settings()
    print(f"model={cfg['model']} base={cfg['base']} key={'已配置' if cfg['key'] else '未配置'}")

    images = extract._ocr_page_images(pdf_path, cfg["dpi"], max_pages)
    print(f"共 {len(images)} 页 @ {cfg['dpi']}DPI, base64 {sum(len(s) for s in images) // 1024} KB")

    started = time.time()
    parts = []
    try:
        for start in range(0, len(images), cfg["batch"]):
            batch = images[start:start + cfg["batch"]]
            print(f"批次 {start // cfg['batch'] + 1}: 页 {start + 1}-{start + len(batch)} ...")
            parts.append(call(batch, cfg, GROUND_PROMPT))
    except urllib.error.HTTPError as e:
        print(f"!! HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        return 1

    raw = "\n\n".join(p.strip() for p in parts if p and p.strip())
    cleaned = clean_grounding(raw)
    elapsed = time.time() - started

    ref_n = len(_REF_RE.findall(raw))
    det_n = len(_DET_RE.findall(raw))
    print(f"\n== 完成: 耗时 {elapsed:.1f}s ==")
    print(f"原始 {len(raw)} 字符 → 清理后 {len(cleaned)} 字符（移除 {ref_n} 个 ref 标记、{det_n} 个 det 标记）")
    print(f"残留标记检查: ref={len(_REF_RE.findall(cleaned))} det={len(_DET_RE.findall(cleaned))} "
          f"'<'尖括号残留={cleaned.count('<|')} 花括号占比={cleaned.count('}') * 100 // max(1, len(cleaned))}%")

    checks = {
        "标题 #": any(l.startswith("#") for l in cleaned.splitlines()),
        "子标题 ##": any(l.startswith("##") for l in cleaned.splitlines()),
        "列表 -/*": any(l.lstrip().startswith(("-", "*")) for l in cleaned.splitlines()),
        "LaTeX $": "$" in cleaned,
        "表格 |": any("|" in l for l in cleaned.splitlines()),
    }
    print("格式标记:", {k: ("✓" if v else "✗") for k, v in checks.items()})

    print("\n===== 清理后的 Markdown（前 2500 字符）=====")
    print(cleaned[:2500])
    print("===== 结束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
