#!/usr/bin/env python3
"""token_grenze.py (23.10.2026) — последний проход нарезки по ПРЕДЕЛУ МОДЕЛИ В ТОКЕНАХ (коллеги 23.10, единогласно: bge-m3 — 8 192 токена,
24 000 знаков — не предел; проверка quellen_neu/pruefung4/d_token.txt: 610 чанков длиннее). Текст вектора = путь + текст + ссылки.
  - целый § / абзац (para / abs) длиннее предела -> para_lang / abs_lang: без вектора, его листья покрывают текст;
  - лист длиннее предела -> следующий уровень структуры (struktur_laws.fein: разделы, иначе предложения); строки таблицы « | » —
    каждая строка своим листом с шапкой таблицы; исходный лист остаётся в таблице как blatt_geteilt (без вектора);
  - часть, которую делить больше не по чему, — blatt_lang (без вектора, остаётся для полнотекстового поиска).
Счёт по токенизатору BAAI/bge-m3. Запас: 7 800 токенов.
    python3 token_grenze.py [--probe]
"""
import hashlib
import re
import sys

import psycopg2
from psycopg2.extras import execute_values
from transformers import AutoTokenizer

from struktur_laws import SATZ
from struktur_text import VORLAUF, vorlauf_teilen


GRENZE = 7800
probe = "--probe" in sys.argv
tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")


def n_tok(t):
    return len(tok(t, add_special_tokens=True)["input_ids"])


# уровни структуры внутри длинного листа (текст склеен в строку): разделы/заголовки -> строки таблицы и пункты списка -> предложения -> «;»
EBENEN = [
    ("Teil", re.compile(r"\s(?<!vgl\.\s)(?<!Tz\.\s)(?<!Tzn\.\s)(?<!Nr\.\s)(?<!Nrn\.\s)(?<!Art\.\s)(?<!Rz\.\s)(?<!Rn\.\s)(?<!Abs\.\s)(?<!Ziff\.\s)(?<!Abschn\.\s)(?<!Kap\.\s)(?<!Anh\.\s)(?<!\bund\s)(?<!\bbis\s)(?<!\boder\s)(?<!\bsowie\s)(?<!\bgemäß\s)(?<!\bnach\s)(?<!\bsiehe\s)(?<!Ziffer\s)(?<!Nummer\s)(?<!Abschnitt\s)(?<!Kapitel\s)(?<!Randziffer\s)(?<!Textziffer\s)(?=(?:Abschnitt|Teil|Kapitel|Titel|Anlage|Anhang|Zeitrahmen|Lernfeld|Unterabschnitt)\s+[0-9IVX]+[a-z]?\b|\d{1,2}(?:\.\d{1,2})+\.?\s+[A-ZÄÖÜ])")),
    ("Zeilen", re.compile(r"(?<=\|)\s+(?=\|)|\s(?=\*\s{2,}\S)|\s(?=(?:Lfd\.\s?Nr\.|lfd\.\s?Nr\.)\s)")),
    ("Sätze", SATZ),                                          # общее правило предложений (сокращения BStBl., ABl., Az. …)
    ("Teilsätze", re.compile(r"(?<=;)\s+")),
]


def zerlegen(pfad, text, verweise, stufe=0):
    """-> [(метка, текст, вид)]: на первом уровне, где лист делится, соседние части собираются в куски в пределе
    (целые разделы / строки / предложения — посреди единицы не режется); часть, всё ещё длиннее, — следующий уровень; None — делить не по чему"""
    for k in range(stufe, len(EBENEN)):
        name, rx = EBENEN[k]
        stuecke = [x for x in rx.split(text) if x and x.strip()]
        if len(stuecke) < 2:
            continue
        rahmen = f"{pfad}\n\n{verweise or ''}"
        out, cur, cur_n, nr = [], [], 0, 0
        kopf = stuecke[0].strip() if name == "Zeilen" else ""   # шапка таблицы — в каждую следующую часть

        def ablegen():
            nonlocal nr
            if cur:
                nr += 1
                out.append((f"{name} {nr}", (kopf + VORLAUF if kopf and nr > 1 else "") + " ".join(cur)))
        for x in stuecke:
            n = n_tok(x)
            if cur and cur_n + n + n_tok(rahmen) > GRENZE:
                ablegen(); cur, cur_n = [], 0
            cur.append(x.strip()); cur_n += n
        ablegen()
        res = []
        for l, t in out:
            if n_tok(f"{pfad} › {l}\n{t}\n{verweise or ''}") > GRENZE:
                tiefer = zerlegen(f"{pfad} › {l}", t, verweise, k + 1)
                res += [(f"{l} {l2}", t2, a2) for l2, t2, a2 in tiefer] if tiefer else [(l, t, "blatt_lang")]
            else:
                res.append((l, t, "blatt" if re.search(r"[A-Za-zÄÖÜäöüß]{3,}", t) else "leer"))
        return res
    return None


con = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost")
cur = con.cursor()
cur.execute("""SELECT id, knoten, document_id, ord, pfad_text, text_roh, verweise, art FROM struktur_fragmente
               WHERE art IN ('blatt','abs','para') AND length(pfad_text) + length(text_roh) + coalesce(length(verweise), 0) > 10000""")
rows = cur.fetchall()
neu, upd, stat = [], [], {"geprueft": len(rows)}
for fid, kn, doc, ord_, pfad, text, verw, art in rows:
    if n_tok(f"{pfad}\n{text}\n{verw or ''}") <= GRENZE:
        continue
    stat[f"ueber_{art}"] = stat.get(f"ueber_{art}", 0) + 1
    if art in ("para", "abs"):
        upd.append((art + "_lang", fid))
        continue
    teile = zerlegen(pfad, text, verw)
    if teile is None:
        upd.append(("blatt_lang", fid))
        stat["blatt_lang"] = stat.get("blatt_lang", 0) + 1
        continue
    upd.append(("blatt_geteilt", fid))
    for l, t, a in teile:
        vl, t = vorlauf_teilen(t)                   # 24.10: шапка таблицы — в vorlauf
        neu.append((kn, doc, ord_, f"{pfad} › {l}" if l else pfad, t, hashlib.sha256(t.encode()).hexdigest(), "token-2026-10-24v", a, verw, vl))
        stat[f"teil_{a}"] = stat.get(f"teil_{a}", 0) + 1
print("TOKEN_GRENZE", stat, flush=True)
if probe:
    for x in neu[:12]:
        print(f"  [{x[3][-70:]}] {x[4][:120]}")
    sys.exit(0)
cur.executemany("UPDATE struktur_fragmente SET art = %s, embedding = NULL WHERE id = %s", upd)
execute_values(cur, "INSERT INTO struktur_fragmente (knoten, document_id, ord, pfad_text, text_roh, hash, version, art, verweise, vorlauf) VALUES %s", neu, page_size=5000)
con.commit()
print("TOKEN_GRENZE_DONE: изменено", len(upd), "новых частей", len(neu), flush=True)
