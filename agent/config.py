"""配置：读取 .env，按供应商给默认 base_url/model。"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent          # study-app/
load_dotenv(ROOT / ".env")

# data/settings.json（网页“设置”写入）优先于 .env
import json as _json
_S = {}
_sp = ROOT / "data" / "settings.json"
if _sp.exists():
    try:
        _S = _json.loads(_sp.read_text(encoding="utf-8"))
    except Exception:
        _S = {}

PROVIDER = (_S.get("provider") or os.getenv("LLM_PROVIDER", "deepseek")).lower()
API_KEY = _S.get("apiKey") or os.getenv("LLM_API_KEY", "")

# 各供应商默认 (OpenAI 兼容 base_url, 默认模型)
PRESETS = {
    "deepseek":  ("https://api.deepseek.com", "deepseek-v4-flash"),
    "qwen":      ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai":    ("https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-latest"),
}
_base, _model = PRESETS.get(PROVIDER, PRESETS["deepseek"])
BASE_URL = _S.get("baseUrl") or os.getenv("LLM_BASE_URL") or _base
MODEL = _S.get("model") or os.getenv("LLM_MODEL") or _model
S2_API_KEY = _S.get("s2ApiKey") or os.getenv("S2_API_KEY", "")

DB_PATH = os.getenv("DB_PATH") or str(ROOT / "data" / "app.db")
PDF_DIR = ROOT / "data" / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)
