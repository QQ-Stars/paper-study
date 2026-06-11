"""多供应商大模型封装。DeepSeek/Qwen/OpenAI 走 OpenAI 兼容协议；结构化 JSON 输出 + 校验重试。"""
import json
from openai import OpenAI
from pydantic import ValidationError
from . import config
from .models import PaperAttributes

_client = None


def client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    return _client


def _cats(cats, empty):
    cats = [c for c in (cats or []) if c and str(c).strip()]
    return "、".join(cats) if cats else empty


def _classify_system(known_types, known_topics, theme):
    """动态分类提示：让大模型优先复用库中已有类别，没有再新建——使工具不绑定具体领域。"""
    return (
        f"你是论文分析助手。本论文库的研究主题是：{theme or '（未指定，请据论文自行归纳）'}。\n"
        "根据给定论文信息判断其研究属性，并**只输出一个 JSON 对象**。\n"
        "- type（研究方向/类型）：**先看能否归入库中已有类别**——"
        f"{_cats(known_types, '（库中暂无，请自拟一个简短类别）')}；"
        "能贴切归入就直接用其中之一；确实都不合适时，才**新建**一个简短(2~6字)的新类别。\n"
        "- topic（更细的子主题）：同样**优先复用**已有——"
        f"{_cats(known_topics, '（库中暂无，请自拟）')}；都不合适才新建简短子主题。\n"
        "- task：任务简述（可空）\n"
        "- models：用到的模型名数组\n"
        "- datasets：用到的数据集数组\n"
        "- contribution：一句话核心贡献\n"
        "- tldr：三句话以内速览\n"
        "- tags：关键词数组\n"
        "- relevance：与研究主题的相关度，0~1 小数\n"
        "复用已有类别能让分布图整洁；务必沿用相同措辞(别把“缓解”又写成“缓解方法”)。\n"
        "JSON 键固定为：type, topic, task, models, datasets, contribution, tldr, tags, relevance"
    )


def classify(stub, query: str = "", fulltext: str = None,
             known_types=None, known_topics=None, theme: str = "") -> PaperAttributes:
    sysmsg = _classify_system(known_types, known_topics, theme or query)
    info = (
        f"研究主题: {theme or query}\n"
        f"标题: {stub.title}\n"
        f"会议/年份: {stub.venue or ''} {stub.year or ''}\n"
        f"摘要: {stub.abstract or ''}\n"
        f"TLDR: {stub.tldr or ''}\n"
        f"领域标签: {', '.join(stub.fields or [])}"
    )
    if fulltext:
        info += f"\n\n正文片段(节选):\n{fulltext[:12000]}"
    last = None
    for _ in range(3):
        resp = client().chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "system", "content": sysmsg},
                      {"role": "user", "content": info}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = resp.choices[0].message.content
        try:
            return PaperAttributes(**json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            last = e
    raise RuntimeError(f"LLM 结构化输出多次失败: {last}")


def expand_queries(direction: str, n: int = 6) -> list:
    """把（可能中文/模糊的）研究方向 → 一组精准英文检索词组合。"""
    sys = (
        "你是学术检索专家。把用户给的研究方向（可能是中文或较模糊）转成一组用于 "
        "arXiv / Semantic Scholar 的英文检索关键词组合。要求：必要时翻译成英文；"
        "覆盖同义词、子方向、经典方法名、常用基准名；每条 2~6 个英文词，精准、可直接检索。"
        f"只输出 JSON 对象，形如 {{\"queries\": [\"...\", \"...\"]}}，包含约 {n} 条。"
    )
    try:
        resp = client().chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": f"研究方向: {direction}"}],
            response_format={"type": "json_object"}, temperature=0.4,
        )
        qs = json.loads(resp.choices[0].message.content).get("queries", [])
        qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()]
        return qs[:n] or [direction]
    except Exception:
        return [direction]


EXPLAINER_SYSTEM = (
    "你是一位资深的多模态大模型(MLLM)研究者，正在帮一位中文母语的研究生精读论文。"
    "请根据给定论文信息，写一份**中文 Markdown 讲解**：让 AI 小白也能读懂，但要专业、准确、不灌水。\n"
    "结构（严格用二级标题 ##，顺序如下）：\n"
    "1. 开头用一行引用块 `> …` 一句话点明：这篇论文做了什么、为什么值得读。\n"
    "2. `## 研究问题`：它针对哪种幻觉 / 哪个痛点？把动机讲清楚。\n"
    "3. `## 方法`：核心做法，分点讲；遇到公式或模块，先说直觉再说细节，别堆术语。\n"
    "4. `## 关键结论`：主要实验发现（数据集 / 指标 / 相对提升 / 消融，知道才写）。\n"
    "5. `## 可借鉴点`：方法、思路或实验设计里值得直接拿来用的点，分点列。\n"
    "硬性要求：只输出 Markdown 正文（不要用 ``` 把整体包起来）；忠于给定信息，"
    "**信息不足时宁可写“原文未提供”，绝不臆造**数据、指标或会议名。\n"
    "公式：本讲解**不支持 LaTeX 渲染**，不要输出 `\\begin{cases}`、`$$…$$`、`\\[...\\]` 这类公式块；"
    "需要时用一句话讲清直觉，符号用行内 `代码`（如 `logit_新 = (1+α)·logit(原) − α·logit(失真)`）。"
)


