#!/usr/bin/env python3
"""
prepare_bund_neu.py -- подготовка собранного collect_bund_neu.py к загрузке (04.10.2026):
  vwv   quellen_neu/vwv/*.htm  -> de_law_corpus/prepared/vwv_bund.jsonl  (Verwaltungsvorschriften des Bundes, уровень 1)
  bgbl  quellen_neu/bgbl/*.pdf -> de_law_corpus/prepared/bgbl.jsonl      (BGBl Teil I и II с 2023 г., уровень 1; дополняет источник bgbl)
"""
import html
import json
import re
import sys
from pathlib import Path

Q, PREP = Path("quellen_neu"), Path("de_law_corpus/prepared")


def text_of(raw):
    t = raw.decode("latin1")
    m = re.search(r'<div[^>]*id="container"[^>]*>(.*)', t, re.S)
    t = m.group(1) if m else t
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<(?:br|/p|/div|/tr|/h\d|/li)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"</t[dh]>", " | ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = "\n".join(" ".join(l.split()) for l in t.splitlines())
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"Zurück zur Teilliste[^\n]*\n?", "", t)
    t = re.sub(r"\n(zum Seitenanfang|Impressum|Datenschutz|Barrierefreiheitserklärung|Feedback-Formular)\b.*", "", t, flags=re.S)
    return t.strip()


def vwv():
    n = 0
    with (PREP / "vwv_bund.jsonl").open("w", encoding="utf-8") as fh:
        for line in (Q / "vwv" / "index.tsv").open(encoding="utf-8"):
            amt, title, f = line.rstrip("\n").split("\t")
            p = Q / "vwv" / f
            if not p.exists():
                continue
            txt = text_of(p.read_bytes())
            if not title:
                title = txt.split("\n")[0][:300]
            m = re.match(r"bsvwvbund_(\d{2})(\d{2})(\d{4})_", f)
            datum = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
            fh.write(json.dumps({"doc_id": "vwv_" + f[:-4], "source": "vwv_bund", "title": f"{title} ({amt}{', ' + datum if datum else ''})"[:480],
                                 "text": txt, "tier": 1, "origin": f"https://www.verwaltungsvorschriften-im-internet.de/{f}",
                                 "law_state_year": int(datum[:4]) if datum else None}, ensure_ascii=False) + "\n")
            n += 1
    print(f"vwv_bund.jsonl: {n} документов")


def bgbl():
    import pymupdf
    n = scan = 0
    out = PREP / "bgbl.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for p in sorted((Q / "bgbl").glob("*.pdf")):
            try:
                doc = pymupdf.open(p)
                txt = "\n".join(pg.get_text() for pg in doc)
            except Exception:
                continue
            txt = re.sub(r"[ \t]+\n", "\n", txt)
            txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
            if len(txt) < 200:
                scan += 1
                continue
            teil = "II" if p.name.startswith("II_") else "I"
            y, nr = re.search(r"(\d{4})_(\d{4}[a-z]?)", p.name).groups()
            head = " ".join(txt[:1500].split())
            m = re.search(r"Nr\. ?\d+[a-z]? (.{20,300}?) Vom \d{1,2}\. \w+ \d{4}", head)
            title = f"BGBl. {y} {teil} Nr. {int(re.sub(r'[^0-9]', '', nr))}{re.sub(r'[0-9]', '', nr)}: " + (m.group(1) if m else head[60:260])
            fh.write(json.dumps({"doc_id": f"bgbl_{teil}_{y}_{nr}", "source": "bgbl", "title": title[:480], "text": txt, "tier": 1,
                                 "origin": f"https://www.recht.bund.de/bgbl/{1 if teil == 'I' else 2}/{y}/{int(re.sub(r'[^0-9]', '', nr))}/VO.html",
                                 "law_state_year": int(y), "pages": len(doc)}, ensure_ascii=False) + "\n")
            n += 1
    print(f"bgbl.jsonl: {n} документов, без текстового слоя {scan}")


if __name__ == "__main__":
    {"vwv": vwv, "bgbl": bgbl}[sys.argv[1]]()
