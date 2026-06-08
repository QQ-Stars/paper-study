"""配置：读取 .env，按供应商给默认 base_url/model。"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent          # study-app/
load_dotenv(ROOT / ".env")

PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").lower()
API_KEY = os.getenv("LLM_API_KEY", "")

# 各供应商默认 (OpenAI 兼容 base_url, 默认模型)
PRESETS = {
    "deepseek":  ("https://api.deepseek.com", "deepseek-v4-flash"),
    "qwen":      ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai":    ("https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-latest"),
}
_base, _model = PRESETS.get(PROVIDER, PRESETS["deepseek"])
BASE_URL = os.getenv("LLM_BASE_URL") or _base
MODEL = os.getenv("LLM_MODEL") or _model

DB_PATH = os.getenv("DB_PATH") or str(ROOT / "data" / "app.db")
PDF_DIR = ROOT / "data" / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)
