#!/usr/bin/env python3
"""load_struktur_fragmente.py (22.10.2026) — фрагменты нелегальных источников (struktur_verwaltung.py, struktur_gerichte.py) -> struktur_fragmente
(art = 'blatt', knoten = NULL — дерево узлов для них — следующий шаг). Старые строки этих документов заменяются; векторы — из кэша по тексту.
    python3 load_struktur_fragmente.py de_law_corpus/prepared/struktur_fragmente_<…>.jsonl
"""
import hashlib, io, json, re, sys
import psycopg2
sys.path.insert(0, '.')
from struktur_text import vorlauf_teilen
con = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost"); cur = con.cursor()
esc = lambda x: r"\N" if x is None else str(x).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " ")
buf, docs, n = io.StringIO(), set(), 0
ords = {}
for l in open(sys.argv[1], encoding="utf-8"):
    f = json.loads(l)
    vorlauf, t = vorlauf_teilen(f["text"].replace("­", ""))   # 24.10: вводная — в поле vorlauf
    d = f["document_id"]; docs.add(d); ords[d] = ords.get(d, 0) + 1
    leer = not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", t) or bool(re.fullmatch(r"\s*(\(\d+[a-z]?\)\s*)?([0-9a-z]+[.)]\s*)?\((weggefallen|aufgehoben)\)[.;,]?\s*", t))
    # 29.10: пустышки — только метки/цифры и «(weggefallen)»; прежнее «< 2 слов» писалось, когда вводная стояла в тексте, и гасило пункты «1. Altersvorsorgekonten;»
    buf.write("\t".join(esc(x) for x in (None, d, ords[d], f["pfad"], t, hashlib.sha256(t.encode()).hexdigest(), "struktur-2026-10-24v",   # 23.10: шаг 2 (новые нарезчики)
                                         
                                         "leer" if leer else "blatt", vorlauf)) + "\n")
    n += 1
cur.execute("DELETE FROM struktur_fragmente WHERE document_id = ANY(%s)", (list(docs),))
buf.seek(0)
cur.copy_expert("COPY struktur_fragmente (knoten, document_id, ord, pfad_text, text_roh, hash, version, art, vorlauf) FROM STDIN", buf)
con.commit()
print("LOAD", sys.argv[1], "документов", len(docs), "фрагментов", n, flush=True)
