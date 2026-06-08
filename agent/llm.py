"""多供应商大模型封装。DeepSeek/Qwen/OpenAI 走 OpenAI 兼容协议；结构化 JSON 输出 + 校验重试。"""
import json
from openai import OpenAI
from pydantic import ValidationError
from . import config
from .models import PaperAttributes, TYPES, TOPICS

_client = None


def client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    return _client


SYSTEM = (
    "你是论文分析助手。根据给定论文信息判断其研究属性，并**只输出一个 JSON 对象**。\n"
    f"- type：必须从 {TYPES} 里选最贴切的一个\n"
    f"- topic：必须从 {TOPICS} 里选最贴切的一个（都不符则填\"其他\"）\n"
    "- task：任务简述（可空）\n"
    "- models：用到的模型名数组\n"
    "- datasets：用到的数据集数组\n"
    "- contribution：一句话核心贡献\n"
    "- tldr：三句话以内速览\n"
    "- tags：关键词数组\n"
    "- relevance：与“检索方向”的相关度，0~1 小数\n"
    "JSON 键固定为：type, topic, task, models, datasets, contribution, tldr, tags, relevance"
)


def classify(stub, query: str = "") -> PaperAttributes:
    info = (
        f"检索方向: {query}\n"
        f"标题: {stub.title}\n"
        f"会议/年份: {stub.venue or ''} {stub.year or ''}\n"
        f"摘要: {stub.abstract or ''}\n"
        f"TLDR: {stub.tldr or ''}\n"
        f"领域标签: {', '.join(stub.fields or [])}"
    )
    last = None
    for _ in range(3):
        resp = client().chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "system", "content": SYSTEM},
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


def ping() -> str:
    """连通性自检：返回模型回复的一小段文本。"""
    resp = client().chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": "只回复两个字：你好"}],
        temperature=0,
    )
    return resp.choices[0].message.content
