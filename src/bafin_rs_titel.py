#!/usr/bin/env python3
"""bafin_rs_titel.py (19.10.2026): 76 старых документов BaFin (bafin_rs_*, bafin_dl_*) с заголовком просто «Rundschreiben» и без адреса.
Заголовок и адрес берутся из начала текста (строка «Rundschreiben NN/JJJJ (XX) - …» и «Quelle: https://www.bafin.de/…»).
Сверка с новым сбором bafin_web/bafin_pdf (18.10) по адресу: совпадение -> отмечается metadata.doppelt_mit (удаление -- отдельно).
Меняются только documents.title, url, metadata; заголовок-фрагмент (document_titles) со своим вектором -- при пересчёте заголовков (решение Сергея).
    python3 bafin_rs_titel.py [--schreiben]   -> список: quellen_neu/bafin_rs_titel.jsonl
"""
import os
import json
import re
import sys

import psycopg2
from psycopg2.extras import Json

con = psycopg2.connect(host=os.environ.get("PGHOST", "localhost"), user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), dbname="steuer_corpus_test")
cur = con.cursor()
cur.execute("SELECT url, doc_id FROM documents WHERE source='bafin' AND url ~ '^http'")
url_neu = {}
for u, d in cur.fetchall():
    url_neu.setdefault(re.sub(r"[?#].*$", "", u).rstrip("/"), []).append(d)
cur.execute("SELECT id, doc_id, left(full_text, 1500) FROM documents WHERE source='bafin' AND title='Rundschreiben'")
out, n_doppelt = [], 0
for i, d, t in cur.fetchall():
    zeilen = [z.strip(" -|#\t") for z in t.split("\n")]
    titel = next((z for z in zeilen if z and z != "Rundschreiben" and not z.startswith("Quelle")), "")
    titel = re.sub(r"\s+", " ", titel)[:300]
    m = re.search(r"Quelle:\s*(https://www\.bafin\.de/\S+)", t)
    url = m.group(1) if m else None
    doppelt = url_neu.get(re.sub(r"[?#].*$", "", url).rstrip("/"), []) if url else []
    n_doppelt += bool(doppelt)
    out.append({"id": i, "doc_id": d, "title": titel, "url": url, "doppelt_mit": doppelt})
leer = [o for o in out if not o["title"] or not o["url"]]
print(f"документов {len(out)}; без заголовка/адреса {len(leer)}; совпадают по адресу с новым сбором {n_doppelt}")
for o in out[:6]:
    print("  ", o["doc_id"][:45], "|", o["title"][:90], "|", o["doppelt_mit"][:1])
json.dump(out, open("quellen_neu/bafin_rs_titel.jsonl", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
if "--schreiben" in sys.argv:
    for o in out:
        if o["title"] and o["url"]:
            cur.execute("UPDATE documents SET title=%s, url=%s, metadata = metadata || %s WHERE id=%s",
                        (o["title"], o["url"], Json({"titel_aus_text": "2026-10-19", **({"doppelt_mit": o["doppelt_mit"]} if o["doppelt_mit"] else {})}), o["id"]))
    con.commit()
    print("записано")
