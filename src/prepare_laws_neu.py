#!/usr/bin/env python3
"""
prepare_laws_neu.py -- сверка действующих текстов законов в базе (источник laws, взят из стороннего набора harshildarji/bundesrecht)
с официальным XML gesetze-im-internet.de (de_law_corpus/gii_aktuell/*.zip) и подготовка исправленных текстов.

Причина (08.10.2026): в стороннем наборе абзац разложен на «текст абзаца» и отдельные массивы перечней, порядок между ними потерян;
наш сборщик текста (normalize_v2.extract_law_text) выводил сначала текст, потом перечни. Официальный XML хранит порядок документа.

    python3 prepare_laws_neu.py            # только сверка -> benchmark/leckermath/LAWS_SVERKA.md и de_law_corpus/prepared/laws_neu.jsonl
Классы: «совпадает» -- те же слова в том же порядке; «порядок» -- те же слова, порядок другой; «текст» -- слова различаются
(норма изменилась после снимка набора, либо в наборе потерян или лишний текст); «нет в базе» -- единица есть в XML, но не в базе;
«нет в XML» -- есть в базе, но не в официальном тексте (отменена или иначе названа).
В базу НЕ пишет: загрузка -- load_laws_neu.py.
"""
import os
import collections
import difflib
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import psycopg2

SRC = Path("de_law_corpus/gii_aktuell")
OUT = Path("de_law_corpus/prepared/laws_neu.jsonl")
BREAK = ("P", "LA", "DT", "DD", "entry", "Title", "BR", "row", "tgroup")
BLOCK = ("P", "DL", "DD", "LA", "table", "Title", "Revision")


def text_of(el):
    """Текст в порядке документа; элементы списков и абзацы -- с новой строки; номер предложения (SUP) отделяется пробелом."""
    parts = []

    def walk(node):
        if node.tag in BREAK:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for ch in node:
            walk(ch)
            if ch.tag == "SUP" and re.fullmatch(r"\s*\d{1,2}\s*", "".join(ch.itertext()) or ""):
                parts.append(" ")
            if ch.tail:
                if ch.tag in BLOCK and ch.tail.strip():           # текст после списка или абзаца («Schlusssatz») -- с новой строки, иначе слова склеиваются
                    parts.append("\n")
                parts.append(ch.tail)

    walk(el)
    t = "".join(parts)
    t = "\n".join(" ".join(l.split()) for l in t.splitlines())
    return re.sub(r"\n{2,}", "\n", t).strip()


def enbez_norm(e):
    e = " ".join((e or "").split())
    e = re.sub(r"^Art\.? ?(?=\d)", "Art ", e)
    e = re.sub(r"^Artikel ?(?=\d)", "Art ", e)
    e = re.sub(r"^(§+) ?(?=\d)", r"\1 ", e)
    return e


def parse_zip(path):
    """-> [(jurabk, enbez, titel, text)] по всем единицам закона с текстом"""
    out = []
    try:
        z = zipfile.ZipFile(path)
        root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".xml"))))
    except Exception:
        return out
    abk0 = None
    for n in root.iter("norm"):
        md = n.find("metadaten")
        if md is None:
            continue
        abk = (md.findtext("jurabk") or md.findtext("amtabk") or "").strip()
        abk0 = abk0 or abk
        enbez = enbez_norm(md.findtext("enbez"))
        c = n.find("textdaten/text/Content")
        if not enbez or c is None:
            continue
        txt = text_of(c)
        if len(txt) < 30:
            continue
        tt = md.find("titel")
        out.append((abk or abk0, enbez, " ".join("".join(tt.itertext()).split()) if tt is not None else "", txt))
    return out


