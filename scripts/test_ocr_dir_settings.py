# -*- coding: utf-8 -*-
"""验证 settings 契约纳入 ocrMarkdownDir：_validate_patch 接受 + _directory_view 返回三元组。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.application.settings import SettingsService  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    svc = SettingsService(settings_path=Path(tmp) / "settings.json", credential_service=None)

    # 1. _validate_patch 应接受 ocrMarkdownDir（string 字段）
    norm = svc._validate_patch({"ocrMarkdownDir": "data/my_ocr_dir"})
    assert norm.get("ocrMarkdownDir") == "data/my_ocr_dir", norm
    print("[1] _validate_patch 接受 ocrMarkdownDir ✓ ->", norm)

    # 2. 非法类型应拒绝
    try:
        svc._validate_patch({"ocrMarkdownDir": 123})
        print("[2] !! 非法类型未被拒绝")
    except Exception as e:
        print(f"[2] 非法类型正确拒绝 ✓ ({type(e).__name__})")

    # 3. _directory_view 返回 ocrMarkdownDir / defaultOcrMarkdownDir / resolvedOcrMarkdownDir
    view = svc._directory_view({})
    for key in ("ocrMarkdownDir", "defaultOcrMarkdownDir", "resolvedOcrMarkdownDir"):
        assert key in view, f"缺少 {key}"
    print("[3] _directory_view 三元组 ✓")
    print("    ocrMarkdownDir        =", repr(view["ocrMarkdownDir"]))
    print("    defaultOcrMarkdownDir =", view["defaultOcrMarkdownDir"])
    print("    resolvedOcrMarkdownDir =", view["resolvedOcrMarkdownDir"])

    # 4. 已配置值时 resolved 指向配置目录
    view2 = svc._directory_view({"ocrMarkdownDir": "data/my_ocr_dir"})
    print("[4] 配置后 resolved =", view2["resolvedOcrMarkdownDir"])
    assert str(view2["resolvedOcrMarkdownDir"]).endswith("my_ocr_dir")
    print("\nsettings 契约验证通过 ✓")
