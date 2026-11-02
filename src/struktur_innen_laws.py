#!/usr/bin/env python3
"""struktur_innen_laws.py (22.10.2026) — второй уровень векторов законов (проба: вариант D лучший — MRR 0,917, реранкер 97,8 %):
целые § и абзацы с перечнями — фрагменты art = 'para' / 'abs' в struktur_fragmente (путь + полный текст поддерева)."""
import hashlib, io, json
from collections import defaultdict
import psycopg2
kn = {}
for l in open("quellen_neu/vorlauf_fix/prepared/struktur_knoten_laws.jsonl", encoding="utf-8"):
    k = json.loads(l); kn[k["id"]] = k
kids = defaultdict(list)
for k in kn.values():
    if k["parent"]:
        kids[k["parent"]].append(k["id"])
for v in kids.values():
    v.sort(key=lambda i: kn[i]["ord"])
def voll(i):
    return " ".join(x for x in [kn[i]["text"]] + [voll(c) for c in kids[i]] if x)
def kopf(i):
    out, x = [], kn[i]
    while x["parent"]:
        x = kn[x["parent"]]
        # 23.10: корень — полное название закона + сокращение (как в листьях struktur_baum.py)
        out.append(f"{x['marke']} {x['text']}".strip() if x["ebene"] in ("Gliederung", "Para") else
                   (f"{x['text']} ({x['marke']})" if x["ebene"] == "Gesetz" and x["text"] else x["marke"]))
    return " › ".join(reversed(out))
esc = lambda x: r"\N" if x is None else str(x).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ")
buf, n = io.StringIO(), 0
for pid, p in kn.items():
    if p["ebene"] != "Para" or not p.get("document_ids") or not kids[pid]:
        continue
    pk = (kopf(pid) + " › " if p["parent"] else "") + f"{p['marke']} {p['text']}".strip()
    einheiten = [("para", pid, pk, voll(pid))] + [("abs", c, f"{pk} › Abs {kn[c]['marke']}".rstrip(), voll(c)) for c in kids[pid]
                                                 if kn[c]["ebene"] == "Abs" and kids[c]]
    einheiten = einheiten[:1] + [e for e in einheiten[1:] if e[3] != einheiten[0][3]]   # 23.10: единственный абзац = целый § — один раз
    for art, kid, pf, t in einheiten:
        if len(t) > 25000:          # 22.10 проверка: длиннее предела модели (8 192 токена) -> без вектора целого узла; полноту дают его абзацы/листья
            art = art + "_lang"
        for d in p["document_ids"]:
            buf.write("\t".join(esc(x) for x in (kid, d, kn[kid]["ord"], pf, t, hashlib.sha256(t.encode()).hexdigest(), "struktur-2026-10-28v", art)) + "\n")
            n += 1
con = psycopg2.connect(dbname="steuer_staging", user="postgres", password="postgres", host="localhost"); cur = con.cursor()
cur.execute("DELETE FROM struktur_fragmente WHERE art IN ('para', 'abs', 'para_lang', 'abs_lang')")   # 23.10: и длинные — иначе старые остаются
buf.seek(0)
cur.copy_expert("COPY struktur_fragmente (knoten, document_id, ord, pfad_text, text_roh, hash, version, art) FROM STDIN", buf)
con.commit()
print("INNEN", n, flush=True)
