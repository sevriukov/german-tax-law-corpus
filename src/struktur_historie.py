#!/usr/bin/env python3
"""struktur_historie.py (22.10.2026) — прежние редакции законов (laws_historie) по структуре текста (struktur_laws.text_einheiten + fein):
абзац «(n)», пункты с вводной фразой; путь = заголовок редакции. Покрытие слов проверяется. -> de_law_corpus/prepared/struktur_fragmente_laws_historie.jsonl"""
import json, os, re
from collections import Counter
from multiprocessing import Pool
import psycopg2
from struktur_laws import fein, text_einheiten
from struktur_text import VORLAUF, saeubern
W = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
def doc(row):
    i, title, text = row
    # 29.10: fein режет только собственный текст единицы; вводная (до VORLAUF) — полем у каждого куска. Прежде fein резал и вводную
    # (у «Nr. a» — весь предыдущий абзац): её слова попадали в текст кусков второй раз (51 документ, напр. RheinSchPV 1994 § 15: 510 кусков вместо 209)
    u = [(l2, (vor + VORLAUF if vor else "") + t2) for l, t in text_einheiten(saeubern(text))
         for vor, _, eigen in [t.rpartition(VORLAUF)] for l2, t2 in fein([(l, eigen)])] or [("", " ".join((text or "").split()))]   # 24.10: общая чистка
    frs = [{"document_id": i, "pfad": (title or "") + (f" › {l}" if l else ""), "text": t} for l, t in u]
    wb, wf = Counter(W.findall(text or "")), Counter(W.findall(" ".join(t for _, t in u)))
    return frs, sum((wb - wf).values())
cur = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost").cursor()
# 23.10 (владелец проекта): NUR_NICHT_BESTANDEN=1 — резать только документы, не прошедшие проверку (dok_pruefung.bestanden = false);
# 24.10: AUCH_IDS=1,2,3 — и эти (исходник заменён после проверки: старая отметка устарела)
cur.execute("SELECT id, title, full_text FROM documents WHERE (source IN ('laws_historie') OR (source = 'laws' AND valid_until IS NOT NULL))" + ((" AND id IN (" + ",".join(x.strip() for x in open(os.environ["NUR_IDS"]) if x.strip()) + ")") if os.environ.get("NUR_IDS") else (" AND (id NOT IN (SELECT document_id FROM dok_pruefung WHERE bestanden) OR id IN (" + os.environ.get("AUCH_IDS", "0") + "))" if os.environ.get("NUR_NICHT_BESTANDEN") else "")))
st = Counter()
with Pool(20) as p, open("de_law_corpus/prepared/struktur_fragmente_laws_historie.jsonl", "w", encoding="utf-8") as f:
    for frs, fehl in p.imap_unordered(doc, cur.fetchall(), chunksize=100):
        st["docs"] += 1; st["fragmente"] += len(frs); st["verlust_docs"] += fehl > 3
        for x in frs:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
print("STRUKTUR_HISTORIE", dict(st), flush=True)
