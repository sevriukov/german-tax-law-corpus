#!/usr/bin/env python3
"""rag_qualitaetstest.py -- программа проверки качества RAG-базы (19.10.2026, поручение владельца проекта: «все стандартные проверки —
целостность, правильность заполнения, короткие и пустые чанки, обрывы чанков, несовпадение с именем и заголовком»).
Дополняет pruefung_basis.py (тот считает пустые/огромные тексты, даты, кодировку, дубли документов). Только чтение, только CPU.
Разделы: A целостность, B заполнение и заголовки, C чанки (пустые, короткие, обрывы), D качество текста, E индексы,
F самопоиск (вектор чанка, вектор заголовка, полнотекст) -- без GPU, по уже сохранённым векторам.
Возобновляемый: готовые проверки берутся из quellen_neu/rag_qualitaetstest.json, считается только недостающее.
Каждая проверка -- с statement_timeout; тяжёлые -- по выборке (TABLESAMPLE / random), это помечено в отчёте.
    python3 -u rag_qualitaetstest.py            -> quellen_neu/rag_qualitaetstest.json, QUALITAETSTEST_RAG_<дата>.txt
"""
import datetime
import json
import os
import re
import time

import psycopg2

OUT = "quellen_neu/rag_qualitaetstest.json"
S = "TABLESAMPLE SYSTEM (2) REPEATABLE (19)"          # ~2 % страниц таблицы фрагментов, повторяемая выборка

