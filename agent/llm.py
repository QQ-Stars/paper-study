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


PDF_META_SYSTEM = (
    "你从一篇学术论文的首页文本中抽取书目信息。**只输出一个 JSON 对象**，键固定为：\n"
    "- title：论文真正的标题（英文原文，合并断行成一行）。不是页眉、会议名、arXiv 编号或栏目名。\n"
    "- authors：作者姓名数组（没有就空数组）。\n"
    "- year：四位发表年份字符串；判断不了就 null。\n"
    "- abstract：摘要正文（英文原文，尽量完整；首页没有摘要就空串）。\n"
    "忽略 arXiv 行、脚注、版权与投稿信息。只依据给定文本，不要臆造。"
)


def parse_pdf_meta(page_text: str) -> dict:
    """从 PDF 首页文本里抽出 {title, authors, year, abstract}。失败时返回空骨架。"""
    try:
        resp = client().chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "system", "content": PDF_META_SYSTEM},
                      {"role": "user", "content": (page_text or "")[:6000]}],
            response_format={"type": "json_object"}, temperature=0,
        )
        d = json.loads(resp.choices[0].message.content) or {}
    except Exception:
        d = {}
    yr = d.get("year")
    return {
        "title": (d.get("title") or "").strip(),
        "authors": [a for a in (d.get("authors") or []) if isinstance(a, str) and a.strip()],
        "year": str(yr).strip() if yr else None,
        "abstract": (d.get("abstract") or "").strip() or None,
    }


VENUE_NORM_SYSTEM = (
    "你是学术会议/期刊名称规整助手。给你一组会议或期刊名称（JSON 数组），为每个给出**标准简称**。规则：\n"
    "- 知名会议/期刊一律用公认缩写：CVPR, ICCV, ECCV, WACV, NeurIPS, ICML, ICLR, AAAI, IJCAI, "
    "ACL, EMNLP, NAACL, COLING, ACM MM, TPAMI, IJCV, TMLR, TACL 等。\n"
    "- 名称里括号内含缩写（如 “…(ICFTIC)”）就用括号里的缩写。\n"
    "- arXiv / arXiv.org / preprint / CoRR 一律写成 'arXiv'。\n"
    "- 没有公认缩写的期刊或小会：去掉 'Proceedings of the'、年份、'IEEE'/'ACM' 等冗余前后缀，"
    "保留一个**尽量短**的规范名称。\n"
    "- **已经是规范缩写的原样保留**，不要改动。\n"
    "只输出一个 JSON 对象：键=我给的每个原名（原样照抄），值=对应简称。不要解释。"
)


def canonicalize_venues(venues: list) -> dict:
    """把一组会议/期刊名规整成标准简称。返回 {原名: 简称}。分批以防过长。"""
    out = {}
    uniq = [v for v in dict.fromkeys(venues) if v and str(v).strip()]
    for i in range(0, len(uniq), 60):
        chunk = uniq[i:i + 60]
        try:
            resp = client().chat.completions.create(
                model=config.MODEL,
                messages=[{"role": "system", "content": VENUE_NORM_SYSTEM},
                          {"role": "user", "content": json.dumps(chunk, ensure_ascii=False)}],
                response_format={"type": "json_object"}, temperature=0,
            )
            d = json.loads(resp.choices[0].message.content) or {}
            for k, v in d.items():
                if isinstance(v, str) and v.strip():
                    out[k] = v.strip()
        except Exception:
            continue
    return out


def expand_queries(direction: str, n: int = 8) -> list:
    """把（可能中文/模糊的）研究方向 → 一组多样化的精准英文检索词组合（目标：最大化召回）。"""
    sys = (
        "你是学术文献检索专家。把用户给的研究方向（可能中文或较模糊）转成一组用于 "
        "arXiv / Semantic Scholar / DBLP 的英文检索关键词组合，目标是**最大化召回**——"
        "尽量从不同角度切入，把换个说法就搜不到的论文也捞回来。\n"
        "每条从不同切入点出发，整体覆盖：①核心问题的同义/近义表述 ②细分子任务/子问题 "
        "③经典或代表性方法名 ④常用评测基准/数据集名 ⑤该任务的别称或相关现象词 "
        "⑥关键术语的缩写与全称（如 MLLM / multimodal large language model）。\n"
        "硬性要求：必要时翻译成英文；每条 2~6 个英文词、精准可直接检索；"
        "**条目之间措辞与角度尽量不同、避免雷同**；宁可多覆盖一个角度也别漏。"
        f"只输出 JSON 对象，形如 {{\"queries\": [\"...\", \"...\"]}}，包含约 {n} 条。"
    )
    try:
        resp = client().chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": f"研究方向: {direction}"}],
            response_format={"type": "json_object"}, temperature=0.6,
        )
        qs = json.loads(resp.choices[0].message.content).get("queries", [])
        seen, out = set(), []
        for q in qs:                                  # 去雷同（忽略大小写）
            if isinstance(q, str) and q.strip() and q.strip().lower() not in seen:
                seen.add(q.strip().lower()); out.append(q.strip())
        return out[:n] or [direction]
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
    "- **追求通顺地道**：按整句意思意译，而非逐词硬译；行文要像一篇连贯的中文科技论文，"
    "句子之间自然衔接、读起来不生硬。\n"
    "- **合并 PDF 抽取造成的句内硬换行与连字符断词**（段落中间的换行、represen-/tation 这类断词），"
    "先还原成完整句子再翻译；但段落之间的空行、标题、列表项等**真实结构必须保留**。\n"
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


TRANSLATE_SNIPPET_SYSTEM = (
    "你是专业的学术论文翻译。用户会给你一段从 PDF 里直接选取的英文文字"
    "（可能带换行、连字符断词，甚至从句子中间开始）。把它翻译成**通顺、地道的简体中文**。\n"
    "- **只输出译文本身**：不要重复原文、不要任何前后缀说明、不要加引号或代码块。\n"
    "- 合并 PDF 造成的硬换行与连字符断词（如 represen-\\ntation → representation），译成连贯句子，意译而非逐字硬译。\n"
    "- 专有名词、模型/数据集/方法名、缩写(如 LLaVA、POPE、Transformer、CVPR)保留英文。\n"
    "- 数学公式/变量/符号(如 $x$、\\alpha)保持原样不译。\n"
    "- 即使片段很短或从句中间开始，也要尽力译成中文，绝不原样返回英文。"
)


def translate_snippet(text: str) -> str:
    """划词翻译：把用户从 PDF 选中的一小段英文译成流畅中文（不保留 Markdown 结构）。"""
    text = (text or "").strip()
    if not text:
        return ""
    resp = client().chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "system", "content": TRANSLATE_SNIPPET_SYSTEM},
                  {"role": "user", "content": text}],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def ping() -> str:
    """连通性自检：返回模型回复的一小段文本。"""
    resp = client().chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": "只回复两个字：你好"}],
        temperature=0,
    )
    return resp.choices[0].message.content
