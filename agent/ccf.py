"""CCF 推荐目录（第七版/2026）会议·期刊 → 级别 A/B/C。数据见 db/ccf_ranks.json。

venue 先经 db.norm_venue 归一成标准简称再查表，因此 'Computer Vision and Pattern
Recognition' / 'CVPR' 都能命中。库外/未收录 venue 返回 None。"""
import json
from . import config, db

_MAP = None


def _load():
    global _MAP
    if _MAP is None:
        try:
            _MAP = json.loads((config.ROOT / "db" / "ccf_ranks.json").read_text(encoding="utf-8"))
        except Exception:
            _MAP = {}
    return _MAP


def rank(venue):
    """返回 'A' / 'B' / 'C' 或 None。"""
    if not venue:
        return None
    return _load().get(db.norm_venue(venue))


def is_a(venue):
    return rank(venue) == "A"
