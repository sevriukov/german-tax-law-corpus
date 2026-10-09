#!/usr/bin/env python3
"""
collect_rii.py -- решения федеральных судов с официального портала rechtsprechung-im-internet.de (открытые, обезличенные).
Берём то, чего в базе нет и что понадобилось на реальном деле:
  bsg        -- Bundessozialgericht, дела о страховых взносах (сенаты KR и P: Krankenversicherung, Pflegeversicherung)
  bgh_haftung -- BGH, IX. Zivilsenat (ответственность Steuerberater и адвокатов) -- позже отбор по тексту
  python3 collect_rii.py            # скачать (резюмируемо, пауза 1 с) -> de_law_corpus/rii/<набор>/*.xml
  python3 collect_rii.py prepare    # -> de_law_corpus/prepared/bsg.jsonl, bgh_haftung.jsonl (отбор по теме)
"""
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

OUT = Path("de_law_corpus/rii")
UA = {"User-Agent": "Mozilla/5.0 (LeckerMath research; local legal database)"}


def toc():
    t = (OUT / "rii-toc.xml").read_text(encoding="utf-8", errors="ignore")
    for i in re.findall(r"<item>(.*?)</item>", t, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", i) or [None, ""])[1]      # noqa: E731
        yield g("gericht"), g("entsch-datum"), g("aktenzeichen"), g("link")


def want(ger, az):
    if ger.startswith("BSG") and re.search(r"\b(KR|P)\b", az):
        return "bsg"
    if ger.startswith("BGH") and (az.startswith("IX ZR") or "9. Zivilsenat" in ger):
        return "bgh_haftung"
    return None


def download():
    todo = [(want(g, az), d, az, l) for g, d, az, l in toc() if want(g, az)]
    print(f"к скачиванию: {len(todo)}", flush=True)
    n = 0
    for kind, date, az, link in todo:
        dst = OUT / kind / (re.sub(r"[^\w-]+", "_", f"{date}_{az}") + ".xml")
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(link.replace("http://", "https://"), headers=UA, timeout=60)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            name = next(x for x in z.namelist() if x.endswith(".xml"))
            dst.write_bytes(z.read(name))
            n += 1
        except Exception as e:
            print(f"[!] {az}: {str(e)[:80]}", flush=True)
            time.sleep(10)
        if n % 100 == 0 and n:
            print(f"скачано {n}", flush=True)
        time.sleep(1.0)
    print(f"готово: новых {n}", flush=True)


def text_of(xml, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    if not m:
        return ""
    t = re.sub(r"</(p|dd|dt|div|h\d|td)>", "\n", m.group(1))
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t.replace("&#160;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))).strip()


def prepare():
    rules = {"bsg": (r"§ ?240|freiwillig\w* (Mitglied|versichert)|Beitragsbemess|hauptberuflich selbst|Arbeitseinkommen|Einkommensteuerbescheid", "BSG"),
             "bgh_haftung": (r"Steuerberater|steuerliche[rn]? Berat", "BGH")}
    for kind, (rx, court) in rules.items():
        n, tot = 0, 0
        with open(Path("de_law_corpus/prepared") / f"{kind}.jsonl", "w", encoding="utf-8") as out:
            for f in sorted((OUT / kind).glob("*.xml")):
                xml = f.read_text(encoding="utf-8", errors="ignore")
                tot += 1
                parts = {k: text_of(xml, k) for k in ("leitsatz", "tenor", "tatbestand", "entscheidungsgruende", "gruende")}
                body = "\n\n".join(f"{k.upper()}:\n{v}" for k, v in (("Leitsatz", parts["leitsatz"]), ("Tenor", parts["tenor"]), ("Tatbestand", parts["tatbestand"]),
                                                                  ("Entscheidungsgründe", parts["entscheidungsgruende"] or parts["gruende"])) if v)
                if len(body) < 1500 or not re.search(rx, body):
                    continue
                date, az = f.stem.split("_", 1)[0], text_of(xml, "aktenzeichen") or f.stem.split("_", 1)[1].replace("_", " ")
                norms = sorted(set(re.findall(r"§ \d+[a-z]? (?:Abs\. \d+ )?(?:SGB [IVX]+|StBerG|BGB|AO|EStG|UStG)", body)), key=body.count, reverse=True)[:5]
                gist = " ".join((parts["leitsatz"] or parts["tatbestand"] or body)[:300].split())
                out.write(json.dumps({"doc_id": f"{kind}_{f.stem}", "source": kind, "tier": 1,
                                      "title": f"{court} {az} ({date[:4]}-{date[4:6]}-{date[6:]}). Normen: {', '.join(norms)}. {gist}",
                                      "text": body, "origin": "https://www.rechtsprechung-im-internet.de", "law_state_year": int(date[:4])}, ensure_ascii=False) + "\n")
                n += 1
        print(f"{kind}: отобрано по теме {n} из {tot}")


if __name__ == "__main__":
    prepare() if len(sys.argv) > 1 and sys.argv[1] == "prepare" else download()
