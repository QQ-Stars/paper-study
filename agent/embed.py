"""论文向量 + 语义检索。

本地 **model2vec** 静态嵌入：纯 numpy + tokenizers，无需 GPU / torch / onnxruntime，
跨平台、无原生 DLL 依赖。默认多语种模型 → 中文 query 可直接匹配英文论文。
向量(L2 归一)存 paper_vectors；检索 = 余弦相似度暴力排序（库小，足够快）。

  python -m agent embed --scope all|missing      # 建/更新向量索引
  python -m agent semsearch --query "..." --k 30  # 语义检索(结果 JSON→stdout)
进度 → stderr，结果 JSON → stdout。"""
import os
import sys
import json
from . import config, db

# 下载缓存留项目内，且必须在导入 model2vec 之前设好
os.environ.setdefault("HF_HOME", str(config.MODEL_DIR / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

_model = None


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def model():
    global _model
    if _model is None:
        from model2vec import StaticModel
        _model = StaticModel.from_pretrained(config.EMBED_MODEL)
    return _model


def _l2norm(V):
    import numpy as np
    V = np.asarray(V, dtype="float32")
    if V.ndim == 1:
        V = V[None, :]
    n = np.linalg.norm(V, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (V / n).astype("float32")


def _api_embed(texts):
    """OpenAI 兼容的外部嵌入 API（如硅基流动 bge-m3）。批量请求 /embeddings，返回 L2 归一矩阵。"""
    import httpx
    base, key, mdl = config.EMBED_API_BASE, config.EMBED_API_KEY, config.EMBED_API_MODEL
    vecs, B = [], 32                         # 多数嵌入 API 单次 input 数组上限 ~32/64，取 32 稳妥
    with httpx.Client(timeout=60) as cli:
        for i in range(0, len(texts), B):
            batch = [(t if (t and t.strip()) else " ") for t in texts[i:i + B]]
            r = cli.post(f"{base}/embeddings",
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         json={"model": mdl, "input": batch})
            r.raise_for_status()
            data = sorted(r.json().get("data") or [], key=lambda d: d.get("index", 0))
            vecs.extend(d["embedding"] for d in data)
    return _l2norm(vecs)


def embed_texts(texts):
    """返回 L2 归一化的 float32 向量矩阵 (n, dim)。按 config.EMBED_PROVIDER 选 本地 / 外部 API。"""
    texts = list(texts)
    if config.EMBED_PROVIDER == "api" and config.EMBED_API_KEY:
        return _api_embed(texts)
    return _l2norm(model().encode(texts))


def _paper_text(row):
    # 优先用「讲解」(信息最全、多为中文，利于中文检索)，没有则退摘要，再退一句话总结，最后仅标题。
    title = (row["title"] or "").strip()
    body = (_col(row, "explainer") or _col(row, "abstract") or _col(row, "tldr") or "").strip()
    return (title + ". " + body).strip()[:5000]


def _col(row, name):
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def _index(con, rows):
    """嵌入给定 papers 行并写入 paper_vectors（仅 stderr 进度）。返回写入条数。"""
    db.ensure_vectors_table(con)
    if not rows:
        return 0
    if not (config.EMBED_PROVIDER == "api" and config.EMBED_API_KEY):
        _p("STAGE::model")
        model()                          # 本地模式：触发(可能的)一次性模型下载（API 模式跳过）
    _p(f"TOTAL::{len(rows)}")
    B, done = 64, 0
    for i in range(0, len(rows), B):
        batch = rows[i:i + B]
        vecs = embed_texts([_paper_text(r) for r in batch])
        for r, v in zip(batch, vecs):
            con.execute(
                "INSERT INTO paper_vectors(paper_id,dim,vector) VALUES(?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET dim=excluded.dim, vector=excluded.vector",
                (r["id"], int(v.shape[0]), v.tobytes()))
        con.commit()
        done += len(batch)
        _p(f"PROG::{done}::{len(rows)}")
    return done


def reindex(scope="missing"):
    """CLI：建立/更新向量索引。all=全量重建；missing=只补未索引。结果 JSON→stdout。"""
    con = db.connect()
    db.ensure_vectors_table(con)
    allrows = con.execute("SELECT id,title,tldr,abstract,explainer FROM papers").fetchall()
    if scope == "all":
        con.execute("DELETE FROM paper_vectors")
        con.commit()
        todo = allrows
    else:
        have = {r[0] for r in con.execute("SELECT paper_id FROM paper_vectors").fetchall()}
        todo = [r for r in allrows if r["id"] not in have]
    try:
        n = _index(con, todo)
    except Exception as e:
        _p(f"EMBEDERR::{e}")
        sys.stdout.write(json.dumps({"ok": False, "error": str(e), "indexed": 0}, ensure_ascii=False))
        sys.stdout.flush(); con.close(); return
    con.close()
    _p(f"DONE::{n}")
    sys.stdout.write(json.dumps({"ok": True, "indexed": n, "total": len(allrows)}, ensure_ascii=False))
    sys.stdout.flush()


def rank(query, k=30, exclude=None):
    """语义检索核心：返回 [{'id','score'}, ...]（按余弦降序）。不打印，供 CLI / MCP 复用。
    先嵌 query 得当前维度，再把「缺失 或 维度不符(换了嵌入模型/来源)」的论文重嵌 → 自动适配换模型；
    exclude 可排除某 paper_id（库内相似论文剔除自身）。进度→stderr。"""
    import numpy as np
    con = db.connect()
    db.ensure_vectors_table(con)
    _p("STAGE::query")
    qv = embed_texts([query])[0]
    qdim = int(qv.shape[0])
    have = {pid: dim for pid, dim in con.execute("SELECT paper_id, dim FROM paper_vectors").fetchall()}
    rows = con.execute("SELECT id,title,tldr,abstract,explainer FROM papers").fetchall()
    todo = [r for r in rows if have.get(r["id"]) != qdim]   # 缺失 / 维度不符 → (重)嵌
    if todo:
        _p("STAGE::index")
        _index(con, todo)
    allv = con.execute("SELECT paper_id, vector FROM paper_vectors").fetchall()
    con.close()
    ids, mat = [], []
    for r in allv:
        if exclude and r["paper_id"] == exclude:
            continue
        v = np.frombuffer(r["vector"], dtype="float32")
        if v.shape[0] == qdim:               # 只比同维向量（混入的旧维度向量本轮已被重嵌覆盖）
            ids.append(r["paper_id"]); mat.append(v)
    if not mat:
        return []
    sims = np.vstack(mat) @ qv
    order = np.argsort(-sims)[:max(1, int(k))]
    return [{"id": ids[i], "score": round(float(sims[i]), 4)} for i in order]


def semsearch(query, k=30):
    """CLI 包装：调用 rank() 并把结果 JSON 打到 stdout（前端语义检索用）。"""
    try:
        res = rank(query, k)
    except Exception as e:
        _p(f"EMBEDERR::{e}")
        sys.stdout.write(json.dumps({"ok": False, "error": str(e), "results": []}, ensure_ascii=False))
        sys.stdout.flush(); return
    _p(f"DONE::{len(res)}")
    sys.stdout.write(json.dumps({"ok": True, "results": res}, ensure_ascii=False))
    sys.stdout.flush()
