#!/usr/bin/env python3
"""
load_laws_kleb.py -- поправка к load_laws_neu.py (08.10.2026): при чтении официального XML текст, стоящий после перечня или абзаца
(«Schlusssatz»), приклеивался к последнему слову перечня («Zeitpunktabgegeben», AO § 152). prepare_laws_neu.text_of исправлен;
здесь тексты, прочитанные с ошибкой, заменяются заново, фрагменты и векторы пересчитываются (обе карты).
Список -- de_law_corpus/prepared/laws_kleb.json ({id, text}), его готовит сверка повторным чтением XML.
ЗАПУСКАТЬ ТОЛЬКО С РАЗРЕШЕНИЯ СЕРГЕЯ (пишет в базу и считает векторы).
    python3 -u load_laws_kleb.py
"""
import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import json
import time

import psycopg2
from psycopg2.extras import execute_values


def vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def main():
    from sentence_transformers import SentenceTransformer
    from rebuild_structured_gpu import _laws_chunker
    conn = psycopg2.connect(dbname="steuer_corpus_test", user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), host=os.environ.get("PGHOST", "localhost"))
    cur = conn.cursor()
    todo = json.load(open("de_law_corpus/prepared/laws_kleb.json", encoding="utf-8"))
    cur.execute("SELECT id FROM documents WHERE source = 'laws' AND metadata->>'kleb_fix' = '1'")
    done = {r[0] for r in cur.fetchall()}
    todo = [r for r in todo if r["id"] not in done]
    print(f"к обработке: {len(todo)}", flush=True)
    if not todo:
        return
    ch = _laws_chunker()
    model = SentenceTransformer("BAAI/bge-m3", device="cuda:0", model_kwargs={"use_safetensors": True})
    pool = model.start_multi_process_pool(["cuda:0", "cuda:1"])
    t0, n_docs, n_chunks = time.time(), 0, 0
    for s in range(0, len(todo), 300):
        part = todo[s:s + 300]
        cs = [ch.merge_small(ch.chunk(r["text"])) or [r["text"]] for r in part]
        flat = [c for x in cs for c in x]
        emb = model.encode(flat, pool=pool, batch_size=64, convert_to_numpy=True, normalize_embeddings=True)
        k = 0
        for r, c in zip(part, cs):
            cur.execute("""UPDATE documents SET full_text = %s, metadata = coalesce(metadata, '{}'::jsonb) || '{"kleb_fix": "1"}'::jsonb WHERE id = %s""", (r["text"], r["id"]))
            cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (r["id"],))
            execute_values(cur, "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding) VALUES %s",
                           [(r["id"], i, x, vec(emb[k + i])) for i, x in enumerate(c)], template="(%s, %s, %s, %s::vector)")
            k += len(c)
        conn.commit()
        n_docs += len(part)
        n_chunks += len(flat)
        print(f"  {n_docs}/{len(todo)} документов, {n_chunks:,} фрагментов, {n_chunks / max((time.time() - t0) / 60, 0.01):.0f} в минуту", flush=True)
    model.stop_multi_process_pool(pool)
    conn.close()
    print(f"LAWS_KLEB_DONE: {n_docs} документов, {n_chunks:,} фрагментов за {(time.time() - t0) / 60:.1f} мин", flush=True)


if __name__ == "__main__":
    main()