# (раздел, ключ, описание, SQL, порог «плохо»: None -- только справка)
CHECKS = [
 ("A", "fk_chunks", "внешний ключ фрагмент -> документ объявлен",
  "SELECT count(*) FROM pg_constraint WHERE conrelid='document_chunks'::regclass AND contype='f'", None),
 ("A", "fk_titles", "внешний ключ заголовок -> документ объявлен",
  "SELECT count(*) FROM pg_constraint WHERE conrelid='document_titles'::regclass AND contype='f'", None),
 ("A", "titel_waise", "заголовки без документа",
  "SELECT count(*) FROM document_titles t WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.id=t.document_id)", 0),
 ("A", "docid_dubl", "повторяющиеся doc_id",
  "SELECT count(*) FROM (SELECT doc_id FROM documents GROUP BY 1 HAVING count(*)>1) x", 0),
 ("A", "docid_leer", "документы без doc_id",
  "SELECT count(*) FROM documents WHERE doc_id IS NULL OR doc_id=''", 0),
 ("A", "ohne_frag", "документы без фрагментов (по источникам)",
  "SELECT d.source, count(*) FROM documents d WHERE NOT EXISTS (SELECT 1 FROM document_chunks c WHERE c.document_id=d.id) GROUP BY 1 ORDER BY 2 DESC", 0),
 ("A", "index_luecken", "документы с пропусками или повторами chunk_index",
  """SELECT count(*) FILTER (WHERE n<>mx-mn+1), count(*) FILTER (WHERE n<>nd), count(*) FILTER (WHERE mn<>0) FROM
     (SELECT document_id, count(*) n, count(DISTINCT chunk_index) nd, min(chunk_index) mn, max(chunk_index) mx FROM document_chunks GROUP BY 1) x""", None),
 ("A", "emb_null", "фрагменты без вектора (по источникам)",
  "SELECT d.source, count(*) FROM document_chunks c JOIN documents d ON d.id=c.document_id WHERE c.embedding IS NULL GROUP BY 1 ORDER BY 2 DESC", 0),
 ("A", "emb_norm", f"векторы с нулевой/ненормированной длиной (выборка {S})",
  f"""SELECT count(*), count(*) FILTER (WHERE n < 0.001), count(*) FILTER (WHERE n <> n), count(*) FILTER (WHERE abs(n-1) > 0.01)
      FROM (SELECT vector_norm(embedding) n FROM document_chunks {S} WHERE embedding IS NOT NULL) x""", None),
 ("A", "emb_dubl", f"одинаковые векторы у разных текстов (выборка {S})",
  f"""SELECT count(*) FROM (SELECT embedding::text e, count(DISTINCT md5(chunk_text)) k FROM document_chunks {S}
      WHERE embedding IS NOT NULL GROUP BY 1 HAVING count(DISTINCT md5(chunk_text))>1) x""", 0),
 ("A", "titel_emb_null", "заголовки без вектора",
  "SELECT kind, count(*) FROM document_titles WHERE embedding IS NULL GROUP BY 1", 0),
 ("A", "frag_nicht_im_text", "фрагмент не найден в полном тексте документа (выборка 3000, по первым 60 знакам)",
  """SELECT d.source, count(*) FROM (SELECT * FROM document_chunks TABLESAMPLE SYSTEM (0.2) REPEATABLE (7) LIMIT 3000) c
     JOIN documents d ON d.id=c.document_id
     WHERE position(left(regexp_replace(c.chunk_text,'\\s+',' ','g'),60) in regexp_replace(d.full_text,'\\s+',' ','g'))=0 GROUP BY 1 ORDER BY 2 DESC""", None),
 ("A", "abdeckung", "покрытие текста фрагментами: сумма длин фрагментов / длина текста (доля документов)",
  """SELECT d.source, count(*), count(*) FILTER (WHERE s < 0.5*length(d.full_text)), count(*) FILTER (WHERE s > 2.0*length(d.full_text))
     FROM documents d JOIN (SELECT document_id, sum(length(chunk_text)) s FROM document_chunks GROUP BY 1) c ON c.document_id=d.id
     WHERE length(d.full_text) >= 300 GROUP BY 1 HAVING count(*) FILTER (WHERE s < 0.5*length(d.full_text)) > 0 ORDER BY 3 DESC""", None),

 ("B", "titel_leer", "документы с пустым или служебным заголовком",
  r"""SELECT source, count(*) FROM documents WHERE title IS NULL OR btrim(title)='' OR title ~* '^(none|null|untitled|unbekannt|n/a|dokument|document|\d+)$'
      OR length(btrim(title)) < 5 GROUP BY 1 ORDER BY 2 DESC""", 0),
 ("B", "titel_dubl", "одинаковый заголовок у разных документов одного источника (документов в группах)",
  "SELECT source, sum(n) FROM (SELECT source, title, count(*) n FROM documents GROUP BY 1,2 HAVING count(*)>1) x GROUP BY 1 ORDER BY 2 DESC LIMIT 25", None),
 ("B", "titel_lang", "заголовок длиннее 400 знаков",
  "SELECT source, count(*) FROM documents WHERE length(title) > 400 GROUP BY 1 ORDER BY 2 DESC", None),
 ("B", "titel_par_nicht_im_text", "законы: «§ N» из заголовка не встречается в начале текста",
  r"""SELECT source, count(*), count(*) FILTER (WHERE position(m in left(regexp_replace(full_text,'\s+',' ','g'),3000))=0) FROM
      (SELECT source, full_text, (regexp_match(title, '(§\s*\d+[a-z]?)'))[1] m FROM documents WHERE source IN ('laws','laws_historie','landesrecht')) x
      WHERE m IS NOT NULL GROUP BY 1""", None),
 ("B", "karte_ungleich_titel", "заголовок-чанк (kind=title) не содержит заголовка документа",
  """SELECT d.source, count(*) FROM document_titles t JOIN documents d ON d.id=t.document_id
     WHERE t.kind='title' AND position(lower(left(btrim(d.title),40)) in lower(t.title_text))=0 GROUP BY 1 ORDER BY 2 DESC""", None),
 ("B", "az_nicht_im_text", "суды: номер дела (reference_number) не встречается в тексте",
  """SELECT source, count(*), count(*) FILTER (WHERE position(regexp_replace(reference_number,'\\s+','','g') in regexp_replace(left(full_text,20000),'\\s+','','g'))=0)
     FROM documents WHERE reference_number IS NOT NULL AND reference_number<>'' AND source IN ('bfh','finanzgericht','bverfg','bgh','eugh','bsg','sozialgericht','gw_urteile')
     GROUP BY 1""", None),
 ("B", "quelle_ohne_tier", "источники без уровня в source_tiers",
  "SELECT d.source, count(*) FROM documents d LEFT JOIN source_tiers s ON s.source=d.source WHERE s.source IS NULL GROUP BY 1", 0),
 ("B", "url_format", "адрес не http(s)",
  "SELECT source, count(*) FROM documents WHERE url IS NOT NULL AND url<>'' AND url !~ '^https?://' GROUP BY 1 ORDER BY 2 DESC", None),
 ("B", "gueltig_vertauscht", "valid_from позже valid_until",
  "SELECT source, count(*) FROM documents WHERE valid_from > valid_until GROUP BY 1", 0),
 ("B", "gueltig_ohne_ersatz", "valid_until в прошлом (утратившие силу; справка)",
  "SELECT source, count(*) FROM documents WHERE valid_until < current_date GROUP BY 1 ORDER BY 2 DESC", None),
 ("B", "eff_vs_pub", "effective_date раньше publication_date более чем на 5 лет",
  "SELECT source, count(*) FROM documents WHERE effective_date < publication_date - interval '5 years' GROUP BY 1 ORDER BY 2 DESC", None),
 ("B", "dok_tsv_leer", "документы с пустым полнотекстовым индексом при непустом тексте",
  "SELECT source, count(*) FROM documents WHERE length(full_text)>=300 AND (full_text_tsv IS NULL OR full_text_tsv=''::tsvector) GROUP BY 1", 0),

 ("C", "frag_laenge", "фрагменты: всего, пустые, <50, <80, <200, >2000, >4000, средняя длина (по источникам)",
  r"""SELECT d.source, count(*), count(*) FILTER (WHERE btrim(c.chunk_text,E' \n\t\r')=''), count(*) FILTER (WHERE length(c.chunk_text)<50),
      count(*) FILTER (WHERE length(c.chunk_text)<80), count(*) FILTER (WHERE length(c.chunk_text)<200),
      count(*) FILTER (WHERE length(c.chunk_text)>2000), count(*) FILTER (WHERE length(c.chunk_text)>4000), round(avg(length(c.chunk_text)))
      FROM document_chunks c JOIN documents d ON d.id=c.document_id GROUP BY 1 ORDER BY 2 DESC""", None),
 ("C", "frag_nur_zeichen", "фрагменты без букв (только цифры, знаки, номера страниц)",
  r"SELECT count(*) FROM document_chunks WHERE chunk_text !~ '[A-Za-zÄÖÜäöüß]{3}'", 0),
 ("C", "frag_tsv_leer", "фрагменты с пустым полнотекстовым индексом",
  "SELECT count(*) FROM document_chunks WHERE chunk_text_tsv=''::tsvector", None),
 ("C", "frag_dubl_im_dok", "повтор одного и того же фрагмента внутри документа (лишних копий)",
  "SELECT count(*), coalesce(sum(n-1),0) FROM (SELECT document_id, md5(chunk_text), count(*) n FROM document_chunks GROUP BY 1,2 HAVING count(*)>1) x", None),
 ("C", "frag_dubl_quer", "одинаковые фрагменты в разных документах (групп / лишних копий)",
  "SELECT count(*), coalesce(sum(n-1),0) FROM (SELECT md5(chunk_text), count(DISTINCT document_id) n FROM document_chunks GROUP BY 1 HAVING count(DISTINCT document_id)>1) x", None),
 ("C", "grenzen", f"обрывы: начало со строчной/середины слова, конец без знака конца, перенос на конце (выборка {S}, по источникам)",
  rf"""SELECT d.source, count(*),
      count(*) FILTER (WHERE c.chunk_index>0 AND c.chunk_text ~ '^\s*[a-zäöüß]'),
      count(*) FILTER (WHERE c.chunk_text !~ '[.;:!?)\]"“»”…]\s*$'),
      count(*) FILTER (WHERE c.chunk_text ~ '[A-Za-zäöüß]-\s*$'),
      count(*) FILTER (WHERE c.chunk_text ~ '\m[A-Za-zÄÖÜäöüß]{{1,2}}\s*$' AND c.chunk_text !~ '\m(ab|an|am|im|in|zu|so|da|es|er|ob|um|EU|AO|EG|Nr|Abs)\s*$')
      FROM document_chunks c {S} JOIN documents d ON d.id=c.document_id GROUP BY 1 HAVING count(*)>=50 ORDER BY 2 DESC""", None),
 ("C", "grenze_naht", "шов между соседними фрагментами: конец без знака и следующий начинается со строчной (выборка документов)",
  r"""SELECT count(*), count(*) FILTER (WHERE a.chunk_text !~ '[.;:!?)\]"“»”…]\s*$' AND b.chunk_text ~ '^\s*[a-zäöüß]')
      FROM (SELECT id FROM documents TABLESAMPLE SYSTEM (1) REPEATABLE (3)) d
      JOIN document_chunks a ON a.document_id=d.id JOIN document_chunks b ON b.document_id=d.id AND b.chunk_index=a.chunk_index+1""", None),

 ("D", "mojibake_frag", f"«битая» кодировка и знак замены во фрагментах (выборка {S})",
  rf"""SELECT d.source, count(*) FROM document_chunks c {S} JOIN documents d ON d.id=c.document_id
      WHERE c.chunk_text ~ '(Ã¤|Ã¶|Ã¼|ÃŸ|Ã„|â€ž|â€œ|â€™|�)' GROUP BY 1 ORDER BY 2 DESC""", 0),
 ("D", "ocr_muell", f"доля непечатных/посторонних знаков > 10 % (выборка {S})",
  rf"""SELECT d.source, count(*) FROM document_chunks c {S} JOIN documents d ON d.id=c.document_id
      WHERE length(c.chunk_text) >= 200 AND length(regexp_replace(c.chunk_text, '[A-Za-zÄÖÜäöüßéè0-9\s.,;:!?()\[\]§%&/"„“»«''’+*=<>-]', '', 'g'))::float / length(c.chunk_text) > 0.10
      GROUP BY 1 ORDER BY 2 DESC""", None),
 ("D", "wort_zerhackt", f"«разорванные» слова (много одиночных букв через пробел, след плохого PDF) (выборка {S})",
  rf"""SELECT d.source, count(*) FROM document_chunks c {S} JOIN documents d ON d.id=c.document_id
      WHERE c.chunk_text ~ '(\m[a-zäöü] ){{6,}}' GROUP BY 1 ORDER BY 2 DESC""", None),
 ("D", "sprache", f"фрагменты без немецких служебных слов (возможно чужой язык или мусор) (выборка {S})",
  rf"""SELECT d.source, count(*) FROM document_chunks c {S} JOIN documents d ON d.id=c.document_id
      WHERE length(c.chunk_text) >= 300 AND c.chunk_text !~* '\m(der|die|das|und|ist|nicht|von|mit|für|den|des|eine?)\M' GROUP BY 1 ORDER BY 2 DESC""", None),
 ("D", "html_reste", f"остатки разметки HTML/XML во фрагментах (выборка {S})",
  rf"""SELECT d.source, count(*) FROM document_chunks c {S} JOIN documents d ON d.id=c.document_id
      WHERE c.chunk_text ~ '(<(/?)(p|div|span|br|td|tr|table|a|b|i)\M[^>]*>|&nbsp;|&amp;|&lt;|&#[0-9]+;)' GROUP BY 1 ORDER BY 2 DESC""", None),

 ("E", "indizes", "индексы: имя, метод, исправен (indisvalid), готов (indisready)",
  """SELECT c.relname, am.amname, i.indisvalid, i.indisready FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
     WHERE i.indrelid IN ('documents'::regclass,'document_chunks'::regclass,'document_titles'::regclass) ORDER BY 1""", None),
 ("E", "tabellen", "размер таблиц и мёртвые строки",
  """SELECT relname, n_live_tup, n_dead_tup, pg_size_pretty(pg_total_relation_size(relid)), last_autovacuum::date, last_autoanalyze::date
     FROM pg_stat_user_tables WHERE relname IN ('documents','document_chunks','document_titles') ORDER BY 1""", None),
]


