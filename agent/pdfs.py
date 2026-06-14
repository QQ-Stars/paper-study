"""Download or repair local PDFs for papers already in the library."""
import json
import sys
from pathlib import Path
from . import config, db, util


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _row_pdf_path(row):
    sp = row.get("pdf_path")
    if sp:
        p = Path(sp)
        if not p.is_absolute():
            p = config.ROOT / sp
        if p.exists() and p.stat().st_size > 0:
            return p
    p = config.artifact_path("pdf", row.get("id"), ".pdf")
    if p.exists() and p.stat().st_size > 0:
        return p
    return None


def _target_rows(con, ids=None, limit=0):
    if ids:
        rows = []
        for pid in ids:
            row = con.execute("SELECT * FROM papers WHERE id=?", (str(pid),)).fetchone()
            if row:
                rows.append(dict(row))
        return rows[:limit or None]
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM papers ORDER BY created_at DESC, id DESC").fetchall()]
    rows = [r for r in rows if not _row_pdf_path(r)]
    return rows[:limit or None]


def download_pdfs(ids=None, limit=0):
    con = db.connect()
    rows = _target_rows(con, ids=ids, limit=limit)
    _p(f"TOTAL::{len(rows)}")
    ok = failed = skipped = 0
    for r in rows:
        pid = r.get("id")
        title = (r.get("title") or pid or "paper").replace("\n", " ")[:48]
        existing = _row_pdf_path(r)
        if existing:
            con.execute("UPDATE papers SET pdf_path=?, updated_at=datetime('now') WHERE id=?",
                        (str(existing), pid))
            con.commit()
            skipped += 1
            _p(f"PDFEXISTS::{title}")
            continue
        pdf_url = util.resolve_pdf_url(r, config.UNPAYWALL_EMAIL, config.S2_API_KEY)
        if not pdf_url:
            skipped += 1
            _p(f"PDFNOURL::{title}")
            continue
        dest = config.artifact_path("pdf", pid, ".pdf")
        _p(f"PDFSTART::{title}")

        def progress(done, total):
            _p(f"PDFPROG::{done}::{total or 0}::{title}")

        try:
            util.download_pdf(pdf_url, dest, progress=progress)
            con.execute("""UPDATE papers
                           SET pdf_url=COALESCE(NULLIF(pdf_url,''), ?),
                               pdf_path=?,
                               updated_at=datetime('now')
                           WHERE id=?""", (pdf_url, str(dest), pid))
            con.commit()
            ok += 1
            _p(f"PDFOK::{dest.name}")
        except Exception as e:
            failed += 1
            _p(f"PDFERR::{title}::{e}")
    con.close()
    result = {"ok": True, "downloaded": ok, "skipped": skipped, "failed": failed, "total": len(rows)}
    _p(f"DONE::{ok}::{skipped}::{failed}")
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return result
