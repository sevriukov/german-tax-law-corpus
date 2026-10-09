#!/usr/bin/env python3
"""
collect_bund_neu.py -- два официальных источника уровня 1 (04.10.2026), сбор неспеша, с возобновлением:

  vwv   Verwaltungsvorschriften des Bundes (verwaltungsvorschriften-im-internet.de; robots: без ограничений) -- все ведомства,
        пауза 6-10 с -> quellen_neu/vwv/<файл>.htm + index.tsv (ведомство, заголовок, файл)
  bgbl  Bundesgesetzblatt Teil I и II с 2023 г. (recht.bund.de; robots: Crawl-delay 30) -- по карте сайта,
        PDF «regelungstext» каждой публикации -> quellen_neu/bgbl/<год>_<номер>.pdf + index.tsv
  python3 collect_bund_neu.py vwv|bgbl
Тексты -- amtliche Werke (§ 5 UrhG). Векторизация и загрузка в базу -- отдельным шагом.
"""
import html
import random
import re
import sys
import time
from pathlib import Path

import requests

UA = {"User-Agent": "LeckerMath research crawler (kontakt@example.org); langsam, amtliche Texte"}
OUT = Path("quellen_neu")


def get(url, pause, binary=False, tries=4):
    for t in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=90)
            if r.status_code == 200:
                time.sleep(pause + random.uniform(0, pause * 0.3))
                return r.content if binary else r.content
            if r.status_code in (403, 429, 503):
                print(f"  [{r.status_code}] {url} -- жду {300 * (t + 1)} с", flush=True)
                time.sleep(300 * (t + 1))
                continue
            print(f"  [{r.status_code}] {url}", flush=True)
            time.sleep(pause)
            return None
        except requests.RequestException as e:
            print(f"  [сеть] {url}: {str(e)[:80]}", flush=True)
            time.sleep(60 * (t + 1))
    return None


def vwv():
    base, d = "https://www.verwaltungsvorschriften-im-internet.de/", OUT / "vwv"
    d.mkdir(parents=True, exist_ok=True)
    idx = d / "index.tsv"
    have = {l.split("\t")[2].strip() for l in idx.open(encoding="utf-8")} if idx.exists() else set()
    start = get(base + "erlassstellen.html", 6)
    lists = re.findall(r'href="\./(Teilliste_[^"]+\.html)"[^>]*>([^<]*)', start.decode("latin1"))
    print(f"ведомств: {len(lists)}", flush=True)
    for li, (page, amt) in enumerate(lists, 1):
        raw = get(base + page, 6)
        if not raw:
            continue
        docs = re.findall(r'href="\./(bsvwvbund_[^"]+\.htm)"[^>]*>(?:<[^>]+>)*([^<]*)', raw.decode("latin1"))
        new = 0
        for f, title in docs:
            if f in have:
                continue
            body = get(base + f, 6)
            if not body:
                continue
            (d / f).write_bytes(body)
            with idx.open("a", encoding="utf-8") as fh:
                fh.write(f"{html.unescape(amt).strip()}\t{' '.join(html.unescape(title).split())}\t{f}\n")
            have.add(f)
            new += 1
        print(f"{li}/{len(lists)} {html.unescape(amt)[:50]}: документов {len(docs)}, новых {new}, всего {len(have)}", flush=True)
    print("VWV_DONE", flush=True)


def bgbl():
    d = OUT / "bgbl"
    d.mkdir(parents=True, exist_ok=True)
    sm = get("https://www.recht.bund.de/XMLSitemaps/Sitemap_Verkuendungen.xml", 30)
    items = sorted(set(re.findall(r"<loc>https://www\.recht\.bund\.de/bgbl/([12])/(\d{4})/(\d+[a-z]?)/VO\.html", sm.decode("utf-8"))),
                   key=lambda x: (int(x[0]), int(x[1]), int(re.sub(r"\D", "", x[2])), x[2]))
    print(f"публикаций в карте сайта: {len(items)} (Teil I и II)", flush=True)
    for k, (teil, y, n) in enumerate(items, 1):
        num = f"{int(re.sub(r'[^0-9]', '', n)):04d}{re.sub(r'[0-9]', '', n)}"
        f = d / (f"{y}_{num}.pdf" if teil == "1" else f"II_{y}_{num}.pdf")
        if f.exists() and f.stat().st_size > 1000:
            continue
        body = get(f"https://www.recht.bund.de/bgbl/{teil}/{y}/{n}/regelungstext.pdf?__blob=publicationFile&v=1", 30, binary=True)
        if body and body[:4] == b"%PDF":
            f.write_bytes(body)
        else:
            with (d / "fehlt.tsv").open("a") as fh:
                fh.write(f"{teil}\t{y}\t{n}\n")
        if k % 20 == 0:
            print(f"bgbl {k}/{len(items)} (Teil {teil}, {y}/{n})", flush=True)
    print("BGBL_DONE", flush=True)


if __name__ == "__main__":
    {"vwv": vwv, "bgbl": bgbl}[sys.argv[1]]()
