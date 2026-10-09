#!/usr/bin/env python3
"""
prepare_ustae.py -- действующая сводная редакция UStAE (PDF с сайта BMF, «aktuelle Version») -> документы по разделам
(Abschnitt 19.1 и т.д.) -> de_law_corpus/prepared/ustae.jsonl (источник bmf_handbuecher, уровень 1).
Скачать заново: curl -L -o de_law_corpus/bmf_ustae/UStAE_aktuell.pdf \
  "https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Steuerarten/Umsatzsteuer/Umsatzsteuer-Anwendungserlass/Umsatzsteuer-Anwendungserlass-aktuell.pdf?__blob=publicationFile"
Загрузка: python3 load_new_sources.py load --sources ustae
"""
import json
import re

import pymupdf

SRC = "de_law_corpus/bmf_ustae/UStAE_aktuell.pdf"
OUT = "de_law_corpus/prepared/ustae.jsonl"


def main():
    doc = pymupdf.open(SRC)
    full = "\n".join(p.get_text() for p in doc).replace("\xa0", " ").replace("\xad", "")
    stand = (re.search(r"Stand (\d{1,2}\. \w+ \d{4})", full) or [None, "?"])[1]
    full = re.sub(r"(?m)^\s*Seite \d+\s*$\n?", "", full)
    heads = [m for m in re.finditer(r"(?m)^(\d{1,2}[a-z]?\.\d{1,3}[a-z]?(?:\.\d{1,3}[a-z]?)?)\. +([A-ZÄÖÜ][^\n]{2,120})$", full) if "...." not in m.group(2)]
    last = {}
    for m in heads:                       # оглавление идёт первым -- телу принадлежит ПОСЛЕДНЕЕ вхождение номера
        last[m.group(1)] = m
    body = sorted(last.values(), key=lambda m: m.start())
    n = 0
    with open(OUT, "w", encoding="utf-8") as out:
        for i, m in enumerate(body):
            text = full[m.start():body[i + 1].start() if i + 1 < len(body) else len(full)].strip()
            if len(text) < 300:
                continue
            title_line = " ".join((m.group(2) + " " + full[m.end():m.end() + 160].split("\n\n")[0]).split())
            title_line = re.split(r"\s\(1\)|\s1[A-ZÄÖÜ]", title_line)[0][:150].strip()
            par = re.match(r"\d+[a-z]?", m.group(1)).group(0)
            out.write(json.dumps({"doc_id": f"ustae_{stand.split()[-1]}_{m.group(1).replace('.', '_')}", "source": "bmf_handbuecher",
                                  "title": f"UStAE (Stand {stand}) Abschnitt {m.group(1)} — {title_line} (zu § {par} UStG)",
                                  "text": text, "tier": 1, "origin": SRC, "law_state_year": int(stand.split()[-1])}, ensure_ascii=False) + "\n")
            n += 1
    print(f"UStAE, Stand {stand}: разделов {n} из {len(body)} найденных заголовков -> {OUT}")


if __name__ == "__main__":
    main()
