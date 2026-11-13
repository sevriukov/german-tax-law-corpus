#!/usr/bin/env python3
"""load_struktur_laws.py (22.10.2026) — дерево законов (struktur_baum.py) -> таблицы struktur_knoten / struktur_fragmente.
Для узла: werk_id (норма «как таковая» поверх редакций, по образцу ELI/LegalDocML: gwg/p56/abs1/nr30), адрес цитирования
(«§ 56 Abs. 1 Nr. 30 GwG»), путь ltree, sha256 собственного текста, редакция и срок — по документу базы (valid_from / valid_until).
Только новые таблицы; старые не меняются. Векторы не считаются (embedding = NULL).
    python3 load_struktur_laws.py
"""
import hashlib
import io
import json
import re
from collections import defaultdict

import psycopg2

from struktur_text import vorlauf_teilen

VERSION = "struktur-2026-10-24v"   # 24.10: вводная — поле vorlauf
P = "de_law_corpus/prepared/"


def slug(s):
    s = (s or "").lower().replace("§", "p").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "x"


def teil(k):
    e, m = k["ebene"], (k["marke"] or "")
    if e == "Gesetz":
        return slug(re.sub(r" \d{4}$", "", m))
    if e == "Gliederung":
        return "gl_" + slug(m)
    if e == "Para":
        return slug(m.replace(" ", ""))            # «§ 56» -> p56, «Art 3» -> art3, «Anlage 1» -> anlage1
    pre = {"Abs": "abs", "Nr": "nr", "Buchst": "b", "Satzteil": "st", "Zeile": "z", "Schlusssatz": "schluss",
           "Block": "blk", "Satz": "s"}.get(e, slug(e))
    if e != "Schlusssatz" and not m.strip():          # 22.10 проверка: узел без номера -> номер по месту среди соседей (было «absx», 712 повторов)
        return f"{pre}_u{k.get('_rang', 0)}"
    return pre + slug(m if e != "Schlusssatz" else "")


def zitat_von(kette, abk):
    """«§ 56 Abs. 1 Nr. 30 GwG» aus der Kette Para -> … -> Knoten"""
    teile = []
    for k in kette:
        e, m = k["ebene"], (k["marke"] or "").strip()
        if e == "Para":
            teile.append(m)
        elif e == "Abs" and m:
            teile.append(f"Abs. {m}")
        elif e == "Nr" and m:
            teile.append(f"Nr. {m}")
        elif e == "Buchst" and m:
            teile.append(f"Buchst. {m}")
        elif e == "Satzteil":
            teile.append(f"Satzteil {m}")
        elif e == "Zeile":
            teile.append(f"Zeile {m}")
        elif e == "Schlusssatz":
            teile.append("Schlusssatz")
    return (" ".join(teile) + f" {abk}").strip() if teile else abk


