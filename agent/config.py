"""配置：读取 .env，按供应商给默认 base_url/model。"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent          # study-app/
load_dotenv(ROOT / ".env")

# data/settings.json（网页“设置”写入）优先于 .env。
# 运行时可通过 PAPER_STUDY_SETTINGS_PATH 指定隔离/候选运行所用的设置文件；
# 生产默认仍是仓库根目录下的 data/settings.json。
import json as _json
_S = {}
_settings_path = os.getenv("PAPER_STUDY_SETTINGS_PATH", "").strip()
_sp = Path(_settings_path).expanduser() if _settings_path else ROOT / "data" / "settings.json"
if not _sp.is_absolute():
    _sp = ROOT / _sp
if _sp.exists():
    try:
        _S = _json.loads(_sp.read_text(encoding="utf-8"))
    except Exception:
        _S = {}


def _dir_from_settings(key: str, env_key: str, default: str) -> Path:
    val = (_S.get(key) or os.getenv(env_key) or "").strip()
    p = Path(val) if val else (ROOT / default)
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p

PROVIDER = (_S.get("provider") or os.getenv("LLM_PROVIDER", "deepseek")).lower()
API_KEY = os.getenv("PAPER_STUDY_LLM_API_KEY") or _S.get("apiKey") or os.getenv("LLM_API_KEY", "")

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
S2_API_KEY = (
    os.getenv("PAPER_STUDY_S2_API_KEY")
    or _S.get("s2ApiKey")
    or os.getenv("S2_API_KEY", "")
)

# 研究主题（宽泛方向）。分类时大模型据此 + 库中已有类别给论文归类；为空则用本次检索词。
# 让本工具不绑定某一领域：换个主题即可研究任意方向。可在网页 ⚙ 设置里改。
RESEARCH_THEME = _S.get("researchTheme") or os.getenv("RESEARCH_THEME") or ""

# 生成讲解读 PDF 全文时的安全上限（字符）。默认覆盖绝大多数会议论文(8~20页)全文；
# 仅为防超长综述撑爆模型上下文而设，可经 settings.json: explainMaxChars 调整。
EXPLAIN_MAX_CHARS = int(_S.get("explainMaxChars") or os.getenv("EXPLAIN_MAX_CHARS") or 120000)

# 全文翻译管道（网页 ⚙ 设置「大模型与翻译管道」分区）：
# translateMode=chunked 分块并发翻译（默认，稳）；full 整篇一次送入（需大上下文模型）。
TRANSLATE_MODE = (_S.get("translateMode") or os.getenv("TRANSLATE_MODE") or "chunked").strip().lower()
if TRANSLATE_MODE not in ("chunked", "full"):
    TRANSLATE_MODE = "chunked"
# 分块大小（字符，仅 chunked 模式生效）；翻译全文截断上限（独立于讲解）；分块翻译并发数。
TRANSLATE_CHUNK_SIZE = max(500, int(_S.get("translateChunkSize") or os.getenv("TRANSLATE_CHUNK_SIZE") or 3800))
TRANSLATE_MAX_CHARS = int(_S.get("translateMaxChars") or os.getenv("TRANSLATE_MAX_CHARS") or EXPLAIN_MAX_CHARS)
TRANSLATE_WORKERS = max(1, int(_S.get("translateWorkers") or os.getenv("TRANSLATE_WORKERS") or 4))
# 翻译前是否剔除参考文献/致谢（默认开，对应 extract.strip_references）。
_v = _S.get("translateSkipReferences")
if _v is None:
    TRANSLATE_SKIP_REFERENCES = os.getenv("TRANSLATE_SKIP_REFERENCES", "1") not in ("0", "false", "False", "")
else:
    TRANSLATE_SKIP_REFERENCES = bool(_v)

# LLM 请求超时（毫秒）：OpenAI 兼容客户端全局请求超时；0/缺省 → SDK 默认（约 600s）。
_llm_timeout = _S.get("llmTimeout")
if _llm_timeout is None:
    _llm_timeout = os.getenv("LLM_TIMEOUT", 0)
LLM_TIMEOUT = int(_llm_timeout or 0)

# PDF 文本提取方式：default=本地 pymupdf4llm 解析（默认，行为不变）；
# ocr=调用 OCR 模型 API（OpenAI 兼容 chat/vision 接口）提取文本；失败直接报错，
# 不与 default 的本地解析结果混用。
# 在网页 ⚙ 设置里改；只影响讲解/翻译的全文读取（full_text），不影响采集分类。
PDF_TEXT_PROVIDER = (_S.get("pdfTextProvider") or os.getenv("PDF_TEXT_PROVIDER") or "default").strip().lower()
_ocr_enabled_value = _S.get("ocrEnabled")
if _ocr_enabled_value is None:
    _ocr_enabled_value = os.getenv("OCR_ENABLED", "0")
OCR_ENABLED = (
    _ocr_enabled_value is True
    or str(_ocr_enabled_value).strip().lower() in {"1", "true", "yes", "on"}
)
OCR_API_BASE = (_S.get("ocrBaseUrl") or _S.get("ocrApiBase") or os.getenv("OCR_BASE_URL") or "").strip().rstrip("/")
OCR_API_KEY = (os.getenv("PAPER_STUDY_OCR_API_KEY") or _S.get("ocrApiKey") or os.getenv("OCR_API_KEY") or "").strip()
OCR_MODEL = (_S.get("ocrModel") or os.getenv("OCR_MODEL") or "").strip()
OCR_TIMEOUT = int(_S.get("ocrTimeout") or os.getenv("OCR_TIMEOUT") or 60000)
OCR_DPI = int(_S.get("ocrDpi") or os.getenv("OCR_DPI") or 200)
# 每次 OCR 请求携带的页数（多页一次请求，减少往返）；0/缺省按 4 页。
OCR_PAGE_BATCH = int(_S.get("ocrPageBatchSize") or os.getenv("OCR_PAGE_BATCH_SIZE") or 4) or 4
# OCR 最多处理的页数；0=不限（仍受 EXPLAIN_MAX_CHARS 截断保护）。
OCR_MAX_PAGES = int(_S.get("ocrMaxPages") or os.getenv("OCR_MAX_PAGES") or 0)
# 批量 PDF→Markdown(OCR) 的篇级并发数（POST /api/ocr-md-batch）。
# OCR API 按页计费且有限流，并发过高易触发 429；默认 2，可用设置页 ocrMaxConcurrency 调整。
OCR_BATCH_WORKERS = max(
    1,
    min(
        8,
        int(
            _S.get("ocrMaxConcurrency")
            or os.getenv("OCR_MAX_CONCURRENCY")
            or os.getenv("OCR_BATCH_WORKERS")
            or 2
        ),
    ),
)

# 语义检索的嵌入模型（本地 model2vec 静态嵌入，纯 numpy，无需 GPU/torch/onnx）。
# 默认多语种 → 中文 query 可直接匹配英文论文。设置页的 canonical 字段是
# embedApiModel；embedModel 仅保留为旧 settings.json 的兼容别名。
EMBED_MODEL = (
    _S.get("embedApiModel")
    or _S.get("embedModel")
    or os.getenv("EMBED_MODEL")
    or os.getenv("EMBED_API_MODEL")
    or "minishlab/potion-multilingual-128M"
)
# 模型下载缓存目录（留在项目内，符合“只装项目内”）。
MODEL_DIR = ROOT / ".models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# 语义检索嵌入来源：local=本地 model2vec（默认，无需联网/Key）；
# api=OpenAI 兼容的外部嵌入 API（如硅基流动 SiliconFlow 的 BAAI/bge-m3，上下文 8K、多语种）。
# 在网页 ⚙ 设置里改；切换来源/模型会改变向量维度 → 下次语义检索自动重嵌全库（见 embed.rank 的自愈）。
EMBED_PROVIDER = (_S.get("embedProvider") or os.getenv("EMBED_PROVIDER") or "local").strip().lower()
EMBED_API_BASE = (_S.get("embedApiBase") or os.getenv("EMBED_API_BASE")
                  or "https://api.siliconflow.cn/v1").strip().rstrip("/")
EMBED_API_KEY = (os.getenv("PAPER_STUDY_EMBED_API_KEY") or _S.get("embedApiKey") or os.getenv("EMBED_API_KEY") or "").strip()
EMBED_API_MODEL = (_S.get("embedApiModel") or os.getenv("EMBED_API_MODEL") or "BAAI/bge-m3").strip()

DB_PATH = os.getenv("DB_PATH") or str(ROOT / "data" / "app.db")
# Artifact directories. Relative paths are resolved from project root.
PDF_DIR = _dir_from_settings("pdfDir", "PDF_DIR", "data/pdfs")
EXPLAINER_DIR = _dir_from_settings("explainerDir", "EXPLAINER_DIR", "data/explainers")
TRANSLATION_DIR = _dir_from_settings("translationDir", "TRANSLATION_DIR", "data/translations")
OCR_MARKDOWN_DIR = _dir_from_settings(
    "ocrMarkdownDir",
    "OCR_MARKDOWN_DIR",
    "data/ocr_markdown",
)

# Unpaywall 联系邮箱（按 DOI 找开放获取 PDF 时需带一个联系邮箱；可在设置 contactEmail 覆盖）
UNPAYWALL_EMAIL = (_S.get("contactEmail") or "paper-study@users.noreply.github.com").strip()


def artifact_path(kind: str, paper_id: str, ext: str = ".md") -> Path:
    base = {
        "pdf": PDF_DIR,
        "explainer": EXPLAINER_DIR,
        "translation": TRANSLATION_DIR,
        "ocr_markdown": OCR_MARKDOWN_DIR,
    }[kind]
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(paper_id or "paper"))
    safe = safe.strip(".-_") or "paper"
    return base / f"{safe}{ext}"
