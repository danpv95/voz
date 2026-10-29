"""Build docs/guia-voz.html and docs/guia-voz.pdf from docs/guia-voz.src.html.

    python3.12 scripts/build_guide.py

The table of contents needs page numbers, which only exist once the PDF is laid
out: the script renders twice (the second pass has the numbers from the first) and
reports whether they came out stable. Needs Google Chrome (headless) and pdftotext
(poppler: `brew install poppler`). Set CHROME to use another Chrome/Chromium binary.
"""
import html
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SRC = DOCS / "guia-voz.src.html"
OUT_HTML = DOCS / "guia-voz.html"
OUT_PDF = DOCS / "guia-voz.pdf"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def prepare(src: str):
    """Give every numbered h2 an id, collect (level, id, text)."""
    counter = [0]

    def add_id(m):
        counter[0] += 1
        return f'<h2 id="h{counter[0]}">{m.group(1)}</h2>'

    src = re.sub(r"<h2>(\d+\.\d+ .*?)</h2>", add_id, src)
    items = []
    for m in re.finditer(r'<h([12]) id="([^"]+)">(.*?)</h\1>', src):
        level, hid, inner = int(m.group(1)), m.group(2), m.group(3)
        text = strip_tags(inner)
        if level == 2 and not re.match(r"\d+\.\d+ ", text):
            continue
        items.append((level, hid, text))
    return src, items


def toc_html(items, pages):
    rows = []
    for level, hid, text in items:
        pn = pages.get(hid, "00")
        rows.append(
            f'    <a class="t{level}" href="#{hid}"><span class="tt">{html.escape(text)}</span>'
            f'<span class="dots"></span><span class="pn">{pn}</span></a>'
        )
    return "\n".join(rows)


def render():
    if OUT_PDF.exists():
        OUT_PDF.unlink()
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()],
        check=True, capture_output=True, timeout=180,
    )


def pages_text():
    out = subprocess.run(["pdftotext", "-layout", str(OUT_PDF), "-"], check=True,
                         capture_output=True, text=True).stdout
    return [re.sub(r"\s+", " ", p) for p in out.split("\f")][:-1]


def locate(items, texts):
    pages = {}
    # first page after the TOC page(s): the page where "1. Qué es y filosofía" shows AFTER "Índice"
    toc_idx = next(i for i, t in enumerate(texts) if "Índice" in t and "11. Licencia" in t)
    start = toc_idx + 1
    for level, hid, text in items:
        needle = re.sub(r"\s+", " ", text)
        for i in range(start, len(texts)):
            if needle in texts[i]:
                pages[hid] = str(i + 1)
                break
        else:
            print("NOT FOUND:", text, file=sys.stderr)
    return pages, toc_idx


def main():
    if not Path(CHROME).exists():
        sys.exit(f"No encuentro Chrome en {CHROME}. Define CHROME con la ruta de tu navegador.")
    src, items = prepare(SRC.read_text(encoding="utf-8"))
    DOCS.mkdir(parents=True, exist_ok=True)

    def build(pages):
        OUT_HTML.write_text(src.replace("{{TOC}}", toc_html(items, pages)), encoding="utf-8")
        render()
        return pages_text()

    texts = build({})
    n1 = len(texts)
    pages, toc_idx = locate(items, texts)
    texts = build(pages)
    n2 = len(texts)
    pages2, _ = locate(items, texts)
    stable = pages == pages2
    print(f"pasada 1: {n1} páginas · pasada 2: {n2} páginas · índice en la página {toc_idx + 1} · estable: {stable}")
    if not stable:
        texts = build(pages2)
        pages3, _ = locate(items, texts)
        stable = pages2 == pages3
        print(f"pasada 3 estable: {stable}")
    if not stable:
        sys.exit("El índice no se estabilizó: revisa los saltos de página.")
    print(f"Escrito {OUT_HTML.relative_to(ROOT)} y {OUT_PDF.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
