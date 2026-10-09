#!/usr/bin/env python3
"""
audit_chunking.py -- аудит КАЧЕСТВА НАРЕЗКИ DocumentRouter (из
rebuild_structured_gpu.py) на ВСЕХ документах steuer_corpus_test.

Только CPU, только чтение: никаких эмбеддингов, никаких записей в БД.
Прогоняет роутер на full_text и считает конкретные дефекты по источникам:

  empty      -- документ дал 0 чанков (ребилд такие МОЛЧА пропускает)
  lost_text  -- в чанки не попало >2% буквенно-цифровых символов
                санитизированного текста (выброшено TOC-фильтром и т.п.)
  oversize   -- чанк длиннее max_chars
  tiny       -- чанк короче 40 символов (огрызок маркера "II." / "1.")
  toc_left   -- в чанке >=3 строк вида "Заголовок ....... 12" (утечка оглавления)
  dup        -- одинаковый чанк повторяется внутри документа
  midword    -- граница чанков разрезала слово (буква|буква без пробела в оригинале)
  bad_join   -- sanitize_text склеил перенос с союзом ("Kauf-\\nund" -> "Kaufund")
  abbr_cut   -- чанк (не последний) обрывается на сокращении/порядковом числе
                ("z.", "Abs.", "BGBl.", "12.") -- предложение разрезано посередине

Для каждого дефекта сохраняет примеры в audit_chunking_samples.jsonl.

Запуск:
    python3 audit_chunking.py                 # весь корпус
    python3 audit_chunking.py --sources bgbl  # один источник
    python3 audit_chunking.py --limit 2000    # быстрая выборка
"""

import os
import argparse
import collections
import json
import multiprocessing as mp
import re
import time

import psycopg2

DB_CONFIG = {
    "dbname": "steuer_corpus_test",
    "user": "postgres",
    "password": os.environ.get("PGPASSWORD"),
    "host": "localhost",
    "port": "5432",
}

LOST_TEXT_THRESHOLD = 0.02
TINY_LEN = 40
SAMPLES_PER_DEFECT = 25

_ALNUM = re.compile(r"[0-9A-Za-zÄÖÜäöüß]")
_TOC_LINE = re.compile(r"[.·_]{5,}\s*\d+\s*$", re.MULTILINE)
_ABBR_CUT = re.compile(r"(?:\b(?:z|vgl|Abs|Nr|Art|S|BGBl|BStBl|bzw|Rn|Rz|Dr|Prof|d|u|i|ff|f|v|Buchst|Ziff|Tz|gem)\.|\b\d{1,3}\.)$")
_BAD_JOIN = re.compile(r"([a-zA-ZäöüÄÖÜß]+)-\s*\n\s*(und|oder|bzw|sowie|als auch)\b")

_router = None
_ROUTER_MODULE = "rebuild_structured_gpu"


def _init_worker():
    global _router
    import importlib
    _router = importlib.import_module(_ROUTER_MODULE).DocumentRouter()


