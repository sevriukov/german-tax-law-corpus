#!/usr/bin/env python3
"""
prepare_historie.py -- прежние редакции федеральных законов из открытого репозитория bundestag/gesetze (Unlicense), 04.10.2026.

В репозитории не ежегодные срезы, а состояния на даты ВНЕСЕНИЯ коммитов (2012–2013 подробно, затем 2021–03.2022).
Важно: берётся дата внесения (%cd), а не дата автора: у коммита 5710c0a6 дата автора 2018-11-08, внесён 2022-01-28 --
содержимое на январь 2022 г. (обнаружено 05.10.2026 по порогу § 19 UStG и Grundfreibetrag; окна в базе исправлены).
Для каждого параграфа каждой нормы собираются РАЗЛИЧАЮЩИЕСЯ редакции; редакция, совпадающая с действующим текстом в базе, не берётся.
У редакции: valid_from = первая дата, когда она наблюдалась, valid_until = последняя дата, когда она ещё наблюдалась; changed_before = дата среза, где текст уже другой
(точная дата изменения по этому источнику неизвестна -- это сказано в заголовке).
  python3 prepare_historie.py   -> de_law_corpus/prepared/laws_historie.jsonl
Источник уровня 1, но НЕ действующее право: valid_until заполнен, поиск помечает «VERALTET».
"""
import os
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import psycopg2

REPO = Path("quellen_neu/gesetze_git")
OUT = Path("de_law_corpus/prepared/laws_historie.jsonl")
HEAD = re.compile(r"^#{2,6} +((?:§+|Art\.?|Artikel) *\d+[a-z]*)\b *(.*)$", re.M)


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, errors="replace").stdout


def norm(t):
    return re.sub(r"[^0-9a-zäöüß§]", "", t.lower())


def sections(md):
    m = re.search(r"^jurabk: *(.+)$", md, re.M)
    abk = m.group(1).strip().strip("'\"") if m else None
    hs = list(HEAD.finditer(md))
    out = {}
    for i, h in enumerate(hs):
        body = md[h.end():hs[i + 1].start() if i + 1 < len(hs) else len(md)]
        body = re.sub(r"^#{1,6} .*$", "", body, flags=re.M)          # заголовки разделов между параграфами
        body = "\n".join(l.strip() for l in body.splitlines()).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        num = re.sub(r"\s+", " ", h.group(1).replace("Artikel", "Art").replace("Art.", "Art")).strip()
        num = re.sub(r"^(§+|Art) ?", lambda x: x.group(1) + " ", num)
        if len(body) > 80:
            out[num] = (h.group(2).strip(), body)
    return abk, out


def main():
    cur = psycopg2.connect(dbname="steuer_corpus_test", user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), host=os.environ.get("PGHOST", "localhost")).cursor()
    cur.execute("SELECT title, full_text FROM documents WHERE source='laws'")
    current = defaultdict(set)
    for title, text in cur.fetchall():
        m = re.match(r"^(.*?) ((?:§+|Art) \d+[a-z]*)\b", title)
        if m:
            current[(m.group(1), m.group(2))].add(hashlib.md5(norm(text).encode()).hexdigest())
    print("действующих параграфов в базе:", len(current), flush=True)
    # коммиты в хронологическом порядке и затронутые ими файлы
    log = git("log", "--reverse", "--format=@%H %cd", "--date=short", "--name-only", "--", "*/index.md")
    touched, dates, order = defaultdict(list), {}, []
    c = None
    for line in log.splitlines():
        if line.startswith("@"):
            c, d = line[1:].split()
            dates[c] = d
            order.append(c)
        elif line.strip().endswith("index.md"):
            touched[line.strip()].append(c)
    last_date = dates[order[-1]]
    print(f"файлов законов: {len(touched)}, коммитов: {len(order)}, срезы {dates[order[0]]} … {last_date}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_docs = n_laws = 0
    with OUT.open("w", encoding="utf-8") as fh:
        for k, (path, commits) in enumerate(sorted(touched.items()), 1):
            versions = defaultdict(list)                 # (abk, §) -> [(первая дата, последняя дата, заголовок, текст, хеш)]
            abk_last = None
            for i, c in enumerate(commits):
                md = git("show", f"{c}:{path}")
                if not md:
                    continue
                abk, secs = sections(md)
                abk_last = abk or abk_last
                for num, (head, body) in secs.items():
                    h = hashlib.md5(norm(body).encode()).hexdigest()
                    v = versions[(abk_last, num)]
                    if v and v[-1][4] == h:
                        v[-1] = (v[-1][0], dates[c], v[-1][2], v[-1][3], h, None)      # та же редакция: продлить «видели до»
                    else:
                        if v:
                            v[-1] = v[-1][:5] + (dates[c],)                             # прежняя редакция изменена не позже этой даты
                        v.append((dates[c], dates[c], head, body, h, None))
            wrote = False
            for (abk, num), v in versions.items():
                if not abk:
                    continue
                cur_h = current.get((abk, num), set())
                for first, last, head, body, h, changed in v:
                    if h in cur_h:                       # совпадает с действующим текстом -- уже в базе
                        continue
                    span = f"gesehen {first}" + (f" bis {last}" if last != first else "") + (f"; geändert vor {changed}" if changed else "; geändert nach diesem Stand")
                    title = f"{abk} {num} - {head}".rstrip(" -") + f" (frühere Fassung, {span})"
                    fh.write(json.dumps({"doc_id": f"hist_{re.sub(r'[^A-Za-z0-9]+', '_', abk)}_{re.sub(r'[^A-Za-z0-9]+', '', num)}_{first}",
                                         "source": "laws_historie", "title": title, "text": body, "tier": 1,
                                         "origin": f"https://github.com/bundestag/gesetze/blob/master/{path}", "law_state_year": int(last[:4]),
                                         "valid_from": first, "valid_until": last, "changed_before": changed}, ensure_ascii=False) + "\n")
                    n_docs += 1
                    wrote = True
            n_laws += wrote
            if k % 500 == 0:
                print(f"  {k}/{len(touched)} законов, прежних редакций {n_docs}", flush=True)
    print(f"готово: {OUT}: прежних редакций {n_docs} в {n_laws} законах", flush=True)


if __name__ == "__main__":
    main()
