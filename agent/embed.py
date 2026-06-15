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


def embed_texts(texts):
    """返回 L2 归一化的 float32 向量矩阵 (n, dim)。"""
    import numpy as np
    V = np.asarray(model().encode(list(texts)), dtype="float32")
    if V.ndim == 1:
        V = V[None, :]
    n = np.linalg.norm(V, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (V / n).astype("float32")


def _paper_text(row):
    title = (row["title"] or "").strip()
    extra = (row["tldr"] or row["abstract"] or "").strip()
    return (title + ". " + extra).strip()[:2000]


def _index(con, rows):
    """嵌入给定 papers 行并写入 paper_vectors（仅 stderr 进度）。返回写入条数。"""
    db.ensure_vectors_table(con)
    if not rows:
        return 0
    _p("STAGE::model")
    model()                              # 触发(可能的)一次性模型下载
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
    allrows = con.execute("SELECT id,title,tldr,abstract FROM papers").fetchall()
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
    自动补齐未索引论文；exclude 可排除某个 paper_id（用于“库内相似论文”剔除自身）。进度→stderr。"""
    import numpy as np
    con = db.connect()
    db.ensure_vectors_table(con)
    miss = con.execute("SELECT id,title,tldr,abstract FROM papers "
                       "WHERE id NOT IN (SELECT paper_id FROM paper_vectors)").fetchall()
    if miss:
        _p("STAGE::index")
        _index(con, miss)
    _p("STAGE::query")
    qv = embed_texts([query])[0]
    rows = con.execute("SELECT paper_id, vector FROM paper_vectors").fetchall()
    con.close()
    ids, mat = [], []
    for r in rows:
        if exclude and r["paper_id"] == exclude:
            continue
        v = np.frombuffer(r["vector"], dtype="float32")
        if v.shape[0] == qv.shape[0]:        # 跳过换模型后维度不符的旧向量
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
