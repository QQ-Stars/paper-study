# -*- coding: utf-8 -*-
"""用 SiliconFlow 官方提示词复测 DeepSeek-OCR：
General: Free OCR. / Markdown: <|grounding|>Convert the document to markdown."""
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, extract  # noqa: E402


def call(images_b64, cfg, user_prompt, system_prompt=None):
    content = []
    for b64 in images_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    content.append({"type": "text", "text": user_prompt})
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})
    payload = {"model": cfg["model"], "temperature": 0, "messages": messages}
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


def report(name, text):
    print(f"\n===== {name} =====")
    print(f"总长 {len(text)} 字符, '}}' 占比 {text.count('}') * 100 // max(1, len(text))}%")
    print("----- 前 800 字符 -----")
    print(text[:800])
    checks = {
        "标题 #": any(l.startswith("#") for l in text.splitlines()),
        "列表 -/*": any(l.lstrip().startswith(("-", "*")) for l in text.splitlines()),
        "LaTeX $": "$" in text,
        "加粗 **": "**" in text,
    }
    print("格式标记:", {k: ("✓" if v else "✗") for k, v in checks.items()})


def main():
    pdf_path = sys.argv[1]
    cfg = extract._ocr_settings()
    images = extract._ocr_page_images(pdf_path, cfg["dpi"], 1)
    print(f"1 页图片, base64 {len(images[0]) // 1024} KB")

    for name, prompt in [
        ("官方 Markdown 提示词", "<|grounding|>Convert the document to markdown."),
        ("官方 General 提示词", "Free OCR."),
    ]:
        started = time.time()
        try:
            text = call(images, cfg, prompt)
        except urllib.error.HTTPError as e:
            print(f"\n===== {name} =====\n!! HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"\n===== {name} =====\n!! {type(e).__name__}: {e}")
            continue
        print(f"(耗时 {time.time() - started:.1f}s)", end="")
        report(name, text)


if __name__ == "__main__":
    main()