def audit_doc(row):
    db_id, doc_id, source, text = row
    chunker = _router.chunkers.get(source, _router.fallback)
    chunks = _router.route_and_chunk(source, text)
    clean = chunker.sanitize_text(text)
    # потерю текста меряем ДО merge_small (он только склеивает соседей и
    # выкидывает точные дубли -- это не потеря содержания)
    raw_chunks = chunker.chunk(text)
    found = {}

    if not chunks:
        found["empty"] = {"len": len(text), "head": text[:300]}
    else:
        # строки оглавления, вырезанные чанкером намеренно (strip_toc_lines), -- не потеря
        base = chunker.strip_toc_lines(clean) if hasattr(chunker, "strip_toc_lines") else clean
        src_n = len(_ALNUM.findall(base))
        got_n = sum(len(_ALNUM.findall(c)) for c in raw_chunks)
        if src_n and (src_n - got_n) / src_n > LOST_TEXT_THRESHOLD:
            found["lost_text"] = {"lost_pct": round(100 * (src_n - got_n) / src_n, 1),
                                  "src_chars": src_n, "chunk_chars": got_n}

        limit = chunker.max_chars + getattr(chunker, "MERGE_LIMIT_SLACK", 0)
        big = [c for c in chunks if len(c) > limit]
        if big:
            found["oversize"] = {"n": len(big), "max": max(map(len, big)), "ex": big[0][:200]}

        tiny = [c for c in chunks if len(c.strip()) < TINY_LEN]
        # короткий документ целиком -- это не огрызок
        if tiny and len(chunks) > 1:
            found["tiny"] = {"n": len(tiny), "ex": tiny[:5]}

        toc = [c for c in chunks if len(_TOC_LINE.findall(c)) >= 3]
        if toc:
            found["toc_left"] = {"n": len(toc), "ex": toc[0][:400]}

        cnt = collections.Counter(c.strip() for c in chunks)
        dups = {c: n for c, n in cnt.items() if n > 1 and len(c) >= TINY_LEN}
        if dups:
            found["dup"] = {"n": sum(n - 1 for n in dups.values()), "ex": next(iter(dups))[:200]}

        mid = 0
        mid_ex = None
        for a, b in zip(chunks, chunks[1:]):
            ta, hb = a.rstrip(), b.lstrip()
            if ta and hb and ta[-1].isalpha() and hb[0].isalpha():
                # проверяем, что в оригинале между ними НЕ было пробела
                probe = ta[-15:] + hb[:15]
                if probe in clean or probe in re.sub(r"\s*\n\s*", " ", clean):
                    mid += 1
                    mid_ex = mid_ex or f"...{ta[-40:]} | {hb[:40]}..."
        if mid:
            found["midword"] = {"n": mid, "ex": mid_ex}

        cuts = [c for c in chunks[:-1] if _ABBR_CUT.search(c.rstrip())]
        if cuts:
            found["abbr_cut"] = {"n": len(cuts), "ex": [c.rstrip()[-80:] for c in cuts[:3]]}

    # дефект -- только если склейка реально случилась в санитизированном тексте
    bj = [m for m in _BAD_JOIN.findall(text) if (m[0] + m[1]) in clean]
    if bj:
        found["bad_join"] = {"n": len(bj), "ex": ["-".join(m) for m in bj[:5]]}

    return db_id, doc_id, source, len(chunks), found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--out", default="audit_chunking")
    ap.add_argument("--router-module", default="rebuild_structured_gpu",
                    help="модуль с DocumentRouter (напр. rebuild_structured_gpu_v2 для сравнения со старой версией)")
    args = ap.parse_args()

    global _ROUTER_MODULE
    _ROUTER_MODULE = args.router_module

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(name="audit_stream")
    cur.itersize = 500
    sql = "SELECT id, doc_id, source, full_text FROM documents WHERE full_text IS NOT NULL"
    params = []
    if args.sources:
        sql += " AND source = ANY(%s)"
        params.append(args.sources)
    sql += " ORDER BY md5(doc_id)" if args.limit else " ORDER BY id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cur.execute(sql, params)

    docs = collections.Counter()
    chunks_total = collections.Counter()
    defects = collections.defaultdict(collections.Counter)
    samples = collections.defaultdict(list)
    t0 = time.time()

    with mp.get_context("fork").Pool(args.procs, initializer=_init_worker) as pool:
        for i, (db_id, doc_id, source, n, found) in enumerate(
                pool.imap_unordered(audit_doc, cur, chunksize=20), 1):
            docs[source] += 1
            chunks_total[source] += n
            for kind, info in found.items():
                defects[source][kind] += 1
                key = (source, kind)
                if len(samples[key]) < SAMPLES_PER_DEFECT:
                    samples[key].append({"doc_id": doc_id, "db_id": db_id, **info})
            if i % 10000 == 0:
                print(f"  {i} документов, {time.time() - t0:.0f} с", flush=True)
    conn.close()

    kinds = ["empty", "lost_text", "oversize", "tiny", "toc_left", "dup", "midword", "bad_join", "abbr_cut"]
    lines = [f"{'source':15}{'docs':>8}{'chunks':>10}" + "".join(f"{k:>11}" for k in kinds)]
    for s in sorted(docs, key=lambda s: -docs[s]):
        lines.append(f"{s:15}{docs[s]:>8}{chunks_total[s]:>10}" + "".join(f"{defects[s][k]:>11}" for k in kinds))
    lines.append(f"{'TOTAL':15}{sum(docs.values()):>8}{sum(chunks_total.values()):>10}"
                 + "".join(f"{sum(defects[s][k] for s in docs):>11}" for k in kinds))
    report = "\n".join(lines)
    print("\nДокументов с дефектом (по типу):\n" + report)
    print(f"\nВремя: {time.time() - t0:.0f} с")

    with open(f"{args.out}_report.txt", "w", encoding="utf-8") as f:
        f.write(report + "\n")
    with open(f"{args.out}_samples.jsonl", "w", encoding="utf-8") as f:
        for (source, kind), items in sorted(samples.items()):
            for it in items:
                f.write(json.dumps({"source": source, "defect": kind, **it}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
