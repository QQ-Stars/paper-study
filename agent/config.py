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

# 研究主题（宽泛方向）。分类时大模型据此 + 库中已有类别给论文归类；为空则用本次检索词。
# 让本工具不绑定某一领域：换个主题即可研究任意方向。可在网页 ⚙ 设置里改。
RESEARCH_THEME = _S.get("researchTheme") or os.getenv("RESEARCH_THEME") or ""

# 生成讲解读 PDF 全文时的安全上限（字符）。默认覆盖绝大多数会议论文(8~20页)全文；
# 仅为防超长综述撑爆模型上下文而设，可经 settings.json: explainMaxChars 调整。
EXPLAIN_MAX_CHARS = int(_S.get("explainMaxChars") or os.getenv("EXPLAIN_MAX_CHARS") or 120000)

# 语义检索的嵌入模型（本地 model2vec 静态嵌入，纯 numpy，无需 GPU/torch/onnx）。
# 默认多语种 → 中文 query 可直接匹配英文论文。可在 settings.json: embedModel 换。
EMBED_MODEL = _S.get("embedModel") or os.getenv("EMBED_MODEL") or "minishlab/potion-multilingual-128M"
# 模型下载缓存目录（留在项目内，符合“只装项目内”）。
MODEL_DIR = ROOT / ".models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("DB_PATH") or str(ROOT / "data" / "app.db")
# PDF 下载目录：默认 data/pdfs；网页设置可自定义（相对路径相对项目根）
_pd = _S.get("pdfDir")
PDF_DIR = Path(_pd) if _pd else (ROOT / "data" / "pdfs")
if not PDF_DIR.is_absolute():
    PDF_DIR = ROOT / PDF_DIR
PDF_DIR.mkdir(parents=True, exist_ok=True)
