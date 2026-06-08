"""pydantic 数据模型：PaperStub(源给的半成品) / PaperAttributes(LLM 抽的成品)。"""
from pydantic import BaseModel
from typing import Optional, List

TYPES = ["检测", "缓解·解码", "缓解·训练", "机制", "评测", "定义", "其他"]
TOPICS = ["知识-视觉冲突", "多图", "多物体", "通用物体", "语言先验", "其他"]


class PaperStub(BaseModel):
    source: str
    source_id: str = ""
    title: str
    authors: List[str] = []
    venue: Optional[str] = None
    year: Optional[str] = None
    abstract: Optional[str] = None
    tldr: Optional[str] = None
    fields: List[str] = []
    citations: Optional[int] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None
    s2_id: Optional[str] = None


class PaperAttributes(BaseModel):
    type: str = "其他"
    topic: str = "其他"
    task: Optional[str] = None
    models: List[str] = []
    datasets: List[str] = []
    contribution: str = ""
    tldr: Optional[str] = None
    tags: List[str] = []
    relevance: Optional[float] = None
