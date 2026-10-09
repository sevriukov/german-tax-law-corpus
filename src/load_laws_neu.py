#!/usr/bin/env python3
"""
load_laws_neu.py -- исправление действующих текстов законов по официальному XML (после prepare_laws_neu.py), 08.10.2026.
Обновляются все законы, с пересчётом фрагментов и векторов
затронутых параграфов. Резервная копия: backups/steuer_corpus_test_2026-10-08_mit_sozialgerichten.dump (проверена).

  порядок, текст  -> full_text заменяется официальным; прежний текст -- в таблицу documents_fulltext_backup_20261008;
                     фрагменты документа удаляются и строятся заново (тот же разборщик, что был у законов), векторы -- на обеих картах
  нет в базе      -> новый документ (источник laws), фрагменты, чанк-заголовок
  нет в XML       -> документ остаётся, но помечается как недействующий: valid_until = 2026-10-08, metadata.nicht_im_bestand
  совпадает       -> не трогается
Резюмируемо: уже исправленные (metadata.text_quelle) и уже добавленные (doc_id) пропускаются.
    python3 load_laws_neu.py
"""
import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

import prepare_laws_neu as P

QUELLE = "gii_xml_2026-10-08"


def vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def law_names():
    out = {}
    for f in Path("de_law_corpus/gii_aktuell").glob("*.zip"):
        try:
            z = zipfile.ZipFile(f)
            root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".xml"))))
            md = root.find("norm/metadaten")
            lang = md.find("langue")
            out[f.stem] = " ".join("".join(lang.itertext()).split()) if lang is not None else (md.findtext("jurabk") or f.stem)
        except Exception:
            out[f.stem] = f.stem
    return out


def main():
    from sentence_transformers import SentenceTransformer
    from rebuild_structured_gpu import _laws_chunker
    conn = psycopg2.connect(dbname="steuer_corpus_test", user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), host=os.environ.get("PGHOST", "localhost"))
    cur = conn.cursor()
    rows = [json.loads(l) for l in open(P.OUT, encoding="utf-8")]
    cur.execute("CREATE TABLE IF NOT EXISTS documents_fulltext_backup_20261008 (id integer PRIMARY KEY, doc_id text, full_text text, cls text)")
    cur.execute("SELECT id FROM documents WHERE source = 'laws' AND metadata->>'text_quelle' = %s", (QUELLE,))
    done = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT doc_id FROM documents WHERE source = 'laws'")
    have = {r[0] for r in cur.fetchall()}
    names = law_names()
    todo = []
    for r in rows:
        if r["cls"] in ("порядок", "текст") and r["id"] not in done:
            todo.append(r)
        elif r["cls"] == "нет в базе":
            abk, enbez = P.key_of_title(r["title"])
            r["doc_id"] = "law_gii__" + re.sub(r"[^\w]+", "_", f"{r['slug']}_{enbez}")[:150]
            r["abk"] = abk
            if r["doc_id"] not in have:
                have.add(r["doc_id"])
                todo.append(r)
    print(f"к обработке: {len(todo)} (замена текста {sum(r['cls'] != 'нет в базе' for r in todo)}, новых {sum(r['cls'] == 'нет в базе' for r in todo)})", flush=True)
    # недействующие
    xml_keys = {P.key_of_title(r["title"]) for r in rows}
    cur.execute("SELECT id, title FROM documents WHERE source = 'laws' AND valid_until IS NULL AND coalesce(metadata->>'text_quelle','') <> %s AND doc_id NOT LIKE 'law_gii__%%'", (QUELLE,))
    lost = [i for i, t in cur.fetchall() if P.key_of_title(t) not in xml_keys]
    if lost:
        cur.execute("""UPDATE documents SET valid_until = DATE '2026-10-08',
                       metadata = coalesce(metadata, '{}'::jsonb) || '{"nicht_im_bestand": "в официальном тексте на 08.10.2026 не найдено (отменено или переименовано)"}'::jsonb
                       WHERE id = ANY(%s)""", (lost,))
        conn.commit()
    print(f"помечено недействующими: {len(lost)}", flush=True)
    if not todo:
        return
    ch = _laws_chunker()
    model = SentenceTransformer("BAAI/bge-m3", device="cuda:0", model_kwargs={"use_safetensors": True})
    pool = model.start_multi_process_pool(["cuda:0", "cuda:1"])
    t0, n_docs, n_chunks = time.time(), 0, 0
    B = 300
    for s in range(0, len(todo), B):
        part = todo[s:s + B]
        cs = [ch.merge_small(ch.chunk(r["text"])) or [r["text"]] for r in part]
        flat = [c for x in cs for c in x]
        neu = [r for r in part if r["cls"] == "нет в базе"]
        titles = [f"{names.get(r['slug'], r['abk'])}: {r['title']}" for r in neu]
        emb = model.encode(flat + titles, pool=pool, batch_size=64, convert_to_numpy=True, normalize_embeddings=True)
        t_emb = dict(zip([r["doc_id"] for r in neu], emb[len(flat):]))
        k = 0
        for r, c in zip(part, cs):
            if r["cls"] == "нет в базе":
                cur.execute("""INSERT INTO documents (doc_id, source, title, url, full_text, metadata) VALUES (%s, 'laws', %s, %s, %s, %s) RETURNING id""",
                            (r["doc_id"], r["title"][:500], f"https://www.gesetze-im-internet.de/{r['slug']}/", r["text"],
                             Json({"jurabk": r["abk"], "text_quelle": QUELLE, "neu": True})))
                db_id = cur.fetchone()[0]
                cur.execute("INSERT INTO document_titles (document_id, kind, title_text, norms, embedding) VALUES (%s, 'title', %s, '{}', %s::vector)",
                            (db_id, f"{names.get(r['slug'], r['abk'])}: {r['title']}", vec(t_emb[r["doc_id"]])))
            else:
                db_id = r["id"]
                cur.execute("INSERT INTO documents_fulltext_backup_20261008 (id, doc_id, full_text, cls) SELECT id, doc_id, full_text, %s FROM documents WHERE id = %s ON CONFLICT DO NOTHING",
                            (r["cls"], db_id))
                cur.execute("UPDATE documents SET full_text = %s, metadata = coalesce(metadata, '{}'::jsonb) || %s WHERE id = %s",
                            (r["text"], Json({"text_quelle": QUELLE, "vorher": r["cls"]}), db_id))
                cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (db_id,))
            execute_values(cur, "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding) VALUES %s",
                           [(db_id, i, x, vec(emb[k + i])) for i, x in enumerate(c)], template="(%s, %s, %s, %s::vector)")
            k += len(c)
        conn.commit()
        n_docs += len(part)
        n_chunks += len(flat)
        el = (time.time() - t0) / 60
        print(f"  {n_docs}/{len(todo)} документов, {n_chunks:,} фрагментов, {n_chunks / max(el, 0.01):.0f} в минуту", flush=True)
    model.stop_multi_process_pool(pool)
    conn.close()
    print(f"LAWS_NEU_DONE: {n_docs} документов, {n_chunks:,} фрагментов за {(time.time() - t0) / 60:.1f} мин", flush=True)


if __name__ == "__main__":
    main()
