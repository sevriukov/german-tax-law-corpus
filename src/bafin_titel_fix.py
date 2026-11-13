#!/usr/bin/env python3
"""bafin_titel_fix.py (19.10.2026): заголовки документов BaFin.
Дефект сбора 18.10: у всех 177 страниц bafin_web (179 с частями) заголовок «BaFin: Hinweis zur Verwendung von Cookies» (из баннера),
он же -- в заголовке-фрагменте (document_titles) и в паре для реранкера. У 76 старых документов (bafin_rs_*, bafin_dl_*) заголовок
просто «Rundschreiben» и нет адреса; 70 из них -- те же страницы, что в bafin_web (совпадение адреса, тексты той же длины).
  text  (процессор): прежние строки -> quellen_neu/bafin_titel_backup.jsonl; 70 старых дублей удаляются; у 6 остальных -- заголовок
        и адрес из начала текста; у bafin_web -- заголовок из первой строки текста (подстраница с общим заголовком -- + раздел из
        навигационной цепочки); части «— Teil n» сохраняют номер. documents.title и document_titles.title_text.
  emb   (видеокарты, только под сторожем): вектор заголовка-фрагмента для изменённых документов (quellen_neu/bafin_titel_ids.json).
    python3 bafin_titel_fix.py text | emb
"""
import json
import re
import sys

import psycopg2
from psycopg2.extras import Json

con = psycopg2.connect(host="localhost", user="postgres", dbname="steuer_corpus_test")
cur = con.cursor()
IDS = "quellen_neu/bafin_titel_ids.json"
sp = lambda s: re.sub(r"\s+", " ", re.sub(r"\(\s+(\w+)\s+\)", r"(\1)", s)).strip()        # noqa: E731
NAV = {"Unternehmen & Märkte", "Recht & Regelungen", "Verwaltungspraxis", "Rundschreiben", "Verbraucher", "Aufsicht", "Themen"}


def web_titel(t):
    z = [sp(x) for x in t.split("\n") if x.strip()]
    titel = z[0]
    try:
        i = z.index("StartseiteUeberschrift")
        j = next(k for k in range(i + 1, len(z)) if z[k] == titel or z[k].startswith(titel[:30]))
        bc = [x for x in z[i + 1:j] if x not in NAV]
    except (ValueError, StopIteration):
        bc = []
    if not re.search(r"Rundschreiben|Merkblatt|Leitfaden|Auslegungs|Verordnung|Gesetz", titel) and bc:
        titel = f"{titel} ({bc[-1]})"
    return titel[:300]


def text():
    bak = open("quellen_neu/bafin_titel_backup.jsonl", "w", encoding="utf-8")
    alt = json.load(open("quellen_neu/bafin_rs_titel.jsonl", encoding="utf-8"))      # из bafin_rs_titel.py
    ids, n_del = [], 0
    for o in alt:
        cur.execute("SELECT id, doc_id, source, title, url, full_text, metadata FROM documents WHERE id=%s", (o["id"],))
        r = cur.fetchone()
        if not r:
            continue
        bak.write(json.dumps(dict(zip(["id", "doc_id", "source", "title", "url", "full_text", "metadata"], r)), ensure_ascii=False, default=str) + "\n")
        if o["doppelt_mit"]:
            cur.execute("DELETE FROM documents WHERE id=%s", (o["id"],))
            n_del += 1
        else:
            cur.execute("UPDATE documents SET title=%s, url=%s, metadata = metadata || '{\"titel_aus_text\": \"2026-10-19\"}' WHERE id=%s",
                        (o["title"], o["url"], o["id"]))
            cur.execute("UPDATE document_titles SET title_text=%s WHERE document_id=%s AND kind='title'", (o["title"], o["id"]))
            ids.append(o["id"])
    cur.execute("SELECT id, doc_id, title, full_text FROM documents WHERE doc_id LIKE 'bafin_web%%' ORDER BY doc_id")
    rows = cur.fetchall()
    erste = {}
    for i, d, t, ft in rows:
        b = re.sub(r"_p\d{3}$", "", d)
        if d == b or d.endswith("_p001"):
            erste[b] = web_titel(ft)
    for i, d, t, ft in rows:
        b = re.sub(r"_p\d{3}$", "", d)
        m = re.search(r" — Teil \d+$", t)
        neu = erste[b] + (m.group(0) if m else "")
        cur.execute("SELECT title, url, metadata FROM documents WHERE id=%s", (i,))
        bak.write(json.dumps({"id": i, "doc_id": d, "title": t}, ensure_ascii=False) + "\n")
        cur.execute("UPDATE documents SET title=%s, metadata = metadata || '{\"titel_aus_text\": \"2026-10-19\"}' WHERE id=%s", (neu, i))
        cur.execute("UPDATE document_titles SET title_text=%s WHERE document_id=%s AND kind='title'", (neu, i))
        ids.append(i)
    con.commit()
    json.dump(ids, open(IDS, "w"))
    print(f"старых дублей удалено {n_del}; заголовков исправлено {len(ids)} (векторы заголовков -- шаг emb)", flush=True)


def emb():
    import os
    from emb_sicher import Pool
    ids = json.load(open(IDS))
    cur.execute("SELECT document_id, title_text FROM document_titles WHERE kind='title' AND document_id = ANY(%s)", (ids,))
    rows = cur.fetchall()
    pool = Pool(["cuda:0", "cuda:1"])
    e = pool.encode([t for _, t in rows], int(os.environ.get("EMB_BATCH", "64")))
    pool.close()
    for (i, _), v in zip(rows, e):
        cur.execute("UPDATE document_titles SET embedding=%s::vector WHERE document_id=%s AND kind='title'",
                    ("[" + ",".join(f"{x:.6f}" for x in v) + "]", i))
    con.commit()
    print(f"векторов заголовков пересчитано: {len(rows)}", flush=True)


if __name__ == "__main__":
    {"text": text, "emb": emb}[sys.argv[1]]()