def key_of_title(title):
    """«UStG 1980 § 15 - Vorsteuerabzug» -> ('UStG 1980', '§ 15'); «AOEG 1977 Art 97 § 1 …» -> берём первую единицу."""
    head = title.split(" - ")[0].strip()
    m = re.match(r"^(.*?) ((?:§+|Art) \d+[a-zA-Z]*)$", head)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(\S+(?: \d{4}(?:-\d\d)?)?) (.+)$", head)
    return (m.group(1), m.group(2)) if m else (head, "")


toks = lambda t: re.findall(r"[a-zäöüß]{2,}", t.lower())


def main():
    cur = psycopg2.connect(dbname="steuer_corpus_test", user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), host=os.environ.get("PGHOST", "localhost")).cursor()
    cur.execute("SELECT id, title, full_text FROM documents WHERE source = 'laws'")
    db = {}
    dup_db = 0
    for i, title, text in cur.fetchall():
        k = key_of_title(title)
        if k in db:
            dup_db += 1
        else:
            db[k] = (i, title, text)
    stat, by_law, rows, seen = collections.Counter(), collections.defaultdict(collections.Counter), [], set()
    zips = sorted(SRC.glob("*.zip"))
    for f in zips:
        for abk, enbez, titel, txt in parse_zip(f):
            k = (abk, enbez)
            if k in seen:
                stat["повтор ключа в XML"] += 1
                continue
            seen.add(k)
            new_title = f"{abk} {enbez}" + (f" - {titel}" if titel else "")
            if k not in db:
                cls = "нет в базе"
                rows.append({"cls": cls, "id": None, "title": new_title, "text": txt, "slug": f.stem})
            else:
                i, title, old = db[k]
                a, b = toks(old), toks(txt)
                ca, cb = collections.Counter(a), collections.Counter(b)
                miss = sum(((ca - cb) + (cb - ca)).values()) / max(len(a), len(b), 1)
                if miss > 0.03:
                    cls = "текст"
                elif len(a) > 20000 or difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.985:
                    cls = "совпадает"
                else:
                    cls = "порядок"
                rows.append({"cls": cls, "id": i, "title": title, "text": txt, "slug": f.stem, "alt_len": len(old), "miss": round(miss, 3)})
            stat[cls] += 1
            by_law[abk][cls] += 1
    lost = [k for k in db if k not in seen]
    slugs = {f.stem for f in zips}
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    L = [f"# Сверка законов в базе с официальным XML ({time.strftime('%d.%m.%Y %H:%M')})", "",
         f"Законов (архивов XML): {len(zips)}; единиц в XML: {len(seen)}; единиц в базе: {len(db)} (повторов ключа в базе {dup_db}).", "",
         "| Класс | Единиц | Что это |", "|---|---:|---|",
         f"| совпадает | {stat['совпадает']} | те же слова в том же порядке |",
         f"| порядок | {stat['порядок']} | те же слова, порядок другой — дефект набора |",
         f"| текст | {stat['текст']} | слова различаются: норма изменилась либо в наборе потеря или лишнее |",
         f"| нет в базе | {stat['нет в базе']} | есть в официальном тексте, в базе нет |",
         f"| нет в XML | {len(lost)} | есть в базе, в официальном тексте не найдено |", ""]
    kern = ["EStG", "UStG 1980", "AO 1977", "KStG 1977", "GewStG", "HGB", "ErbStG 1974", "BewG", "SGB 5", "SGB 4", "EStDV 1955", "UStDV 1980", "GmbHG", "StBerG", "GwG 2017"]
    L += ["| Закон | совпадает | порядок | текст | нет в базе |", "|---|---:|---:|---:|---:|"]
    for a in kern:
        c = by_law.get(a, {})
        L.append(f"| {a} | {c.get('совпадает', 0)} | {c.get('порядок', 0)} | {c.get('текст', 0)} | {c.get('нет в базе', 0)} |")
    L += ["", "Примеры «нет в XML»: " + "; ".join(f"{a} {e}" for a, e in lost[:25])]
    Path("benchmark/leckermath/LAWS_SVERKA.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
