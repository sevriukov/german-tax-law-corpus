#!/usr/bin/env python3
"""
spacy_boundary_validator.py -- spaCy-ВАЛИДАТОР границ чанков (та самая
"стадия 2" из HANDOFF, которой не было в коде). Ничего не перечанковывает и
в БД не пишет -- только проверяет.

Для каждой границы между соседними чанками A|B берёт окно
A[-WINDOW:] + " " + B[:WINDOW], прогоняет spaCy (senter) и смотрит, есть ли
граница предложения ровно в точке стыка (допуск +-2 символа). Если нет --
чанк разрезал предложение посередине.

Запуск:
    python3 spacy_boundary_validator.py --doc-ids <doc_id> [<doc_id> ...]
    python3 spacy_boundary_validator.py --audit-samples audit_chunking_v3_samples.jsonl --defect abbr_cut
"""

import os
import argparse
import json
import re

import psycopg2
import spacy

from rebuild_structured_gpu import DocumentRouter

DB_CONFIG = {
    "dbname": "steuer_corpus_test",
    "user": "postgres",
    "password": os.environ.get("PGPASSWORD"),
    "host": "localhost",
    "port": "5432",
}
WINDOW = 300
TOLERANCE = 2

nlp = spacy.load("de_core_news_sm", exclude=["ner", "lemmatizer", "parser", "tagger",
                                             "morphologizer", "attribute_ruler"])
nlp.enable_pipe("senter")


# Структурная граница: слева конец фразы, справа маркер абзаца/перечня
# ("6 Bei...", "d) die...", "1. Der...", "II.", "§ 5", "Rn. 12") -- spaCy такое
# часто не считает началом предложения, но для чанкинга это правильный стык.
_END = re.compile(r"[.!?:;,]$")
_MARKER = re.compile(r"^(?:\(?\d+[.)]?|[a-z]{1,2}\)|[IVX]+\.|§|R[nz]\.|[-–•])\s")


def boundary_ok(a: str, b: str) -> bool:
    left = a.rstrip()[-WINDOW:]
    right = b.lstrip()[:WINDOW]
    if _END.search(left) and _MARKER.match(right):
        return True
    doc = nlp(left + " " + right)
    join = len(left)
    return any(abs(s.start_char - join) <= TOLERANCE for s in doc.sents if s.start_char > 0)


def validate(chunks: list[str]):
    bad = []
    for i, (a, b) in enumerate(zip(chunks, chunks[1:])):
        if not boundary_ok(a, b):
            bad.append((i, a.rstrip()[-70:], b.lstrip()[:70]))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-ids", nargs="+")
    ap.add_argument("--audit-samples")
    ap.add_argument("--defect", default="abbr_cut")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    doc_ids = list(args.doc_ids or [])
    if args.audit_samples:
        with open(args.audit_samples, encoding="utf-8") as f:
            doc_ids += [d["doc_id"] for d in map(json.loads, f) if d["defect"] == args.defect]

    router = DocumentRouter()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    total_b = total_bad = 0
    for doc_id in doc_ids:
        cur.execute("SELECT source, full_text FROM documents WHERE doc_id = %s;", (doc_id,))
        row = cur.fetchone()
        if not row:
            continue
        source, text = row
        chunks = router.route_and_chunk(source, text)
        bad = validate(chunks)
        total_b += max(len(chunks) - 1, 0)
        total_bad += len(bad)
        print(f"\n{source:13} {doc_id[:70]}: чанков {len(chunks)}, границ посреди предложения: {len(bad)}")
        for i, a, b in bad[:args.show]:
            print(f"   #{i}: ...{a!r}  ||  {b!r}...")
    conn.close()
    if total_b:
        print(f"\nИТОГО: {total_bad} из {total_b} границ ({100 * total_bad / total_b:.2f}%) режут предложение по мнению spaCy")


if __name__ == "__main__":
    main()