def generate_explainer(paper: dict, fulltext: str = None) -> str:
    """为一篇论文生成「科学方法论讲解」markdown。"""
    info = (
        f"# 论文信息\n"
        f"标题: {paper.get('title', '')}\n"
        f"会议/年份: {paper.get('venue') or '未知'} {paper.get('year') or ''}\n"
        f"作者: {paper.get('authors_str') or '原文未提供'}\n"
        f"已标注类型/主题: {paper.get('type') or '?'} / {paper.get('topic') or '?'}\n"
        f"已标注核心贡献: {paper.get('contribution') or '原文未提供'}\n"
        f"TLDR: {paper.get('tldr') or '原文未提供'}\n"
        f"摘要: {paper.get('abstract') or '原文未提供'}\n"
    )
    if fulltext:
        info += (
            "\n# 论文全文（已为你抽取，请据此精读，提取真实的方法细节、实验设置与具体数据）\n"
            f"{fulltext}\n"
        )
    resp = client().chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "system", "content": EXPLAINER_SYSTEM},
                  {"role": "user", "content": info}],
        temperature=0.5,
    )
    return (resp.choices[0].message.content or "").strip()


TRANSLATE_SYSTEM = (
    "你是专业的学术论文翻译，把用户给的英文论文片段（Markdown 格式）翻译成**简体中文**。\n"
    "- **必须翻译成中文**：无论片段从何处开始（可能从句子或单词中间开始），都要译成通顺中文，"
    "**绝不能原样返回英文**，不要漏译、不要概括删减、不要加译者注。\n"
    "- **保留原 Markdown 结构**：标题层级(#)、列表、加粗/斜体、代码块、引用块原样保留，只翻译其中的自然语言文字。\n"
    "- 数学公式：已是 LaTeX(`$...$`/`$$...$$`)的**原样保留、不翻译、不要改成代码块**；"
    "若遇到明显是被 PDF 抽取打乱的公式残片(充斥 =、上下标、\\mathbf、[b][M] 之类)，"
    "请尽量还原成正确 LaTeX 并用 `$$ ... $$` 包裹，使其可被渲染；无法识别就照抄。公式内变量与符号一律不译。\n"
    "- 专有名词、模型/数据集/方法名、缩写(如 LLaVA、POPE、Transformer、CVPR)保留英文。\n"
    "- 图表标题(如 Figure 3 / Table 2)：保留编号，翻译其说明文字。\n"
    "- 只输出译文本身，不要任何前后缀说明，不要用 ``` 把整体包起来。"
)


def _cjk_ratio(s: str) -> float:
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    en = sum(1 for ch in s if ch.isascii() and ch.isalpha())
    tot = cjk + en
    return cjk / tot if tot else 1.0


def translate_md(chunk: str) -> str:
    """把一段英文论文 Markdown 译成中文（保留结构）。若回来还是英文则换更强指令重试。"""
    src_en = sum(1 for c in chunk if c.isascii() and c.isalpha())
    user = chunk
    best = ""
    for attempt in range(3):
        try:
            resp = client().chat.completions.create(
                model=config.MODEL,
                messages=[{"role": "system", "content": TRANSLATE_SYSTEM},
                          {"role": "user", "content": user}],
                temperature=0.2 if attempt == 0 else 0.4,
            )
            out = (resp.choices[0].message.content or "").strip()
        except Exception:
            continue
        if not out:
            continue
        best = out
        if src_en < 40 or _cjk_ratio(out) >= 0.25:      # 译文里中文占比够 → 成功
            return out
        user = ("下面是英文论文片段，请**完整翻译成简体中文**，"
                "不要原样返回英文（即使它从句子中间开始）：\n\n" + chunk)
    return best or chunk                                # 实在译不动，返回最后结果/原文，至少不丢内容


def ping() -> str:
    """连通性自检：返回模型回复的一小段文本。"""
    resp = client().chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": "只回复两个字：你好"}],
        temperature=0,
    )
    return resp.choices[0].message.content
