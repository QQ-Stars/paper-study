"""PDF -> 文本（PyMuPDF）。仅在需要正文（写讲解）时用。"""


def first_pages(path, n: int = 8, abstract: str = None) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    text = "\n".join(doc[i].get_text() for i in range(min(n, doc.page_count)))
    if abstract:
        text = f"摘要:{abstract}\n\n{text}"
    return text[:24000]