def main():
    con = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost")
    cur = con.cursor()
    cur.execute("SELECT id, valid_from, valid_until FROM documents WHERE source = 'laws'")
    gilt = {i: (vf, vu) for i, vf, vu in cur.fetchall()}
    kn = {}
    for l in open(P + "struktur_knoten_laws.jsonl", encoding="utf-8"):
        k = json.loads(l)
        kn[k["id"]] = k
    # Kette bis zur Wurzel, Para-Vorfahr, Gesetzeskürzel
    werk, pfad, zit, para_doc = {}, {}, {}, {}
    gesehen = defaultdict(int)

    def kette(i):
        out = []
        while i:
            out.append(kn[i])
            i = kn[i]["parent"]
        return out[::-1]
    rang = defaultdict(int)
    for i in sorted(kn, key=lambda i: kn[i]["ord"]):   # место узла среди соседей того же уровня (для узлов без номера)
        k = kn[i]
        rang[(k["parent"], k["ebene"])] += 1
        k["_rang"] = rang[(k["parent"], k["ebene"])]
    for i in kn:
        ch = kette(i)
        abk = re.sub(r" \d{4}$", "", ch[0]["marke"] or "") if ch[0]["ebene"] == "Gesetz" else (ch[0]["marke"] or "").split(" §")[0]
        # 23.10: статья-раздел («Art 102c» с §§ внутри, EGInsO) — часть адреса нормы: eginso/art102c/p1 (раньше § 1 из Art 102/102b/102c сливались)
        art_gl = [k for k in ch if k["ebene"] == "Gliederung" and re.fullmatch(r"Art\.?\s*\d+[a-z]?", (k["marke"] or "").strip())]
        w = "/".join((slug((k["marke"] or "").replace(" ", "").replace(".", "")) if k in art_gl else teil(k)) for k in ch if k["ebene"] != "Gliederung" or k in art_gl)
        if ch[-1]["ebene"] != "Gliederung":
            w = re.sub(r"/(art\d+[a-z]?)(?=/\1(?:/|$))", "", w)   # 23.10 вечер: статья двумя уровнями Gliederung с одной меткой (FANG «Art 6»/«Art 6») — один сегмент
        gesehen[w] += 1
        if gesehen[w] > 1:                         # одинаковые номера внутри закона (EGBGB «§ 3» в разных Art.) — различитель
            w += f"_{gesehen[w]}"
        werk[i] = w
        pfad[i] = ".".join(re.sub(r"[^A-Za-z0-9_]", "_", teil(k))[:250] for k in ch) + (f"_{gesehen[w]}" if gesehen[w] > 1 else "")
        ps = [k for k in ch if k["ebene"] == "Para"]
        zit[i] = zitat_von(ch[ch.index(ps[0]):] if ps else ch[-1:], abk)
        if art_gl and ps and (ps[0]["marke"] or "").startswith("§"):     # «Art. 102c § 1 EGInsO»
            zit[i] = "Art. " + re.sub(r"^Art\.?\s*", "", art_gl[-1]["marke"].strip()) + " " + zit[i]
        para_doc[i] = (ps[0].get("document_ids") or [None])[0] if ps else (kn[i].get("document_ids") or [None])[0]
    buf = io.StringIO()

    def esc(x):
        if x is None:
            return r"\N"
        return str(x).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " ")
    for i, k in kn.items():
        d = para_doc.get(i)
        vf, vu = gilt.get(d, (None, None)) if d else (None, None)
        g = f"[{vf},{vu or ''})" if vf else None
        t = k.get("text") or ""
        buf.write("\t".join(esc(x) for x in (i, k["parent"], d, k["ebene"], k["marke"], k["ord"], werk[i], str(vf) if vf else None, g,
                                             zit[i], pfad[i], t, hashlib.sha256(t.encode()).hexdigest(),
                                             json.dumps({}), k.get("quelle", "xml_gii"), VERSION)) + "\n")
    cur.execute("DELETE FROM struktur_knoten")   # 23.10: не TRUNCATE — не ждать идущий снимок pg_dump
    buf.seek(0)
    cur.copy_expert("COPY struktur_knoten (id, parent, document_id, ebene, marke, ord, werk_id, fassung, gueltig, zitat, pfad, text_roh, "
                    "hash, eigenschaften, quelle, version) FROM STDIN", buf)
    print("узлов", len(kn), flush=True)
    buf = io.StringIO()
    n = 0
    for l in open(P + "struktur_fragmente_laws.jsonl", encoding="utf-8"):
        f = json.loads(l)
        vorlauf, t = vorlauf_teilen(f["text"])      # 24.10: вводная — в поле vorlauf, в тексте — только собственный текст
        leer = bool(re.fullmatch(r"\s*(\(\d+[a-z]?\)\s*)?(\d+[a-z]?\.\s*)?\((weggefallen|aufgehoben)\)\s*", t))
        # 23.10 проверка фундамента: почти пустые («[§ 12]», «[……]», «[]») — тоже без вектора
        leer = leer or len(re.sub(r"[^A-Za-zÄÖÜäöüß]", "", t)) < 3
        buf.write("\t".join(esc(x) for x in (f["knoten"], f["document_id"], kn.get(f["knoten"], {}).get("ord"), f["pfad"], t,
                                             hashlib.sha256(t.encode()).hexdigest(), None, VERSION, "leer" if leer else "blatt", vorlauf)) + "\n")
        n += 1
    # 23.10: в таблице уже все источники — заменять только листья законов (узел есть только у законов); целые §/Abs — struktur_innen_laws.py
    # 23.10 вечер: и прочие виды листьев (doppelt, blatt_geteilt, blatt_lang, части token_grenze) — иначе остаются от прошлого прогона
    cur.execute("DELETE FROM struktur_fragmente WHERE knoten IS NOT NULL AND art NOT IN ('para', 'abs', 'para_lang', 'abs_lang')")
    buf.seek(0)
    cur.copy_expert("COPY struktur_fragmente (knoten, document_id, ord, pfad_text, text_roh, hash, modell, version, art, vorlauf) FROM STDIN", buf)
    con.commit()
    print("фрагментов", n, flush=True)
    print("LOAD_STRUKTUR_LAWS_DONE", flush=True)


if __name__ == "__main__":
    main()
