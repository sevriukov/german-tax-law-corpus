#!/usr/bin/env python3
"""prepare_rii_bverwg_bag.py (02.11.2026, план 1.1 Б2; копия prepare_rii_2026.py) -- решения BVerwG и BAG из ZIP, скачанных AGY (~/Documents/Anti_lauf/rii, очередь 1: BVerwG 8/9/10, BAG 3/5/9/10 -- отбор по теме = сенат)
-> quellen_neu/partie4/prepared/bverwg_rii.jsonl, bag_rii.jsonl (новые источники bverwg / bag, уровень 1). Gründe: у Urteil -- entscheidungsgruende, у Beschluss -- gruende (оба берутся).
Только подготовка файлов; в базу не пишет. Загрузка: python3 -u load_new_sources.py load --sources bfh_2026 bverfg_2026"""
import html
import json
import re
import zipfile
from pathlib import Path

Z = Path("~/Documents/Anti_lauf/rii")
PREP = Path("./quellen_neu/partie4/prepared")   # 02.11: de_law_corpus/prepared снят в архив на NAS 01.11
TEILE = (("leitsatz", "LEITSATZ"), ("sonstosatz", "ORIENTIERUNGSSATZ"), ("tenor", "TENOR"), ("tatbestand", "TATBESTAND"),
         ("entscheidungsgruende", "ENTSCHEIDUNGSGRÜNDE"), ("gruende", "GRÜNDE"), ("abwmeinung", "ABWEICHENDE MEINUNG"))


THEMA = re.compile(r"\b(?:\w*[Ss]teuer\w*|Abgaben?\w*|Gebühr\w*|Beitr(?:ag|äge)\w*|KAG|Kommunalabgaben\w*|Erschließungs\w*|Hebesatz\w*|Lohn\w*|"
                   r"Arbeitsentgelt\w*|Vergütung\w*|Sozialversicherung\w*|Altersversorgung\w*|BetrAVG|Scheinselbst\w*|Arbeitnehmerbegriff|"
                   r"Subvention\w*|Zuwendung\w*|Rundfunkbeitrag\w*|Kammerbeitrag\w*|EStG|AO|UStG|GewStG|GrStG|SGB IV)\b")


def feld(x, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", x, re.S)
    if not m:
        return ""
    t = re.sub(r"<br\s*/?>|</p>|</dd>|</div>", "\n", m.group(1))
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()


out = {"BVerwG": open(PREP / "bverwg_rii.jsonl", "w", encoding="utf-8"), "BAG": open(PREP / "bag_rii.jsonl", "w", encoding="utf-8")}
n = {"BVerwG": 0, "BAG": 0}
for f in sorted(Z.glob("*.zip")):
    with zipfile.ZipFile(f) as z:
        x = z.read([i for i in z.namelist() if i.endswith(".xml")][0]).decode("utf-8")
    g = feld(x, "gertyp")
    if g not in out:
        continue
    az, d = feld(x, "aktenzeichen"), feld(x, "entsch-datum")
    datum = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    text = "\n\n".join(f"{k}:\n{feld(x, t)}" for t, k in TEILE if feld(x, t))
    normen = feld(x, 'norm')
    titel = f"{g} {az} ({datum}). {feld(x, 'doktyp')}, {feld(x, 'spruchkoerper')}." + (f" Normen: {normen}." if normen else "") + f" {feld(x, 'titelzeile')}"
    # 02.11 (решение 1б: в базу -- только налоговое и смежное): отметка темы для отбора на шаге В1, сам отбор -- там
    treffer = sorted({w for w in THEMA.findall(text + " " + normen)})
    out[g].write(json.dumps({"doc_id": f"{g.lower()}_{d}_{re.sub(r'[^0-9A-Za-z]+', '_', az)}", "source": g.lower(), "tier": 1,
                             "title": " ".join(titel.split())[:500], "text": text, "origin": f"https://www.rechtsprechung-im-internet.de/jportal/?docid={feld(x, 'doknr')}",
                             "reference_number": az, "effective_date": datum, "law_state_year": int(d[:4]), "ecli": feld(x, "ecli"), "spruchkoerper": feld(x, "spruchkoerper"), "thema_treffer": treffer[:30], "thema_n": len(treffer)},
                            ensure_ascii=False) + "\n")
    n[g] += 1
print(n)
