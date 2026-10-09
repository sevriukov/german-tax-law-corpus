#!/usr/bin/env python3
"""
collect_gii_aktuell.py -- действующие тексты всех федеральных законов с официального сайта gesetze-im-internet.de (xml.zip по списку gii-toc.xml).
Зачем: источник laws в базе взят из стороннего набора (harshildarji/bundesrecht), где порядок предложений и перечней внутри абзаца потерян
(дефект найден 08.10.2026). Официальный XML -- основа для сверки и перезагрузки. Только скачивание в de_law_corpus/gii_aktuell/; резюмируемо.
    python3 collect_gii_aktuell.py
"""
import re, time
from pathlib import Path
import requests

UA = {"User-Agent": "Mozilla/5.0 (LeckerMath research; local legal database)"}
OUT = Path("de_law_corpus/gii_aktuell"); OUT.mkdir(parents=True, exist_ok=True)
toc = requests.get("https://www.gesetze-im-internet.de/gii-toc.xml", headers=UA, timeout=120).text
(OUT / "gii-toc.xml").write_text(toc, encoding="utf-8")
links = re.findall(r"<link>(.*?)</link>", toc)
print(len(links), "законов", flush=True)
n = err = 0
t0 = time.time()
for k, link in enumerate(links, 1):
    slug = link.rstrip("/").split("/")[-2]
    dst = OUT / f"{slug}.zip"
    if dst.exists() and dst.stat().st_size > 200:
        continue
    for attempt in range(4):
        try:
            r = requests.get(link.replace("http://", "https://"), headers=UA, timeout=90)
            if r.status_code == 200 and r.content[:2] == b"PK":
                dst.write_bytes(r.content); n += 1; break
            if r.status_code == 404:
                err += 1; break
            time.sleep(20)
        except Exception:
            time.sleep(30)
    else:
        err += 1
    time.sleep(0.6)
    if n and n % 500 == 0:
        print(f"скачано {n} ({k}/{len(links)}), ошибок {err}, {round((time.time() - t0) / 60)} мин", flush=True)
print(f"GII_DONE новых {n}, ошибок {err}", flush=True)
