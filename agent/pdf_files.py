"""PDF file naming and archiving helpers."""
import re
import shutil
from pathlib import Path


_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.I)


def sanitize_title_stem(title, fallback="paper", max_length=160):
    def clean(value):
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(value or ""))
        value = re.sub(r"\s+", " ", value).strip().rstrip(". ")
        return value

    stem = clean(title) or clean(fallback) or "paper"
    if len(stem) > max_length:
        stem = stem[:max_length].strip().rstrip(". ")
    if _RESERVED.match(stem):
        stem = "_" + stem
    return stem or "paper"


def title_pdf_filename(title, fallback="paper"):
    return sanitize_title_stem(title, fallback=fallback) + ".pdf"


def _same_path(left, right):
    try:
        return Path(left).resolve() == Path(right).resolve()
    except Exception:
        return False


def _inside_dir(path, directory):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


def unique_pdf_path(pdf_dir, title, paper_id="", source_path=None):
    pdf_dir = Path(pdf_dir)
    base_stem = sanitize_title_stem(title)
    first = pdf_dir / f"{base_stem}.pdf"
    if not first.exists() or (source_path and _same_path(first, source_path)):
        return first

    id_stem = sanitize_title_stem(paper_id, fallback="paper", max_length=48)
    candidate = pdf_dir / f"{base_stem} - {id_stem}.pdf"
    if not candidate.exists() or (source_path and _same_path(candidate, source_path)):
        return candidate

    for i in range(2, 1000):
        candidate = pdf_dir / f"{base_stem} - {id_stem}-{i}.pdf"
        if not candidate.exists() or (source_path and _same_path(candidate, source_path)):
            return candidate
    return pdf_dir / f"{base_stem} - paper.pdf"


def archive_pdf(source_path, title, paper_id, pdf_dir):
    source = Path(source_path).resolve()
    pdf_dir = Path(pdf_dir).resolve()
    target = unique_pdf_path(pdf_dir, title, paper_id=paper_id, source_path=source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if _same_path(source, target):
        return target
    if _inside_dir(source, pdf_dir):
        source.replace(target)
    else:
        shutil.copy2(source, target)
    return target
