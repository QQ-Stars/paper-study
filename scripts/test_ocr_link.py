# -*- coding: utf-8 -*-
"""OCR 链路连通性测试：复用 agent/extract.py 的 _ocr_* 函数（与生产同链路）。
用法: python scripts/test_ocr_link.py "<pdf路径>" [页数上限]
输出: 配置/耗时/错误明细 + OCR 结果前 800 字符 + 格式标记检测。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, extract  # noqa: E402


def main() -> int:
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(config.ROOT) / "data" / "pdfs" /
        "Diffusion for Combating the Hallucination in Large Language Models (Student Abstract).pdf"
    )
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    cfg = extract._ocr_settings()
    print("== OCR 配置（来自 data/settings.json）==")
    print(f"base : {cfg['base']}")
    print(f"model: {cfg['model']}")
    print(f"key  : {'已配置（尾号 ' + cfg['key'][-4:] + '）' if cfg['key'] else '未配置'}")
    print(f"dpi={cfg['dpi']}  batch={cfg['batch']}  max_pages={cfg['max_pages']}")
    if not cfg["base"] or not cfg["model"] or not cfg["key"]:
        print("!! 配置不完整，无法测试")
        return 2

    if not Path(pdf_path).exists():
        print(f"!! PDF 不存在: {pdf_path}")
        return 2

    print(f"\n== 渲染页面图片（前 {max_pages} 页, {cfg['dpi']} DPI）==")
    images = extract._ocr_page_images(pdf_path, cfg["dpi"], max_pages)
    print(f"共 {len(images)} 页, base64 总大小约 {sum(len(s) for s in images) // 1024} KB")
    if not images:
        print("!! 没有可 OCR 的页面")
        return 2

    print("\n== 调用 OCR API（OpenAI 兼容 chat/completions, vision）==")
    started = time.time()
    try:
        # 与 _ocr_full_text 相同的批处理逻辑，但显式暴露异常（不静默）
        import urllib.error
        parts = []
        for start in range(0, len(images), cfg["batch"]):
            batch = images[start:start + cfg["batch"]]
            print(f"  批次 {start // cfg['batch'] + 1}: 页 {start + 1}-{start + len(batch)} ...")
            parts.append(extract._ocr_transcribe(batch, cfg))
        text = "\n\n".join(p.strip() for p in parts if p and p.strip())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        print(f"!! HTTP {e.code} {e.reason}\n响应体: {body}")
        return 1
    except urllib.error.URLError as e:
        print(f"!! 网络错误（API 不可达？）: {e.reason}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"!! 调用失败: {type(e).__name__}: {e}")
        return 1

    elapsed = time.time() - started
    print(f"\n== 成功：{len(text)} 字符, 耗时 {elapsed:.1f}s ==")
    print("\n----- OCR 输出前 800 字符 -----")
    print(text[:800])
    print("----- 结束 -----\n")

    print("== 格式标记检测 ==")
    checks = {
        "标题 #": any(l.startswith("#") for l in text.splitlines()),
        "列表 -/*": any(l.lstrip().startswith(("-", "*", "1.")) for l in text.splitlines()),
        "LaTeX $...$": "$" in text,
        "LaTeX $$...$$": "$$" in text,
        "加粗 **": "**" in text,
    }
    for name, hit in checks.items():
        print(f"  {'✓' if hit else '✗'} {name}")
    md_hits = sum(checks.values())
    print(f"\n结论: {'Markdown 格式（含结构标记）' if md_hits >= 2 else '接近纯文本（结构标记稀少）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
