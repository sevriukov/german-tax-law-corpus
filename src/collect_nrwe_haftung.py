#!/usr/bin/env python3
"""
collect_nrwe_haftung.py -- решения судов земли Северный Рейн-Вестфалия об ответственности налоговых консультантов
с официальной базы NRWE (nrwesuche.justiz.nrw.de; тексты решений -- § 5 UrhG, обезличены судом).
Только скачивание в quellen_neu/haftung/nrwe/; в базу НЕ загружает (загрузка -- отдельно и вместе с Сергеем).

    python3 collect_nrwe_haftung.py            # поиск по списку запросов -> nrwe_treffer.json, затем тексты
"""
import json
import re
import time
from pathlib import Path

import requests

OUT = Path("quellen_neu/haftung")
UA = {"User-Agent": "Mozilla/5.0 (LeckerMath research; local legal database)"}
URL = "https://nrwesuche.justiz.nrw.de/index.php"
QUERIES = ["Steuerberaterhaftung", "Steuerberater Pflichtverletzung Schadensersatz", "Steuerberater Regress", "Steuerberater Beratungsfehler",
           "Steuerberatervertrag Schadensersatz", "Steuerberater Mitverschulden Mandant", "Steuerberater Sozialversicherung Schadensersatz",
           "Steuerberater Krankenversicherung Beiträge Schadensersatz", "Steuerberater Schätzungsbescheid Schadensersatz",
           "Steuerberater Verspätungszuschlag Schadensersatz", "Steuerberater Säumniszuschläge Schadensersatz", "Steuerberater Handakten Herausgabe",
           "Steuerberater Einspruchsfrist versäumt Schadensersatz", "Steuerberater Betriebsprüfung Schadensersatz Kassenführung",
           "Steuerberater Kleinunternehmer Schadensersatz", "Berufshaftpflichtversicherung Steuerberater Deckung", "Steuerberater Verjährung Schadensersatzanspruch Kenntnis"]
ENTRY = re.compile(r"<div class='einErgebnis'><a target='_blank'href='([^']+)'>([^<]*)</a>.*?Gericht:\s*([^<]*?)\s*<br />.*?Entscheidungsart:\s*([^<]*?)\s*<br />"
                   r"Aktenzeichen:\s*([^<]*?)\s*<br />(?:(ECLI[^<]*)<br />)?Entscheidungsdatum:\s*([0-9.]+)", re.S)


def search(s, q):
    base = {"q": q, "gerichtstyp": "", "gerichtsbarkeit": "", "gerichtsort": "", "entscheidungsart": "", "date": "", "von": "", "bis": "", "validFrom": "",
            "von2": "", "bis2": "", "aktenzeichen": "", "schlagwoerter": "", "method": "stem", "qSize": "100", "sortieren_nach": "relevanz",
            "advanced_search": "true"}
    out, page = [], 1
    while page <= 12:
        data = dict(base)
        data["absenden" if page == 1 else f"page{page}"] = "Suchen" if page == 1 else str(page)
        r = s.post(URL, data=data, headers=UA, timeout=90)
        hits = ENTRY.findall(r.text)
        n = re.search(r"Es wurden <strong>(\d+)</strong>", r.text)
        out += hits
        if not hits or not n or len(out) >= int(n.group(1)):
            break
        page += 1
        time.sleep(2)
    return out, int(n.group(1)) if n else 0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "nrwe").mkdir(exist_ok=True)
    s = requests.Session()
    s.get("https://nrwesuche.justiz.nrw.de/", headers=UA, timeout=60)
    found = {}
    for q in QUERIES:
        hits, total = search(s, q)
        for url, _, court, art, az, ecli, date in hits:
            found.setdefault(url, {"url": url, "court": court.strip(), "art": art.strip(), "az": az.strip(), "ecli": (ecli or "").strip(), "date": date, "q": []})["q"].append(q)
        print(f"{q!r}: всего {total}, взято {len(hits)}; разных {len(found)}", flush=True)
        time.sleep(2)
    json.dump(list(found.values()), open(OUT / "nrwe_treffer.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    n = 0
    for x in found.values():
        dst = OUT / "nrwe" / (re.sub(r"[^\w.-]+", "_", x["url"].split("nrwe.justiz.nrw.de/")[-1]))
        if dst.exists():
            continue
        try:
            r = requests.get(x["url"], headers=UA, timeout=90)
            if r.status_code == 200:
                dst.write_bytes(r.content)
                n += 1
            else:
                print("[!]", x["az"], r.status_code, flush=True)
                time.sleep(15)
        except Exception as e:
            print("[!]", x["az"], str(e)[:80], flush=True)
            time.sleep(15)
        time.sleep(1.0)
        if n and n % 100 == 0:
            print("скачано", n, flush=True)
    print("NRWE_DONE", n, flush=True)


if __name__ == "__main__":
    main()
