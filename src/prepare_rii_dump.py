#!/usr/bin/env python3
"""
prepare_rii_dump.py -- решения федеральных судов из открытого набора elenanereiss/rechtsprechungen-dump (Hugging Face, CC0;
78 909 решений с rechtsprechung-im-internet.de на 30.07.2025; файл de_law_corpus/rii_dump/dump_30072025.jsonl)
-> de_law_corpus/prepared/bsg.jsonl (BSG целиком; прежняя тематическая выборка сохранена в bsg_thema_311.jsonl)
-> de_law_corpus/prepared/bgh.jsonl (BGH: IX и II гражданские сенаты целиком + решения прочих сенатов с налоговыми,
   бухгалтерскими и корпоративными нормами; новый источник bgh, уровень 1)
Решения, уже лежащие в базе (bsg, bgh_haftung, bgh_criminal), пропускаются по номеру дела и дате.
Только подготовка файлов; в базу не пишет. Загрузка: python3 -u load_new_sources.py load --sources bsg bgh
"""
import os
import collections
import json
import re
import shutil
from pathlib import Path

import psycopg2

SRC = Path("de_law_corpus/rii_dump/dump_30072025.jsonl")
PREP = Path("de_law_corpus/prepared")
THEMA = re.compile(r"StBerG|Steuerberat|\bAO\b|EStG|UStG|KStG|GewStG|\bHGB\b|GmbHG|ErbStG|GrEStG|Abgabenordnung|Steuerhinterzieh|§ 370\b|Insolvenzanfecht|"
                   r"Geschäftsführerhaftung|Bilanz|Buchführung|Sozialversicherungsbeitr|§ 266a|Wirtschaftsprüfer|Jahresabschluss|InsO")
NORM = re.compile(r"§ \d+[a-z]? (?:Abs\. \d+[a-z]? )?(?:SGB [IVX]+|StBerG|BGB|AO|EStG|UStG|KStG|GewStG|HGB|GmbHG|InsO|StGB|ZPO|SGG|AktG)")
az_key = lambda az, d: (re.sub(r"[^\w]+", "", az).lower(), d)      # noqa: E731


def body_of(r):
    teile = (("Leitsatz", r["leitsatz"]), ("Orientierungssatz", r["sonstosatz"]), ("Tenor", r["tenor"]), ("Tatbestand", r["tatbestand"]),
             ("Entscheidungsgründe", r["entscheidungsgruende"] or r["gruende"]), ("Abweichende Meinung", r["abwmeinung"]))
    return "\n\n".join(f"{k.upper()}:\n{v.strip()}" for k, v in teile if v and v.strip())


def main():
    cur = psycopg2.connect(dbname="steuer_corpus_test", user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), host=os.environ.get("PGHOST", "localhost")).cursor()
    cur.execute("SELECT title FROM documents WHERE source IN ('bsg', 'bgh_haftung', 'bgh_criminal')")
    have = set()
    for (t,) in cur.fetchall():
        m = re.match(r"(?:BSG|BGH)? ?(.+?) \((\d{4})-(\d\d)-(\d\d)\)", t or "")
        if m:
            have.add(az_key(m.group(1), m.group(2) + m.group(3) + m.group(4)))
    if (PREP / "bsg.jsonl").exists() and not (PREP / "bsg_thema_311.jsonl").exists():
        shutil.copy2(PREP / "bsg.jsonl", PREP / "bsg_thema_311.jsonl")
    out = {"bsg": open(PREP / "bsg.jsonl", "w", encoding="utf-8"), "bgh": open(PREP / "bgh.jsonl", "w", encoding="utf-8")}
    st, seen = collections.Counter(), set()
    for l in open(SRC, encoding="utf-8"):
        r = json.loads(l)
        g, az, d = r["gertyp"], " ".join((r["aktenzeichen"] or "").split()), r["entsch-datum"] or ""
        if g not in ("BSG", "BGH") or not az or len(d) != 8:
            continue
        body = body_of(r)
        if len(body) < 1500:
            st[f"{g}: короче 1500 знаков"] += 1
            continue
        if g == "BGH":
            sk = r["spruchkoerper"] or ""
            if not (sk.startswith(("9. Zivilsenat", "2. Zivilsenat")) or THEMA.search(r["norm"] or "")
                    or len(THEMA.findall((r["leitsatz"] or "") + " " + (r["titelzeile"] or "") + " " + body[:8000])) >= 3):
                st["BGH: вне отбора"] += 1
                continue
        k = az_key(az, d)
        if k in have or k in seen:
            st[f"{g}: уже в базе или повтор"] += 1
            continue
        seen.add(k)
        kind = g.lower()
        norms = [n.strip() for n in (r["norm"] or "").split(",") if n.strip()][:6] or sorted(set(NORM.findall(body)), key=body.count, reverse=True)[:5]
        gist = " ".join((r["leitsatz"] or r["titelzeile"] or r["sonstosatz"] or r["tatbestand"] or body)[:300].split())
        out[kind].write(json.dumps({"doc_id": f"{kind}_{d}_" + re.sub(r"[^\w]+", "_", az), "source": kind, "tier": 1,
                                    "title": f"{g} {az} ({d[:4]}-{d[4:6]}-{d[6:]}). {r['doktyp'] or ''}, {r['spruchkoerper'] or ''}. Normen: {', '.join(norms)}. {gist}",
                                    "text": body, "origin": r["identifier"] or "https://www.rechtsprechung-im-internet.de", "law_state_year": int(d[:4])}, ensure_ascii=False) + "\n")
        st[f"{g}: отобрано"] += 1
        st[f"{g}: знаков"] += len(body)
    for f in out.values():
        f.close()
    for k, v in sorted(st.items()):
        print(f"{k}: {v:,}")


if __name__ == "__main__":
    main()
