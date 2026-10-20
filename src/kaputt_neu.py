#!/usr/bin/env python3
"""kaputt_neu.py (19.10.2026, Сергей «1а»): документы с испорченным текстом (QUALITAETSTEST_RAG_2026-10-19, дефект D1) -> текст заново.
У этих PDF шрифт без таблицы знаков: текстовый слой сдвинут («'HXWVFKHU» = «Deutscher», цифры -> управляющие знаки, умлауты пропадают).
Список: quellen_neu/kaputt_schrift_docs.csv (id, doc_id, source, url, origin, испорченных фрагментов, всего фрагментов);
заново -- документы с >= 10 испорченными фрагментами или долей >= 20 % и с адресом PDF (остальные только исключаются из выдачи поиска).
По страницам: текстовый слой годен (нет управляющих знаков, слова из словаря) -- берётся он, иначе страница распознаётся tesseract (deu, 300 dpi).
bgbl: документ -- страницы выпуска от своей до начала следующей публикации того же выпуска (как в базе).
Скачивание: пауза 3 с; robots.txt у dserver.bundestag.de нет (404), у media.offenegesetze.de -- проверяется.
Прежние тексты -> quellen_neu/kaputt_alt_backup.jsonl (до удаления в цепочке).
    python3 -u kaputt_neu.py [--max N]  -> de_law_corpus/prepared/kaputt_neu.jsonl
    python3 -u kaputt_neu.py --nur id1,id2 --tag 2  -> kaputt_neu2.jsonl, quellen_neu/kaputt_alt_backup2.jsonl (дозапуск)
"""
import csv
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import psycopg2
import pymupdf

D = Path("quellen_neu/kaputt_pdf")
UA = {"User-Agent": "LeckerMath-Forschung"}
STEUER = re.compile(r"[\x01-\x08\x0e-\x1f]")
EXTRA = ["btdrs_17_10000"]   # сдвиг букв без управляющих знаков (поиск «GHU»+«XQG» 19.10.2026)
W = set(gzip.open("de_wortliste_gesetz.txt.gz", "rt", encoding="utf-8").read().lower().split("\n"))


def seite_gut(t):
    if len(STEUER.findall(t)) > 3:
        return False
    ws = re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", t)
    return len(ws) < 20 or sum(w.lower() in W for w in ws) / len(ws) >= 0.5


def ocr(args):
    pdf, nr = args
    cache = Path(f"{pdf}.{nr}.ocr.txt")                  # распознанная страница -- на диск (перезапуск не повторяет работу)
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    doc = pymupdf.open(pdf)
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        doc[nr].get_pixmap(dpi=300).save(f.name)
        t = subprocess.run(["tesseract", f.name, "-", "-l", "deu", "--psm", "3"], capture_output=True, text=True).stdout
    cache.write_text(t, encoding="utf-8")
    return t


def laden(url):
    D.mkdir(parents=True, exist_ok=True)
    dst = D / re.sub(r"[^\w.-]+", "_", url.split("#")[0].split("?")[0].split("//", 1)[1])[-150:]
    if not dst.name.lower().endswith(".pdf"):
        dst = dst.with_name(dst.name + ".pdf")
    if not dst.exists():
        with urllib.request.urlopen(urllib.request.Request(url.split("#")[0], headers=UA), timeout=180) as r:
            dst.write_bytes(r.read())
        time.sleep(3)
    return dst


