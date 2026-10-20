#!/usr/bin/env python3
"""url_gesetze.py (19.10.2026, проверка качества B4): у ~14 тыс. параграфов законов (source=laws) адрес «N/A».
Адрес строится из официального перечня gesetze-im-internet.de (de_law_corpus/gii_aktuell/gii-toc.xml: полное название -> каталог закона)
по полному названию из doc_id «law_NNNNN__<название>_NNN_<§N|ArtN|…>»; если у другого параграфа того же закона адрес уже есть -- каталог берётся оттуда.
Параграф: «§ N» -> <каталог>/__N.html, «Art N» -> <каталог>/art_N.html (строчными: art_v.html), прочее (Inhaltsübersicht, Anlage) -> <каталог>/.
Меняется только documents.url (фрагменты и векторы не трогаются); список изменённых -- quellen_neu/url_gesetze_geaendert.jsonl.
    python3 url_gesetze.py            # пробный прогон: числа + выборка адресов для проверки
    python3 url_gesetze.py --pruefen  # + запрос HEAD к 30 случайным адресам (пауза 2 с; robots.txt сайта ничего не запрещает)
    python3 url_gesetze.py --schreiben
"""
import os
import html
import json
import random
import re
import sys
import time
import urllib.request

import psycopg2

BASE = "https://www.gesetze-im-internet.de/"
norm = lambda s: re.sub(r"[^0-9a-zäöüß]", "", s.lower())          # noqa: E731

toc = {}
for t, l in re.findall(r"<title>(.*?)</title>\s*<link>(.*?)</link>", open("de_law_corpus/gii_aktuell/gii-toc.xml", encoding="utf-8").read(), re.S):
    toc.setdefault(norm(html.unescape(t)), set()).add(l.split("gesetze-im-internet.de/")[1].split("/")[0])

con = psycopg2.connect(host=os.environ.get("PGHOST", "localhost"), user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), dbname="steuer_corpus_test")
cur = con.cursor()
cur.execute("SELECT id, doc_id, url FROM documents WHERE source='laws' AND doc_id ~ '^law_[0-9]+__'")
rows = cur.fetchall()
kat_vorh = {}
for _, d, u in rows:                                               # каталог из уже имеющихся адресов того же закона
    m = re.match(r"https?://www\.gesetze-im-internet\.de/([^/]+)/", u or "")
    if m:
        kat_vorh.setdefault(d.split("__")[0], set()).add(m.group(1))
toc_keys = sorted(toc)

stat = {"ohne_url": 0, "aus_geschwister": 0, "aus_toc_genau": 0, "aus_toc_praefix": 0, "mehrdeutig": 0, "nicht_gefunden": 0}
neu = []
for i, d, u in rows:
    if (u or "").startswith("http"):
        continue
    stat["ohne_url"] += 1
    m = re.match(r"^(law_\d+)__(.*)_\d{3}_([^_]*)$", d)
    if not m:
        stat["nicht_gefunden"] += 1
        continue
    key, titel, teil = m.groups()
    kat = None
    if len(kat_vorh.get(key, ())) == 1:
        kat = next(iter(kat_vorh[key])); stat["aus_geschwister"] += 1
    else:
        n = norm(titel)
        if n in toc and len(toc[n]) == 1:
            kat = next(iter(toc[n])); stat["aus_toc_genau"] += 1
        else:
            treffer = {k for t in toc_keys if len(n) >= 30 and t.startswith(n) for k in toc[t]}   # название в doc_id обрезано
            if len(treffer) == 1:
                kat = treffer.pop(); stat["aus_toc_praefix"] += 1
            elif treffer or (n in toc):
                stat["mehrdeutig"] += 1
            else:
                stat["nicht_gefunden"] += 1
    if not kat:
        continue
    p = re.match(r"^§(\w+)$", teil)
    a = re.match(r"^Art(\w+)$", teil)
    url = BASE + kat + "/" + (f"__{p.group(1).lower()}.html" if p else f"art_{a.group(1).lower()}.html" if a else "")
    neu.append((i, d, url))

print(json.dumps(stat, ensure_ascii=False), "адресов построено:", len(neu))
random.seed(19)
probe = random.sample(neu, min(30, len(neu)))
for _, d, u in probe[:8]:
    print("  ", d[:70], "->", u)
if "--pruefen" in sys.argv:
    ok = 0
    for _, d, u in probe:
        try:
            with urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": "LeckerMath-Forschung"}), timeout=30) as r:
                ok += r.status == 200
        except Exception as e:                                     # noqa: BLE001
            print("   [!]", u, str(e)[:50])
        time.sleep(2)
    print(f"проверка HEAD: {ok} из {len(probe)} адресов отвечают 200")
if "--schreiben" in sys.argv:
    with open("quellen_neu/url_gesetze_geaendert.jsonl", "w", encoding="utf-8") as f:
        for i, d, u in neu:
            f.write(json.dumps({"id": i, "doc_id": d, "url_alt": "N/A", "url": u}, ensure_ascii=False) + "\n")
    cur.executemany("UPDATE documents SET url=%s WHERE id=%s AND url !~ '^http'", [(u, i) for i, _, u in neu])
    con.commit()
    print("записано:", len(neu))
