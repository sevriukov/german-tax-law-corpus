#!/usr/bin/env python3
"""
collect_wayback_gii.py -- прежние состояния налоговых и смежных законов из Архива интернета (05.10.2026).

Источник: снимки официальных файлов gesetze-im-internet.de/<закон>/xml.zip в web.archive.org (тексты -- amtliche Werke, § 5 UrhG).
Для каждого закона из quellen_neu/wayback/slugs.txt: перечень РАЗЛИЧАЮЩИХСЯ снимков (CDX, collapse=digest) -> скачивание.
Последовательно, пауза 4-6 с, с возобновлением.  -> quellen_neu/wayback/<закон>/<метка времени>.zip
  python3 collect_wayback_gii.py
"""
import json
import random
import sys
import time
from pathlib import Path

import requests

UA = {"User-Agent": "LeckerMath research (kontakt@example.org); langsam, amtliche Gesetzestexte"}
W = Path("quellen_neu/wayback")


def get(url, tries=5, **kw):
    for t in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=120, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503, 504):
                time.sleep(120 * (t + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(60 * (t + 1))
    return None


def wait_cdx(max_h=30):
    """Индекс архива бывает недоступен (503 «Temporarily Offline»): ждать его возвращения, проверяя раз в 10 минут."""
    for k in range(max_h * 6):
        try:
            r = requests.get("https://web.archive.org/cdx/search/cdx", headers=UA, timeout=60,
                             params={"url": "gesetze-im-internet.de/estg/xml.zip", "output": "json", "limit": 1})
            if r.status_code == 200 and r.text.strip().startswith("["):
                return True
        except requests.RequestException:
            pass
        if k % 6 == 0:
            print(f"  индекс архива недоступен, жду ({time.strftime('%H:%M')})", flush=True)
        time.sleep(600)
    return False


def main():
    slugs = [s for s in (W / "slugs.txt").read_text().split() if s]
    if "--rest" in sys.argv:                                  # только законы, по которым ещё нет перечня снимков
        slugs = [s for s in slugs if not (W / s / "cdx.json").exists()]
        print(f"осталось законов: {len(slugs)}", flush=True)
        if not wait_cdx():
            print("WAYBACK_REST_ABBRUCH индекс архива не вернулся", flush=True)
            return
    n_new = 0
    for k, slug in enumerate(slugs, 1):
        d = W / slug
        d.mkdir(exist_ok=True)
        cdx_f = d / "cdx.json"
        if not cdx_f.exists():
            r = get("https://web.archive.org/cdx/search/cdx", params={"url": f"gesetze-im-internet.de/{slug}/xml.zip", "output": "json",
                                                                      "fl": "timestamp,digest,length", "filter": "statuscode:200", "collapse": "digest"})
            time.sleep(4 + random.uniform(0, 2))
            if r is None and wait_cdx():                      # архив вернулся -- ещё одна попытка для этого же закона
                r = get("https://web.archive.org/cdx/search/cdx", params={"url": f"gesetze-im-internet.de/{slug}/xml.zip", "output": "json",
                                                                          "fl": "timestamp,digest,length", "filter": "statuscode:200", "collapse": "digest"})
            if r is None:
                print(f"{k}/{len(slugs)} {slug}: перечень снимков не получен", flush=True)
                continue
            cdx_f.write_text(r.text)
        try:
            rows = json.loads(cdx_f.read_text() or "[]")[1:]
        except json.JSONDecodeError:
            rows = []
        got = 0
        for ts, digest, length in rows:
            f = d / f"{ts}.zip"
            if f.exists() and f.stat().st_size > 200:
                continue
            r = get(f"https://web.archive.org/web/{ts}id_/http://www.gesetze-im-internet.de/{slug}/xml.zip")
            time.sleep(4 + random.uniform(0, 2))
            if r is not None and r.content[:2] == b"PK":
                f.write_bytes(r.content)
                got += 1
                n_new += 1
        print(f"{k}/{len(slugs)} {slug}: снимков {len(rows)}, скачано новых {got}", flush=True)
    print(("WAYBACK_REST_DONE" if "--rest" in sys.argv else "WAYBACK_DONE") + f" новых файлов {n_new}", flush=True)


if __name__ == "__main__":
    main()
