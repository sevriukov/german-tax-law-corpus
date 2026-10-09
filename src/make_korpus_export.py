#!/usr/bin/env python3
"""
make_korpus_export.py -- Ausfuhr des offenen Teils der Arbeitsdatenbank in Parquet-Dateien.

Nur lesend; schreibt in einen eigenen Ordner, sendet nichts. Quellen, die nicht weitergegeben werden dürfen, stehen in einer
lokalen Datei (eine Quelle je Zeile, Standard: ~/.config/leckermath/nicht_oeffentlich.txt) und werden vollständig ausgelassen:
weder Texte noch Kopfabschnitte noch Vektoren. Quellen mit nicht geprüften Bedingungen bleiben enthalten und sind gekennzeichnet
(Spalten `rights_note`, `significance`).

  python3 make_korpus_export.py                  # Texte: documents, chunks, titles, SOURCES.md
  python3 make_korpus_export.py --vektoren       # Vektoren (BAAI/bge-m3, float16) als eigene Dateien
  ... --out <Ordner>   --nur <Quelle>[,<Quelle>]
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq

DSN = os.environ.get("LM_DSN", "host=localhost port=5432 dbname=steuer_corpus_test user=postgres")
_nf = Path("~/.config/leckermath/nicht_oeffentlich.txt").expanduser()
NICHT = tuple(z.strip() for z in _nf.read_text(encoding="utf-8").split("\n") if z.strip() and not z.startswith("#")) if _nf.exists() else ()
# источник -> (откуда, основание или условия, пометка значимости)
KERN, ERG = "Referenz", "ergänzend"
QUELLEN = {
    "laws": ("gesetze-im-internet.de (amtliches XML)", "amtlicher Text, § 5 Abs. 1 UrhG", KERN),
    "laws_historie": ("frühere Fassungen des amtlichen XML aus dem Internet Archive", "amtlicher Text, § 5 Abs. 1 UrhG; Bedingungen der Fundstelle nicht geprüft", KERN),
    "bfh": ("CE-BFH, S. Fobbe, Zenodo, doi 10.5281/zenodo.7691841", "Entscheidungen: § 5 Abs. 1 UrhG; Sammlung CC0 1.0", KERN),
    "bverfg": ("CE-BVerfG, S. Fobbe, Zenodo, doi 10.5281/zenodo.3902658", "Entscheidungen: § 5 Abs. 1 UrhG; Sammlung CC0 1.0", KERN),
    "finanzgericht": ("harshildarji/openlegaldata (Hugging Face), abgeleitet von Open Legal Data", "Entscheidungen: § 5 Abs. 1 UrhG; Sammlung ODbL v1.0", KERN),
    "bgh_criminal": ("harshildarji/openlegaldata (Hugging Face), abgeleitet von Open Legal Data", "Entscheidungen: § 5 Abs. 1 UrhG; Sammlung ODbL v1.0", KERN),
    "bsg": ("rechtsprechung-im-internet.de", "amtlicher Text, § 5 Abs. 1 UrhG", KERN),
    "bgh": ("rechtsprechung-im-internet.de", "amtlicher Text, § 5 Abs. 1 UrhG", KERN),
    "bgh_haftung": ("rechtsprechung-im-internet.de, bundesgerichtshof.de", "amtlicher Text, § 5 Abs. 1 UrhG", KERN),
    "olg_haftung": ("Rechtsprechungsportale der Länder, openlegaldata.io", "Entscheidungen: § 5 Abs. 1 UrhG; Bedingungen der Fundstellen nicht geprüft", KERN),
    "lsg_beitrag": ("openlegaldata.io", "Entscheidungen: § 5 Abs. 1 UrhG; Bedingungen der Fundstelle nicht geprüft", KERN),
    "eugh": ("eur-lex.europa.eu", "Bedingungen der Weiterverwendung von EUR-Lex nicht geprüft", KERN),
    "eu_vat": ("eur-lex.europa.eu", "Bedingungen der Weiterverwendung von EUR-Lex nicht geprüft", KERN),
    "bgbl": ("offenegesetze.de, recht.bund.de", "amtlicher Text, § 5 Abs. 1 UrhG; Bedingungen der Fundstelle nicht geprüft", KERN),
    "vwv_bund": ("verwaltungsvorschriften-im-internet.de", "amtlicher Text; Einordnung § 5 Abs. 1 oder 2 UrhG nicht geprüft", KERN),
    "bmf_handbuecher": ("Amtliche Handbücher des BMF (EStH, LStH, UStAE, AEAO u. a.)", "amtliches Werk; § 5 Abs. 2 UrhG angenommen, nicht geprüft (unverändert, mit Quelle)", KERN),
    "bmf": ("bundesfinanzministerium.de (BMF-Schreiben)", "amtliches Werk; § 5 Abs. 2 UrhG angenommen, nicht geprüft (unverändert, mit Quelle)", KERN),
    "bstbl": ("Bundessteuerblatt über fragdenstaat.de", "amtlicher Inhalt; Rechte an der Ausgabe und Bedingungen der Fundstelle nicht geprüft", ERG),
    "bundesbank": ("bundesbank.de", "Veröffentlichung der Behörde; Bedingungen nicht geprüft", ERG),
    "bafin": ("bafin.de", "Veröffentlichung der Behörde; Bedingungen nicht geprüft", ERG),
    "fiu_reports": ("zoll.de (FIU)", "Veröffentlichung der Behörde; Bedingungen nicht geprüft", ERG),
    "bmf_broschueren": ("bundesfinanzministerium.de", "Veröffentlichung der Behörde; Bedingungen nicht geprüft", ERG),
}
META_OK = ("jurabk", "ecli", "law_references", "jurisdiction", "law_state_year", "pages", "court", "aktenzeichen", "datum")   # прочее в набор не идёт
RX_STOP = [re.compile(r"/home/[a-z][\w.-]+/")]
TEIL = 150_000                                           # строк в одном файле фрагментов


def stop_muster():
    bl = Path("~/.config/leckermath/repo_blocklist.txt").expanduser()
    return RX_STOP + ([re.compile(z.strip()) for z in bl.read_text(encoding="utf-8").split("\n") if z.strip() and not z.startswith("#")] if bl.exists() else [])


def texte(con, out, quellen, bericht):
    stop = stop_muster()
    for q in quellen:
        ort, recht, stufe = QUELLEN[q]
        t0 = time.time()
        cur = con.cursor(name=f"d_{q}")
        cur.itersize = 2000
        cur.execute("""SELECT doc_id, title, url, reference_number, authority, effective_date, publication_date, valid_from, valid_until, metadata, full_text
                       FROM documents WHERE source = %s ORDER BY id""", (q,))
        rows, treffer, zeichen = [], 0, 0
        for r in cur:
            meta = {k: v for k, v in (r[9] or {}).items() if k in META_OK}
            klein = " ".join(str(x) for x in (r[0], r[1], r[2], r[3], json.dumps(meta, ensure_ascii=False)))
            if any(s.search(klein) or s.search(r[10]) for s in stop):
                treffer += 1                             # документ с запрещённым или с личным путём в набор не идёт
                continue
            zeichen += len(r[10])
            rows.append({"doc_id": r[0], "source": q, "title": r[1], "url": r[2], "reference_number": r[3], "authority": r[4],
                         "effective_date": r[5], "publication_date": r[6], "valid_from": r[7], "valid_until": r[8],
                         "metadata": json.dumps(meta, ensure_ascii=False), "rights_note": recht, "significance": stufe, "full_text": r[10]})
        cur.close()
        (out / "data" / "documents").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), out / "data" / "documents" / f"{q}.parquet", compression="zstd")
        ok = {r["doc_id"] for r in rows}
        n_doc = len(rows)
        del rows
        # заголовки документов (отдельный чанк-заголовок: закон -- название и §; суд -- карточка)
        cur = con.cursor(name=f"t_{q}")
        cur.itersize = 5000
        cur.execute("""SELECT d.doc_id, t.kind, t.title_text, t.norms FROM document_titles t JOIN documents d ON d.id = t.document_id
                       WHERE d.source = %s ORDER BY d.id""", (q,))
        tit = [{"doc_id": a, "kind": b, "title_text": c, "norms": d or []} for a, b, c, d in cur if a in ok]
        cur.close()
        (out / "data" / "titles").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(tit), out / "data" / "titles" / f"{q}.parquet", compression="zstd")
        # фрагменты
        cur = con.cursor(name=f"c_{q}")
        cur.itersize = 5000
        cur.execute("""SELECT d.doc_id, c.chunk_index, c.chunk_text FROM document_chunks c JOIN documents d ON d.id = c.document_id
                       WHERE d.source = %s ORDER BY d.id, c.chunk_index""", (q,))
        (out / "data" / "chunks").mkdir(parents=True, exist_ok=True)
        puffer, teil, n_ch = [], 0, 0
        for a, b, c in cur:
            if a not in ok:
                continue
            puffer.append({"doc_id": a, "chunk_index": b, "text": c})
            if len(puffer) >= TEIL:
                pq.write_table(pa.Table.from_pylist(puffer), out / "data" / "chunks" / f"{q}-{teil:03d}.parquet", compression="zstd")
                n_ch += len(puffer)
                puffer, teil = [], teil + 1
        if puffer:
            pq.write_table(pa.Table.from_pylist(puffer), out / "data" / "chunks" / f"{q}-{teil:03d}.parquet", compression="zstd")
            n_ch += len(puffer)
        cur.close()
        bericht[q] = {"dokumente": n_doc, "zeichen": zeichen, "fragmente": n_ch, "titel": len(tit), "ausgelassen": treffer, "sekunden": round(time.time() - t0)}
        print(f"{time.strftime('%T')} {q}: документов {n_doc}, фрагментов {n_ch}, заголовков {len(tit)}, пропущено {treffer}, {time.time() - t0:.0f} с", flush=True)


def vektoren(con, out, quellen, bericht):
    typ = pa.list_(pa.float16(), 1024)
    for tabelle, sql, ordner in (
            ("chunks", """SELECT d.doc_id, c.chunk_index, c.embedding::text FROM document_chunks c JOIN documents d ON d.id = c.document_id
                          WHERE d.source = %s AND c.embedding IS NOT NULL ORDER BY d.id, c.chunk_index""", "chunks"),
            ("titles", """SELECT d.doc_id, 0, t.embedding::text FROM document_titles t JOIN documents d ON d.id = t.document_id
                          WHERE d.source = %s AND t.embedding IS NOT NULL ORDER BY d.id""", "titles")):
        (out / "data" / "embeddings" / ordner).mkdir(parents=True, exist_ok=True)
        for q in quellen:
            dok = out / "data" / "documents" / f"{q}.parquet"
            if not dok.exists():
                print(f"{q}: сначала выгрузка текстов"); continue
            ok = set(pq.read_table(dok, columns=["doc_id"]).column("doc_id").to_pylist())
            t0 = time.time()
            cur = con.cursor(name=f"v_{tabelle}_{q}")
            cur.itersize = 2000
            cur.execute(sql, (q,))
            ids, idx, vek, teil, n = [], [], [], 0, 0

            def schreibe():
                nonlocal ids, idx, vek, teil, n
                arr = pa.FixedSizeListArray.from_arrays(pa.array(np.concatenate(vek), type=pa.float16()), 1024)
                spalten = {"doc_id": pa.array(ids)} | ({"chunk_index": pa.array(idx, type=pa.int32())} if tabelle == "chunks" else {}) | {"embedding": arr}
                pq.write_table(pa.table(spalten), out / "data" / "embeddings" / ordner / f"{q}-{teil:03d}.parquet", compression="zstd")
                n += len(ids)
                ids, idx, vek, teil = [], [], [], teil + 1
            for a, b, e in cur:
                if a not in ok:
                    continue
                ids.append(a); idx.append(b); vek.append(np.fromstring(e[1:-1], sep=",", dtype=np.float32).astype(np.float16))
                if len(ids) >= TEIL:
                    schreibe()
            if ids:
                schreibe()
            cur.close()
            bericht.setdefault(q, {})[f"vektoren_{tabelle}"] = n
            print(f"{time.strftime('%T')} векторы {tabelle} {q}: {n}, {time.time() - t0:.0f} с", flush=True)
    _ = typ


def main():
    arg = sys.argv[1:]
    out = Path(arg[arg.index("--out") + 1]).expanduser() if "--out" in arg else Path("~/Documents/leckermath-korpus").expanduser()
    out.mkdir(parents=True, exist_ok=True)
    con = psycopg2.connect(DSN)
    cur = con.cursor()
    cur.execute("SELECT source, count(*) FROM documents GROUP BY 1")
    in_db = dict(cur.fetchall())
    unbekannt = [s for s in in_db if s not in QUELLEN and s not in NICHT]
    if unbekannt:
        sys.exit(f"СТОП: источники без решения о публикации: {unbekannt} -- внесите в QUELLEN или NICHT")
    quellen = [s for s in QUELLEN if s in in_db]
    if "--nur" in arg:
        quellen = [s for s in arg[arg.index("--nur") + 1].split(",") if s in quellen]
    bf = out / "EXPORT_BERICHT.json"
    bericht = json.load(open(bf, encoding="utf-8")) if bf.exists() else {}
    (vektoren if "--vektoren" in arg else texte)(con, out, quellen, bericht)
    json.dump(bericht, open(bf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = ["# Quellen / Sources", "", f"Stand der Ausfuhr: {time.strftime('%d.%m.%Y')}. Nicht enthalten: Fachliteratur, Hilfetexte von Softwareherstellern, Lehrtexte, Mandantendaten.", "",
         "| source | Dokumente | Abschnitte | Herkunft | Rechtsgrundlage / Bedingungen | Bedeutung |", "|---|---:|---:|---|---|---|"]
    L += [f"| {q} | {bericht[q].get('dokumente', '')} | {bericht[q].get('fragmente', '')} | {QUELLEN[q][0]} | {QUELLEN[q][1]} | {QUELLEN[q][2]} |" for q in QUELLEN if q in bericht]
    L += ["", "»ergänzend« = Quelle von nachrangiger Bedeutung oder mit nicht geprüften Bedingungen; sie ist als solche gekennzeichnet (Spalten `significance`, `rights_note`).",
          f"Summe: {sum(b.get('dokumente', 0) for b in bericht.values())} Dokumente, {sum(b.get('fragmente', 0) for b in bericht.values())} Abschnitte."]
    (out / "SOURCES.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("EXPORT_DONE", "vektoren" if "--vektoren" in arg else "texte", sum(b.get("dokumente", 0) for b in bericht.values()), flush=True)


if __name__ == "__main__":
    main()
