"""DBLP 数据源：CS 顶会元数据最干净准确（venue/年份）。注意：无摘要/无PDF。

DBLP 限流很严：连续约 3 次后的突发请求会被**直接断开 TCP 连接**（RemoteProtocolError，
而非干净的 429），且**反复重试会持续刷新它的冷却**——越 hammer 封得越久。
故本源自带「节流 + 窗口限次 + 熔断」，绝不死命重试：
  ① 最小请求间隔 _MIN_INTERVAL；
  ② 滚动 _WINDOW 秒内最多 _MAX_REQS 次（采集多扩展词时，只查前几词，其余静默跳过）；
  ③ 任一请求失败即开「熔断」冷却 _COOLDOWN 秒，期间直接跳过，不再打扰 DBLP。
代价：单次采集 DBLP 只覆盖前几个检索词；这没关系——DBLP 只贡献干净的会议/年份元数据，
召回由 S2 / arXiv / OpenAlex 兜底。verify.py 的会议核实同样受益（多候选不再被封）。
"""
import re
import time
import threading
import httpx
from ..models import PaperStub
from .. import util

ENDPOINT = "https://dblp.org/search/publ/api"

_MIN_INTERVAL = 3.0          # 连续请求最小间隔（秒）
_MAX_REQS = 3                # 滚动窗口内最多请求次数
_WINDOW = 90.0              # 限次窗口（秒）
_COOLDOWN = 90.0           # 失败后的熔断冷却（秒）
_lock = threading.Lock()
_last = [0.0]
_hits = []
_cooldown_until = [0.0]


def _get(params):
    """节流 + 窗口限次 + 熔断的 DBLP 请求。超限/冷却/失败一律返回 None（静默跳过，不 hammer、不卡住采集）。"""
    with _lock:
        now = time.time()
        if now < _cooldown_until[0]:                 # 熔断冷却中 → 直接跳过
            return None
        _hits[:] = [t for t in _hits if now - t < _WINDOW]
        if len(_hits) >= _MAX_REQS:                  # 窗口内已达上限 → 跳过
            return None
        gap = _MIN_INTERVAL - (now - _last[0])
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.time()
        _hits.append(_last[0])
    try:
        r = httpx.get(ENDPOINT, params=params, timeout=20, headers=util.UA, follow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        with _lock:
            _cooldown_until[0] = time.time() + _COOLDOWN   # 失败 → 熔断，冷却期内不再打扰 DBLP
        return None


class DBLP:
    name = "dblp"

    def search(self, query, years, limit):
        r = _get({"q": query, "format": "json", "h": min(limit, 60)})
        if r is None:                                # 被节流/熔断跳过 → 本次无结果（采集继续走其它源）
            return
        hits = (((r.json() or {}).get("result") or {}).get("hits") or {}).get("hit") or []
        for h in hits[:limit]:
            info = h.get("info") or {}
            yr = info.get("year")
            if years and yr and not (str(years[0]) <= str(yr) <= str(years[1])):
                continue
            au = (info.get("authors") or {}).get("author")
            if isinstance(au, dict):
                au = [au]
            names = [(a.get("text") if isinstance(a, dict) else a) for a in (au or [])]
            ee = info.get("ee") or ""
            doi = ee.split("doi.org/")[-1] if "doi.org/" in ee else None
            venue = info.get("venue")
            if isinstance(venue, list):              # 同时收录于多处时 DBLP 给数组，取第一个
                venue = venue[0] if venue else None
            yield PaperStub(
                source="dblp", source_id=info.get("key", ""),
                title=re.sub(r"\s+", " ", info.get("title") or "").strip().rstrip("."),
                authors=names,
                venue=venue,
                year=str(yr) if yr else None,
                abstract=None,            # DBLP 不提供摘要
                url=info.get("url") or ee or None,
                pdf_url=None,
                doi=doi,
            )