def main():
    maxn = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 10 ** 6
    # 19.10.2026: большие документы при загрузке разрезаны на части «<doc_id>_pNNN» с одним PDF -- заменяется документ целиком
    # (все части): текст PDF один раз, загрузчик режет его заново; прежние части удаляются цепочкой (поле «ersetzt»)
    tag = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else ""
    basis = lambda d: re.sub(r"_p\d{3}$", "", d)                     # noqa: E731
    alle = list(csv.reader(open("quellen_neu/kaputt_schrift_docs.csv")))
    bases = list(dict.fromkeys(basis(r[1]) for r in alle if (int(r[5]) >= 10 or int(r[5]) / int(r[6]) >= 0.2) and r[3].startswith("http")))
    bases += [b for b in EXTRA if b not in bases]
    if "--nur" in sys.argv:
        bases = sys.argv[sys.argv.index("--nur") + 1].split(",")
    con = psycopg2.connect(host=os.environ.get("PGHOST", "localhost"), user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), dbname="steuer_corpus_test")
    cur = con.cursor()
    out = open(f"de_law_corpus/prepared/kaputt_neu{tag}.jsonl", "w", encoding="utf-8")
    bak = open(f"quellen_neu/kaputt_alt_backup{tag}.jsonl", "w", encoding="utf-8")
    pool = ProcessPoolExecutor(12)                       # 36 ядер, рядом другие расчёты -- 12 процессов распознавания
    n_ocr = n_dok = 0
    for doc_id in bases[:maxn]:
        cur.execute("SELECT id, doc_id, source, title, url, full_text, metadata, valid_from, valid_until, effective_date, reference_number FROM documents "
                    "WHERE doc_id=%s OR doc_id ~ %s ORDER BY doc_id", (doc_id, "^" + re.escape(doc_id) + "_p[0-9]{3}$"))
        teile = cur.fetchall()
        if not teile:
            continue
        alt = teile[0][1:]
        src, url = alt[1], alt[3]
        try:
            pdf = laden(url)
            doc = pymupdf.open(pdf)
        except Exception as e:                           # noqa: BLE001
            print("[!] не скачан/не открыт", doc_id, str(e)[:60], flush=True)
            continue
        von, bis = 0, len(doc)
        m = re.search(r"#page=(\d+)", url)
        if m:                                            # bgbl: до начала следующей публикации того же выпуска
            von = int(m.group(1)) - 1
            cur.execute("SELECT url FROM documents WHERE source=%s AND url LIKE %s", (src, url.split("#")[0] + "#page=%"))
            starts = sorted(int(re.search(r"#page=(\d+)", u).group(1)) - 1 for (u,) in cur.fetchall())
            bis = next((s for s in starts if s > von), len(doc))
        texte = {i: doc[i].get_text() for i in range(von, bis)}
        schlecht = [i for i, t in texte.items() if not seite_gut(t)]
        for i, t in zip(schlecht, pool.map(ocr, [(str(pdf), i) for i in schlecht])):
            texte[i] = t
        n_ocr += len(schlecht)
        text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(texte[i].strip() for i in range(von, bis))).strip()
        # 19.10.2026: порог относительный -- в документах по 3000 страниц на годных страницах встречаются одиночные знаки (1-3 на страницу)
        if len(text) < 200 or len(STEUER.findall(text)) > max(20, len(text) // 10000):
            print("[!] результат негоден", doc_id, flush=True)
            continue
        text = STEUER.sub("", text)                         # одиночные управляющие знаки -- убрать
        for t_ in teile:
            bak.write(json.dumps({"id": t_[0], "doc_id": t_[1], "source": t_[2], "title": t_[3], "url": t_[4], "full_text": t_[5], "metadata": t_[6]},
                                 ensure_ascii=False, default=str) + "\n")
        meta = alt[5] or {}
        out.write(json.dumps({"doc_id": doc_id, "source": alt[1], "tier": meta.get("tier", 1), "title": re.sub(r" — Teil \d+$", "", alt[2]), "text": text,
                              "ersetzt": [t_[1] for t_ in teile],
                              "origin": alt[3] if str(alt[3]).startswith("http") else meta.get("origin", "N/A"),
                              "valid_from": str(alt[6]) if alt[6] else None, "valid_until": str(alt[7]) if alt[7] else None,
                              "effective_date": str(alt[8]) if alt[8] else None, "reference_number": alt[9],
                              "law_state_year": meta.get("law_state_year"), "neu_erkannt": f"{len(schlecht)} von {bis - von} Seiten (19.10.2026)"},
                             ensure_ascii=False) + "\n")
        out.flush()
        bak.flush()
        n_dok += 1
        print(f"{n_dok}/{len(bases)} {doc_id} (частей было {len(teile)}): страниц {bis - von}, распознано {len(schlecht)}, знаков {len(text)}", flush=True)
    print(f"KAPUTT_NEU_DONE: документов {n_dok} из {len(bases)}, страниц распознано {n_ocr}", flush=True)


if __name__ == "__main__":
    main()