def selbstsuche(cur, n=200):
    """F: каждый выбранный фрагмент ищется по своему же вектору через HNSW; должен вернуться он сам (топ-1) или свой документ (топ-5).
    Так же заголовки; полнотекст -- по 8 подряд идущим словам фрагмента."""
    res = {}
    cur.execute("SET hnsw.ef_search = 100")
    cur.execute(f"SELECT id, document_id FROM document_chunks TABLESAMPLE SYSTEM (0.05) REPEATABLE (11) WHERE embedding IS NOT NULL LIMIT {n}")
    probe = cur.fetchall()
    t1 = t5 = dok5 = 0
    for cid, did in probe:
        cur.execute("SELECT id, document_id FROM document_chunks ORDER BY embedding <=> (SELECT embedding FROM document_chunks WHERE id=%s) LIMIT 5", (cid,))
        r = cur.fetchall()
        t1 += bool(r) and r[0][0] == cid
        t5 += cid in [x[0] for x in r]
        dok5 += did in [x[1] for x in r]
    res["vektor_frag"] = {"proben": len(probe), "selbst_top1": t1, "selbst_top5": t5, "eigenes_dok_top5": dok5}
    cur.execute(f"SELECT document_id FROM document_titles TABLESAMPLE SYSTEM (1) REPEATABLE (5) WHERE embedding IS NOT NULL LIMIT {n}")
    probe = [x[0] for x in cur.fetchall()]
    t1 = t5 = 0
    for did in probe:
        cur.execute("SELECT document_id FROM document_titles ORDER BY embedding <=> (SELECT embedding FROM document_titles WHERE document_id=%s) LIMIT 5", (did,))
        r = [x[0] for x in cur.fetchall()]
        t1 += bool(r) and r[0] == did
        t5 += did in r
    res["vektor_titel"] = {"proben": len(probe), "selbst_top1": t1, "selbst_top5": t5}
    cur.execute(f"SELECT id, document_id, chunk_text FROM document_chunks TABLESAMPLE SYSTEM (0.05) REPEATABLE (13) WHERE length(chunk_text)>300 LIMIT {n}")
    probe = cur.fetchall()
    gef = leer = 0
    for cid, did, txt in probe:
        w = re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", txt)
        if len(w) < 12:
            leer += 1
            continue
        phrase = " ".join(w[len(w) // 2: len(w) // 2 + 8])
        cur.execute("SELECT document_id FROM document_chunks WHERE chunk_text_tsv @@ phraseto_tsquery('german', %s) LIMIT 50", (phrase,))
        gef += did in [x[0] for x in cur.fetchall()]
    res["volltext_frag"] = {"proben": len(probe) - leer, "eigenes_dok_gefunden": gef}
    return res


def main():
    stand = json.load(open(OUT)) if os.path.exists(OUT) else {}
    con = psycopg2.connect(host="localhost", port=5432, user="postgres", dbname="steuer_corpus_test")
    con.autocommit = True
    cur = con.cursor()
    cur.execute("SET statement_timeout = '15min'")
    for teil, key, txt, sql, gut in CHECKS:
        if key in stand and "fehler" not in stand[key]:
            continue
        t0 = time.time()
        try:
            cur.execute(sql)
            rows = [[str(v) if isinstance(v, (datetime.date,)) else (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v) for v in r]
                    for r in cur.fetchall()]
            stand[key] = {"teil": teil, "text": txt, "rows": rows, "sek": round(time.time() - t0, 1), "soll": gut}
        except Exception as e:                       # noqa: BLE001
            stand[key] = {"teil": teil, "text": txt, "fehler": str(e)[:200], "sek": round(time.time() - t0, 1)}
        print(f"{teil} {key}: {round(time.time() - t0)} с", flush=True)
        json.dump(stand, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    if "selbstsuche" not in stand:
        t0 = time.time()
        try:
            stand["selbstsuche"] = {"teil": "F", "text": "самопоиск по сохранённым векторам и полнотексту", "rows": selbstsuche(cur), "sek": round(time.time() - t0, 1)}
        except Exception as e:                       # noqa: BLE001
            print("selbstsuche:", str(e)[:200], flush=True)
        json.dump(stand, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print("RAG_QUALITAETSTEST_DONE", flush=True)


if __name__ == "__main__":
    main()
