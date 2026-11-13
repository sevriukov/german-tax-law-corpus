#!/usr/bin/env python3
"""
load_new_sources.py -- (1) чанки-заголовки / карточки судов в БД, (2) загрузка
новых источников из de_law_corpus/prepared/*.jsonl (prepare_new_sources.py).

Изменения в БД только ДОБАВЛЯЮЩИЕ:
  document_titles (document_id, kind 'title'|'card', title_text, norms, embedding)
      -- отдельный "чанк-заголовок" рядом с чанками документа (решение 2026-10-01:
         чанки не пересчитываем). Суды: карточка (суд + Az + главные нормы + суть).
  source_tiers (source, tier, note) -- уровни источников (см. CLAUDE.md)
  новые строки в documents / document_chunks для новых источников.

Команды:
  python3 load_new_sources.py schema                 # таблицы + уровни (обе БД)
  python3 load_new_sources.py titles                 # заголовки/карточки существующих документов из eval_cache
  python3 load_new_sources.py load  --sources sternmeister_kurs lexware_help datev_help bmf_broschueren
  python3 load_new_sources.py load  --db steuer_literatur --sources literatur literatur_alt_2010

Эмбеддинги -- на ОБЕИХ GPU. Резюмируемо: документы с уже существующим doc_id пропускаются.
"""

# 18.10.2026: модель bge-m3 берём только из локального кэша — при сбоях DNS запросы к huggingface.co
# валили рабочие процессы эмбеддингов во время загрузки модели.
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


import datetime
import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import argparse
import io
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import Json, execute_values

CACHE = Path("eval_cache")
PREP = Path("de_law_corpus/prepared")
COURTS = ("bfh", "finanzgericht", "bverfg", "bgh_criminal", "eugh")
TIERS = {  # source -> (уровень, пояснение)
    **{s: (1, "референтная часть") for s in ("laws", "bfh", "bverfg", "finanzgericht", "bgh_criminal", "eugh", "eu_vat",
                                             "bmf", "bstbl", "bgbl", "bafin", "bundesbank", "fiu_reports", "bmf_handbuecher", "bsg", "bgh", "bgh_haftung", "olg_haftung", "lsg_beitrag", "vwv_bund")},
    "landtag_drucksachen": (1, "Drucksachen der Landtage: Gesetzentwürfe mit Begründung (Landesgrundsteuergesetze) (20.10.2026)"),
    "laender_erlasse": (1, "Anwendungserlasse und Erlasse der Länderfinanzministerien (Grundsteuer) (20.10.2026)"),
    "landesrecht": (1, "Landesrecht: Grundsteuergesetze der Länder (BayGrStG, NGrStG, LGrStG BW, HmbGrStG, Sachsen) — официальный текст (19.10.2026)"),
    "bmf_formulare": (1, "BMF/BZSt: amtliche Vordrucke und Ausfüllanleitungen — официальные бланки (19.10.2026)"),
    "bstbk": (2, "Bundessteuerberaterkammer: Berufsordnung, Hinweise, AuA GwG — мнение палаты, не источник права (18.10.2026)"),
    "aml_eu": (1, "противодействие отмыванию: акты ЕС (15.10.2026)"),
    "aml_aufsicht": (1, "противодействие отмыванию: BaFin AuA, Länder, национальная оценка рисков (15.10.2026)"),
    "gw_urteile": (1, "решения судов с Geldwäsche / § 261 StGB, openlegaldata (15.10.2026)"),
    "sozialgericht": (1, "решения SG/LSG/BSG, openlegaldata (15.10.2026)"),
    "zoll_vorschriften": (1, "таможня: предписания и приложения из Vorschriftensuche zoll.de (16.10.2026)"),
    "bmf_berichte": (2, "BMF: брошюры, Monatsberichte, отчёты и планы против нарушений (16.10.2026)"),
    "typologien": (2, "типологии нарушений: доклады EU/OECD/FATF-Egmont (16.10.2026)"),
    "bt_drucksachen": (1, "Gesetzesmaterialien: законопроекты Бундестага с пояснительными записками, DIP (16.10.2026)"),
    "dba": (1, "DBA: законы и объявления BGBl II, сканы BMF, распознаны (15.10.2026)"),
    "intl_abkommen": (1, "международные соглашения: оговорки и приложения сторон (MAC: RU 325-ФЗ, DE BT-Drs. 18/5173), 15.10.2026"),
    "laws_historie": (1,"прежние редакции законов: официальный текст, но НЕ действующее право (valid_until заполнен)"),
    **{s: (2, "практические справки") for s in ("datev_help", "lexware_help", "sternmeister_kurs", "bmf_broschueren", "datev_kontenrahmen")},
    "literatur": (3, "литература: служебное/личное, только после проверки по уровню 1"),
    "literatur_alt_2010": (3, "учебник 2010 г.: право на 2010 год, в последнюю очередь, с проверкой по уровню 1"),
}


def connect(db):
    return psycopg2.connect(dbname=db, user="postgres", host="localhost")


def vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def enriched_title(source, title, doc_id):
    title = title or ""
    if source == "laws":
        m = re.match(r"law_\d+__(.*)_\d{3}_", doc_id)
        return f"{m.group(1).replace('_', ' ') if m else ''}: {title}"
    return title


def cmd_schema(args):
    # отдельная БД для литературы (уровень 3) -- та же схема
    out = subprocess.run(["docker", "exec", "steuer_db", "psql", "-U", "postgres", "-At", "-c",
                          "SELECT 1 FROM pg_database WHERE datname='steuer_literatur'"], capture_output=True, text=True).stdout
    if not out.strip():
        subprocess.run(["docker", "exec", "steuer_db", "psql", "-U", "postgres", "-c", "CREATE DATABASE steuer_literatur"], check=True)
        subprocess.run("docker exec steuer_db pg_dump -U postgres --schema-only -t documents -t document_chunks steuer_corpus_test "
                       "| docker exec -i steuer_db psql -U postgres -q -d steuer_literatur -c 'CREATE EXTENSION IF NOT EXISTS vector' "
                       "-c 'CREATE EXTENSION IF NOT EXISTS pg_trgm' -f -", shell=True, check=True)
        print("создана БД steuer_literatur")
    for db in ("steuer_corpus_test", "steuer_literatur"):
        conn = connect(db)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS document_titles (
                         document_id integer PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                         kind text NOT NULL, title_text text NOT NULL, norms text[], embedding vector(1024))""")
        cur.execute("CREATE TABLE IF NOT EXISTS source_tiers (source text PRIMARY KEY, tier smallint NOT NULL, note text)")
        execute_values(cur, "INSERT INTO source_tiers (source, tier, note) VALUES %s ON CONFLICT (source) DO UPDATE "
                            "SET tier = EXCLUDED.tier, note = EXCLUDED.note", [(s, t, n) for s, (t, n) in TIERS.items()])
        conn.commit()
        conn.close()
        print(f"{db}: document_titles, source_tiers -- готово")


def cmd_titles(args):
    conn = connect("steuer_corpus_test")
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM document_titles")
    if cur.fetchone()[0]:
        print("document_titles уже заполнена -- пропускаю (для новых документов заголовки пишет load)")
        return
    cur.execute("SELECT id, source, title, doc_id FROM documents ORDER BY id")
    docs = cur.fetchall()
    title_emb = np.load(CACHE / "title_emb.npy")
    assert len(title_emb) == len(docs), (len(title_emb), len(docs))
    cc = json.load(open(CACHE / "court_cards.json"))
    cards = {int(k): v for k, v in cc["cards"].items()}
    norms = {int(k): v for k, v in cc["norms"].items()}
    card_ids = sorted(cards)
    card_emb = dict(zip(card_ids, np.load(CACHE / "court_card_emb.npy")))
    buf, n = io.StringIO(), 0
    esc = lambda s: s.replace("\\", "\\\\").replace("\t", " ").replace("\n", "\\n").replace("\r", " ")  # noqa: E731
    for i, (db_id, src, title, doc_id) in enumerate(docs):
        if src in COURTS and db_id in cards:
            kind, text, emb, nr = "card", cards[db_id], card_emb[db_id], norms.get(db_id, [])
        else:
            kind, text, emb, nr = "title", enriched_title(src, title, doc_id), title_emb[i], []
        arr = "{" + ",".join('"' + x.replace('"', "") + '"' for x in nr) + "}"
        buf.write(f"{db_id}\t{kind}\t{esc(text) or '-'}\t{arr}\t{vec(emb)}\n")
        n += 1
        if n % 10000 == 0:
            buf.seek(0)
            cur.copy_from(buf, "document_titles", columns=("document_id", "kind", "title_text", "norms", "embedding"))
            buf = io.StringIO()
            print(f"  {n:,}/{len(docs):,}", flush=True)
    buf.seek(0)
    cur.copy_from(buf, "document_titles", columns=("document_id", "kind", "title_text", "norms", "embedding"))
    conn.commit()
    cur.execute("SELECT kind, count(*) FROM document_titles GROUP BY 1")
    print("document_titles:", dict(cur.fetchall()))
    conn.close()


def cmd_load(args):
    from emb_sicher import Pool
    from rebuild_structured_gpu import FallbackChunker

    conn = connect(args.db)
    cur = conn.cursor()
    cur.execute("SELECT doc_id FROM documents")
    have = {r[0] for r in cur.fetchall()}
    todo, seen = [], set()
    for src in args.sources:
        f = PREP / f"{src}.jsonl"
        if not f.exists():
            print(f"[!] нет {f}")
            continue
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            if len(d["text"]) < 200 or d.get("needs_ocr"):
                continue
            # большие книги -> части по ~200 тыс. знаков: у tsvector лимит ~1 МБ на документ,
            # и для поиска часть с собственным заголовком полезнее книги целиком
            parts = [d]
            if len(d["text"]) > 300_000:
                parts, buf, n = [], "", 0
                for para in d["text"].split("\n\n"):
                    if len(buf) + len(para) > 200_000 and buf:
                        n += 1
                        parts.append({**d, "doc_id": f"{d['doc_id']}_p{n:03d}", "title": f"{d['title']} — Teil {n}", "text": buf})
                        buf = ""
                    buf += para + "\n\n"
                n += 1
                parts.append({**d, "doc_id": f"{d['doc_id']}_p{n:03d}", "title": f"{d['title']} — Teil {n}", "text": buf})
            for p in parts:                      # 15.10.2026: два решения с одной датой и Az (BVerfG) -> второе с суффиксом _v2
                k = 2
                while p["doc_id"] in seen:
                    p = {**p, "doc_id": re.sub(r"(_v\d+)?$", f"_v{k}", p["doc_id"], count=1)}
                    k += 1
                seen.add(p["doc_id"])
                if p["doc_id"] not in have:
                    todo.append(p)
    print(f"{args.db}: новых документов {len(todo)} (источники: {', '.join(args.sources)})")
    if not todo:
        return

    chunker_lit = FallbackChunker()          # книги: фильтр оглавлений включён
    chunker_plain = FallbackChunker()        # справки/курс: оглавлений с номерами страниц нет
    chunker_plain.toc_filter = False
    chunker_plain.detect_glued_toc = False

    # 18.10.2026: свой пул (emb_sicher): при OOM -- повтор куска на 48/32/16, при сбое GPU -- выход с ошибкой, а не вечное ожидание
    pool = Pool(["cuda:0", "cuda:1"])
    t0, n_docs, n_chunks = time.time(), 0, 0
    BATCH_DOCS = 20 if args.db == 'steuer_literatur' else 200   # книги: мелкие пачки, чтобы сбой не терял весь расчёт
    for s in range(0, len(todo), BATCH_DOCS):
        part = todo[s:s + BATCH_DOCS]
        chunks_per_doc = []
        for d in part:
            ch = (chunker_lit if d["tier"] == 3 else chunker_plain)
            chunks_per_doc.append(ch.merge_small(ch.chunk(d["text"])))
        flat = [c for cs in chunks_per_doc for c in cs]
        titles = [d["title"] for d in part]
        emb = pool.encode(flat + titles, int(os.environ.get("EMB_BATCH", "64")))
        ch_emb, t_emb = emb[:len(flat)], emb[len(flat):]
        k = 0
        for d, cs, te in zip(part, chunks_per_doc, t_emb):
            for f in ("valid_from", "valid_until", "effective_date"):     # 18.10.2026: невозможная дата из разбора (2024-07-62) -> NULL, сырой вид -- в metadata
                if d.get(f):
                    try:
                        datetime.date.fromisoformat(str(d[f])[:10])
                    except ValueError:
                        d.setdefault("_roh_daten", {})[f] = d[f]
                        d[f] = None
            year = d.get("law_state_year")
            meta = {"tier": d["tier"], "origin": d["origin"], "law_state_year": year, "pages": d.get("pages")}
            if d.get("_roh_daten"):
                meta["roh_daten"] = d["_roh_daten"]
            if d.get("changed_before"):
                meta["changed_before"] = d["changed_before"]
            until = d.get("valid_until") or (f"{year}-12-31" if d["source"] == "literatur_alt_2010" and year else None)
            cur.execute("""INSERT INTO documents (doc_id, source, title, url, full_text, metadata, valid_from, valid_until, reference_number, effective_date)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (d["doc_id"], d["source"], d["title"][:500], d["origin"] if str(d["origin"]).startswith("http") else "N/A",
                         d["text"], Json(meta), d.get("valid_from"), until,
                         d.get("reference_number"), d.get("effective_date")))      # суды (15.10.2026): точный поиск по номеру дела
            db_id = cur.fetchone()[0]
            execute_values(cur, "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding) VALUES %s",
                           [(db_id, i, c, vec(ch_emb[k + i])) for i, c in enumerate(cs)], template="(%s, %s, %s, %s::vector)")
            cur.execute("INSERT INTO document_titles (document_id, kind, title_text, norms, embedding) VALUES (%s, 'title', %s, '{}', %s::vector)",
                        (db_id, d["title"], vec(te)))
            k += len(cs)
        conn.commit()
        n_docs += len(part)
        n_chunks += len(flat)
        el = (time.time() - t0) / 60
        print(f"  {n_docs}/{len(todo)} документов, {n_chunks:,} чанков, {n_chunks / max(el, 0.01):.0f} чанков/мин", flush=True)
    pool.close()
    conn.close()
    print(f"✅ загружено: {n_docs} документов, {n_chunks:,} чанков за {(time.time() - t0) / 60:.1f} мин")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["schema", "titles", "load"])
    ap.add_argument("--db", default="steuer_corpus_test")
    ap.add_argument("--sources", nargs="+", default=[])
    args = ap.parse_args()
    {"schema": cmd_schema, "titles": cmd_titles, "load": cmd_load}[args.cmd](args)


if __name__ == "__main__":
    main()
