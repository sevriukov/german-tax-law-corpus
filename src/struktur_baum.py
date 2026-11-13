#!/usr/bin/env python3
"""struktur_baum.py (22.10.2026) — структура законов деревом-«матрёшкой» (решение владельца проекта 22.10: «новый семантический слой с помощью
баланса-матрёшки и взаимоотношений слоёв»). Закон -> Teil/Abschnitt/… (gliederungseinheit XML) -> § -> Abs. -> Nr. -> Buchst. -> Satz.
Каждый узел: собственный текст (у абзаца с перечнем — вводная фраза), родитель, уровень, метка, порядок. Фрагмент поиска = лист +
путь от корня (заголовки уровней и вводные фразы родителей). Счётчика знаков нет: длинный лист спускается на следующий уровень
структуры (struktur_laws.fein). Только чтение базы; результат — файлы (таблица struktur_knoten — отдельным шагом).

    python3 struktur_baum.py [--probe GwG,EStG,HGB,AO]
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import psycopg2

import prepare_laws_neu as P
from struktur_text import VORLAUF, trennung
from struktur_laws import GRENZE, dl_items, fein, flat, table_rows, text_einheiten  # noqa: F401

SRC = Path("quellen_roh/gii_aktuell/2026-10-08")
OUT = Path("de_law_corpus/prepared")
W = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
# 23.10 вечер: перенос строки в XML («Gruppen-<BR/> behandlung») — слово склеивается; «Kranken- und …» остаётся
TRENN = re.compile(r"(?<=[A-Za-zÄÖÜäöüß])-\s+(?!(?:und|oder|bzw|sowie|bis|als|noch|wie|u|o|beziehungsweise)\b)(?=[a-zäöüß])")


class Baum:
    def __init__(self, praefix):
        self.knoten, self.n, self.p = [], 0, praefix

    def neu(self, parent, ebene, marke, text="", **kw):
        self.n += 1
        k = {"id": f"{self.p}:{self.n}", "parent": parent, "ebene": ebene, "marke": marke, "ord": self.n,
             "text": trennung(text.replace("\u00ad", "")).strip(), **kw}   # 22.10 проверка: мягкий перенос рвёт слова для модели
        self.knoten.append(k)
        return k["id"]


def by_id(b, i):
    return next(k for k in reversed(b.knoten) if k["id"] == i)


def dd_baum(b, parent, dd):
    """пункт перечня: собственный текст (до первого вложенного перечня / таблицы) — текстом узла; вложенные перечни — буквы a), b) …,
    таблицы — строки, текст ПОСЛЕ них («… d) … oder einer vollziehbaren Anordnung … zuwiderhandelt») — узлом Schlusssatz, по порядку.
    23.10 вечер (сборка обратно без потерь): раньше разбирался только первый вложенный перечень, прочие перечни и таблицы пункта
    пропадали, а текст после перечня вставал перед подпунктами (TierGesG § 32, AgrarMSG § 20)"""
    teile, buf = [], []

    def gehen(el, wurzel=False):
        if not wurzel and el.tag in ("DL", "table"):
            teile.append(("text", " ".join("".join(buf).split()))); buf.clear()
            teile.append((el.tag, el))
            buf.append(el.tail or "")
            return
        buf.append(el.text or "")
        for ch in el:
            gehen(ch)
            if ch.tag not in ("DL", "table"):
                buf.append(ch.tail or "")
                if ch.tag == "SUP":
                    buf.append(" ")
    gehen(dd, True)
    teile.append(("text", " ".join("".join(buf).split())))
    if not any(a != "text" for a, _ in teile):
        return P.text_of(dd).replace("\n", " ")
    own = teile[0][1]
    for art, x in teile[1:]:
        if art == "DL":
            for n, t in dl_items(x):
                b.neu(parent, "Buchst", n.rstrip(".)"), (n + " " + t).strip())   # метка в тексте, как у Nr («1.1.3», «a)»): на неё ссылаются
        elif art == "table":
            for i, r in enumerate(table_rows(x), 1):
                b.neu(parent, "Zeile", str(i), r)
        elif x:
            b.neu(parent, "Schlusssatz", "", x)
    return own


def norm_baum(b, parent, content):
    """Content нормы -> узлы абзацев, пунктов, букв, строк таблиц"""
    for p in list(content) or [content]:
        if p.tag == "table":
            for i, r in enumerate(table_rows(p), 1):
                b.neu(parent, "Zeile", str(i), r)
            continue
        if p.tag == "DL":
            for (n, _), dd in zip(dl_items(p), [x for x in p if x.tag == "DD"]):
                nid = b.neu(parent, "Nr", n.rstrip(".)"))
                b.knoten[-1]["text"] = (n + " " + dd_baum(b, nid, dd)).strip()
            continue
        if p.tag != "P":
            if any(True for _ in p.iter("table")):
                for tb in p.iter("table"):
                    for i, r in enumerate(table_rows(tb), 1):
                        b.neu(parent, "Zeile", str(i), r)
            elif P.text_of(p).strip():
                b.neu(parent, "Block", p.tag, P.text_of(p).replace("\n", " "))
            continue
        m = re.match(r"\((\d+[a-z]?)\)", flat(p))
        dls = [x for x in p if x.tag == "DL"]
        tbs = list(p.iter("table"))
        if not dls and not tbs:
            b.neu(parent, "Abs", m.group(1) if m else "", P.text_of(p).replace("\n", " "))
            continue
        # абзац с перечнями: текст до первого перечня -- текст абзаца; у каждого следующего перечня своя вводная фраза (текст между
        # перечнями, напр. «Abweichend von Satz 1 … mit einer» в § 56 Abs. 3 GwG) -- промежуточный узел; хвост после последнего -- «Schlusssatz»
        segs, buf = [], [p.text or ""]
        for ch in p:
            if ch.tag in ("DL", "table"):
                segs.append((" ".join("".join(buf).split()), ch))
                buf = [ch.tail or ""]
                continue
            buf.append(" ".join("".join(ch.itertext()).split()) + " " + (ch.tail or ""))
        tail = " ".join("".join(buf).split())
        aid = b.neu(parent, "Abs", m.group(1) if m else "", segs[0][0] if segs else "")
        for j, (intro, el) in enumerate(segs):
            ziel = aid if j == 0 else (b.neu(aid, "Satzteil", str(j + 1), intro) if intro else aid)
            if el.tag == "table":
                for i, r in enumerate(table_rows(el), 1):
                    b.neu(ziel, "Zeile", str(i), r)
                continue
            for (n, _), dd in zip(dl_items(el), [x for x in el if x.tag == "DD"]):
                nid = b.neu(ziel, "Nr", n.rstrip(".)"))
                k = len(b.knoten) - 1
                b.knoten[k]["text"] = (n + " " + dd_baum(b, nid, dd)).strip()
        if tail:
            b.neu(aid, "Schlusssatz", "", tail)


def parse(path):
    """-> (jurabk, [узлы], {(jurabk, enbez): [(titel, id узла §)]})"""
    try:
        z = zipfile.ZipFile(path)
        root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".xml"))))
    except Exception:
        return None
    for br in root.iter("BR"):                     # 23.10 вечер: «können<BR/>diese» склеивалось в одно слово — разрыв строки = пробел
        br.tail = " " + (br.tail or "")
    b = Baum(path.stem)
    wurzel, glied, paras, abk0 = None, {}, {}, None
    cur_gl = None
    for n in root.iter("norm"):
        md = n.find("metadaten")
        if md is None:
            continue
        abk = (md.findtext("jurabk") or md.findtext("amtabk") or "").strip()
        abk0 = abk0 or abk
        if wurzel is None:
            wurzel = b.neu(None, "Gesetz", abk0, " ".join((md.findtext("langue") or "").split()))
        ge = md.find("gliederungseinheit")
        if ge is not None:
            kz = (ge.findtext("gliederungskennzahl") or "").strip()
            par = glied.get(kz[:-3], wurzel) if len(kz) > 3 else wurzel
            bez = " ".join((ge.findtext("gliederungsbez") or "").split())
            # 23.10 проверка фундамента (XML-сверка, EGInsO): в законах по статьям одна запись norm — и раздел («Art 102c»), и норма
            # с текстом («§ 1 Örtliche Zuständigkeit …», «Art 2 Es werden aufgehoben …»); раньше текст таких норм пропускался
            enbez_ge = P.enbez_norm(md.findtext("enbez"))
            c_ge = n.find("textdaten/text/Content")
            hat_text = c_ge is not None and len(re.findall(r"[A-Za-zÄÖÜäöüß]{2,}", "".join(c_ge.itertext()))) >= 2
            if bez in ("", "-") and enbez_ge and hat_text:          # сама статья — норма («Art 2»): § под разделом-родителем
                ziel = par
            else:
                if kz not in glied or (b.knoten and by_id(b, glied[kz])["marke"] != bez):
                    glied[kz] = b.neu(par, "Gliederung", bez, " ".join((ge.findtext("gliederungstitel") or "").split()))
                ziel = glied[kz]
                cur_gl = glied[kz]
            if enbez_ge and hat_text:
                tt = md.find("titel")
                titel = " ".join("".join(tt.itertext()).split()) if tt is not None else ""
                pid = b.neu(ziel, "Para", enbez_ge, titel)
                norm_baum(b, pid, c_ge)
                paras.setdefault((abk or abk0, enbez_ge), []).append((titel, pid))
            continue
        enbez = P.enbez_norm(md.findtext("enbez"))
        c = n.find("textdaten/text/Content")
        if not enbez or c is None:
            continue
        tt = md.find("titel")
        titel = " ".join("".join(tt.itertext()).split()) if tt is not None else ""
        pid = b.neu(cur_gl or wurzel, "Para", enbez, titel)
        norm_baum(b, pid, c)
        paras.setdefault((abk or abk0, enbez), []).append((titel, pid))
    return abk0, b.knoten, paras


def blaetter(knoten):
    """листья + путь: заголовки уровней и вводные фразы родителей; длинный лист -> следующий уровень (fein)"""
    by = {k["id"]: k for k in knoten}
    kids = Counter(k["parent"] for k in knoten if k["parent"])
    out = []
    for k in knoten:
        # 29.10: собственный текст узла с детьми (вводная абзаца перед перечнем: «(1) Auf Grund … verordnet …:») — своим куском с местом;
        # у пунктов он — вводная (vorlauf, реквизит уже встретившегося текста). Прежде слова вводной были только в vorlauf пунктов —
        # сверка с исходником: 35 749 законов «было чисто -> пробел»
        vorlauf_knoten = kids[k["id"]] and k["ebene"] not in ("Gesetz", "Gliederung", "Para") and re.search(r"[A-Za-zÄÖÜäöüß]{3,}", k["text"] or "")   # не одна метка «1.»
        if (kids[k["id"]] or k["ebene"] in ("Gesetz", "Gliederung", "Para") and not k.get("leer_para")) and not vorlauf_knoten:
            if not (k["ebene"] == "Para" and not kids[k["id"]]):
                continue
        kette, x = [], k
        while x["parent"]:
            x = by[x["parent"]]
            kette.append(x)
        kette.reverse()
        # 23.10 проверка фундамента: корень — полное название закона + сокращение (раньше — только сокращение, вектор без имени закона);
        # узел без номера («Abs » у единственного абзаца) — без метки
        pfad = " › ".join(x for x in (f"{a['marke']} {a['text']}".strip() if a["ebene"] in ("Gliederung", "Para") else
                                      (f"{a['ebene']} {a['marke']}".strip() if a["marke"] else "") if a["ebene"] != "Gesetz" else
                                      (f"{a['text']} ({a['marke']})" if a["text"] else a["marke"]) for a in kette) if x)
        einleitung = " … ".join(a["text"] for a in kette if a["ebene"] not in ("Gesetz", "Gliederung", "Para") and a["text"])
        eigen = f"{k['ebene']} {k['marke']}".strip() if k["ebene"] not in ("Para",) and k["marke"] else ""
        for l, t in fein([("", k["text"])]):
            out.append({"knoten": k["id"], "pfad": pfad + (f" › {eigen}" if eigen else "") + (f" {l}" if l else ""),
                        "text": (einleitung + VORLAUF if einleitung else "") + t})   # 24.10: вводная — отдельным полем
    return out


def main():
    probe = sys.argv[sys.argv.index("--probe") + 1].split(",") if "--probe" in sys.argv else None
    zips = sorted(SRC.glob("*.zip"))
    if probe:
        zips = [z for z in zips if z.stem.split("_")[0].lower() in {p.lower() for p in probe}]
    cur = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost").cursor()
    cur.execute("SELECT id, title, full_text, metadata->>'jurabk' FROM documents WHERE source = 'laws' AND valid_until IS NULL")
    db, jurabk_of = {}, {}
    for i, title, text, jab in cur.fetchall():
        db.setdefault(P.key_of_title(title), []).append((i, title, text))
        # 23.10: сокращение — из заголовка документа («SGB 5 § 140f …» -> «SGB 5»); в metadata.jurabk бывает общее («SGB», «GrÄndStVtr»)
        aus_titel = re.split(r" (?:§|Art\b|Artikel|Anlage|Anl\b|Anhang|Anh\b|Inhaltsverzeichnis|Inhaltsübersicht)", title.split(" - ")[0])[0].strip()
        jurabk_of[i] = aus_titel if aus_titel and (not jab or len(aus_titel) >= len(jab)) else (jab or aus_titel)
    alle_xml = []
    langue_of = {}                                        # 23.10: сокращение -> полное название (из XML), для запасного разбора
    text_of_doc = {i: (title, text) for v in db.values() for i, title, text in v}
    doc_frag = {}                                         # документ -> тексты его листьев (для проверки покрытия)
    st = Counter()
    sfx = "_probe" if probe else ""
    with Pool(24) as pool, open(OUT / f"struktur_knoten_laws{sfx}.jsonl", "w", encoding="utf-8") as fk, \
            open(OUT / f"struktur_fragmente_laws{sfx}.jsonl", "w", encoding="utf-8") as ff:
        for r in pool.imap_unordered(parse, zips, chunksize=10):
            if not r:
                continue
            abk, knoten, paras = r
            by = {k["id"]: k for k in knoten}
            for k in knoten:
                if k["ebene"] == "Gesetz" and k["text"]:
                    langue_of.setdefault(" ".join(k["marke"].split()), k["text"])
                    langue_of.setdefault(re.sub(r" \d{4}$", "", " ".join(k["marke"].split())), k["text"])   # «WHG 2009» -> и «WHG»
            # § -> документ базы (одинаковые номера внутри закона -- по заголовку)
            for key, lst in paras.items():
                for i, title, text in db.get(key, []):
                    dt = title.split(" - ", 1)[1].strip() if " - " in title else ""
                    pid = next((p for t, p in lst if t and t == dt), lst[0][1])
                    by[pid].setdefault("document_ids", []).append(i)
            alle_xml.extend(knoten)        # 23.10: узлы XML пишутся после проверки покрытия (документ запасного разбора отвязывается от § XML)
            bl = blaetter(knoten)
            # лист -> документ его §
            para_of = {}
            for k in knoten:
                x = k
                while x and x["ebene"] != "Para":
                    x = by.get(x["parent"]) if x["parent"] else None
                para_of[k["id"]] = x
            for f in bl:
                pk = para_of.get(f["knoten"])
                for d in (pk or {}).get("document_ids", []):
                    doc_frag.setdefault(d, []).append(f)
            st["gesetze"] += 1
            st["knoten"] += len(knoten)
            st["paras_mit_doc"] += sum(1 for k in knoten if k["ebene"] == "Para" and k.get("document_ids"))
        # покрытие: каждое слово текста базы -- в листьях своего §; иначе (и без XML) -- дерево по тексту базы
        ersetzt = set()
        langue_ohne = {re.sub(r"\s+", "", k): v for k, v in langue_of.items()}   # «BinSchUO2018» = «BinSchUO 2018»
        langue_ohne.update({re.sub(r"\d{4}$", "", k): v for k, v in langue_ohne.items() if re.search(r"\d{4}$", k)})
        if probe:
            text_of_doc = {d: text_of_doc[d] for d in doc_frag}
        for d, (title, text) in text_of_doc.items():
            frs = doc_frag.get(d, [])
            # 24.10: склейка переносов одинаково с обеих сторон (в тексте базы «Rest- nutzungs- dauer», в листьях XML «Restnutzungsdauer») —
            # иначе XML ошибочно считался беднее и таблица уходила в запасной разбор одним куском (BewG Anlage 37)
            wb = Counter(W.findall(trennung(text or "").lower()))
            fehl = sum((wb - Counter(W.findall(trennung(" ".join(f["text"] for f in frs)).lower()))).values()) if frs else None
            if fehl is None or fehl > 3:
                st["text_baum_" + ("ohne_xml" if fehl is None else "xml_beden")] += 1
                ersetzt.add(d)
                b = Baum(f"t{d}")
                kopf = title.split(" - ")[0]
                # 23.10: и у запасного разбора корень — закон (раньше корнем был §: 5 701 закон без узла «Gesetz»)
                jab = re.sub(r"\s*(\(XXXX\)|Schlu(ss|ß)formel|Eingangsformel|Inhaltsübersicht|Anlage.*|Anl\b.*|Anhang.*|Anh\b.*)\s*$", "", " ".join(jurabk_of.get(d, "").split())).strip()
                jab = re.sub(r"\s*\((\d{4}-\d\d-\d\d|XXXX)\).*$", "", jab).strip()          # «SprengV 2 (1977-11-23)» -> «SprengV 2»
                ohne = lambda x: re.sub(r"\s+", "", x)
                lang = (langue_of.get(jab) or langue_of.get(re.sub(r" \d{4}(-\d\d-\d\d)?(/\d+)?$", "", jab))
                        or langue_ohne.get(ohne(jab)) or langue_ohne.get(ohne(re.sub(r"\d{4}$", "", jab))) or "")
                gw = b.neu(None, "Gesetz", jab, lang, quelle="text_basis")
                pid = b.neu(gw, "Para", kopf[len(jab):].strip() or kopf, title.split(" - ", 1)[1] if " - " in title else "", document_ids=[d], quelle="text_basis")
                nodes = {}
                for l, t in text_einheiten(text):
                    teile = l.split(" ")
                    if l.startswith("Abs.") and " Nr." in l:      # «Abs. 1 Nr. 3» -> узел абзаца + пункт
                        a = " ".join(teile[:2])
                        # 23.10 повторная проверка: setdefault(a, b.neu(…)) создавал пустой узел на КАЖДОЙ строке (4 647 пустых листьев),
                        # и пункт висел на пустом узле без вводной («(2) Absatz 1 gilt nicht»); теперь — абзац с текстом вводной
                        if a not in nodes:
                            nodes[a] = b.neu(pid, "Abs", teile[1], t.split(VORLAUF, 1)[0] if VORLAUF in t else "")
                        elif not by_id(b, nodes[a])["text"] and VORLAUF in t:
                            by_id(b, nodes[a])["text"] = t.split(VORLAUF, 1)[0]
                        b.neu(nodes[a], "Nr", teile[-1], t.split(VORLAUF, 1)[-1])
                    else:
                        n_id = b.neu(pid, l.split(" ")[0].rstrip(".") if l else "Abs", l.split(" ", 1)[-1] if l else "", t)
                        if l.startswith("Abs.") and len(teile) == 2:   # абзац «(2) …» — к нему же пункты «Abs. 2 Nr. n»
                            nodes[l] = n_id
                for k in b.knoten:
                    fk.write(json.dumps(k, ensure_ascii=False) + "\n")
                frs = blaetter(b.knoten)
            st["docs"] += 1
            for f in frs:
                ff.write(json.dumps({"document_id": d, **f}, ensure_ascii=False) + "\n")
                st["fragmente"] += 1
            fehl2 = sum((wb - Counter(W.findall(trennung(" ".join(f["text"] for f in frs)).lower()))).values())
            st["verlust_docs"] += fehl2 > 3
            st["leer_docs"] += not frs
        for k in alle_xml:
            if k.get("document_ids") and ersetzt & set(k["document_ids"]):
                k["document_ids"] = [d for d in k["document_ids"] if d not in ersetzt]
                st["xml_para_entbunden"] += 1
            fk.write(json.dumps(k, ensure_ascii=False) + "\n")
    print(f"STRUKTUR_BAUM: {dict(st)}", flush=True)


if __name__ == "__main__":
    main()
