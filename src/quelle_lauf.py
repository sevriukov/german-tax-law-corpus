#!/usr/bin/env python3
"""quelle_lauf.py (24.10.2026, владелец проекта: «реквизиты — в заголовок чанка, в самом чанке — только его текст»; «добавляй полей сколько нужно»).
Сверка ПО МЕСТАМ для всех документов (проба kopf_eigen_probe.py: 942 документа). Фрагменты только читаются; результат — новые таблицы:
  fragment_quelle (fragment_id, document_id, kopf_zusatz, eigen, q_von, q_bis, abw, status)
     kopf_zusatz — реквизиты, которые сейчас вписаны в text_roh: вводная пункта (до последнего « … ») и подписи столбцов таблицы;
     eigen — собственный текст куска; q_von/q_bis — место в исходнике (номера букв в строке исходника после общей чистки
     saeubern, без пробелов и знаков, только буквы/цифры/«§ % €», нижний регистр); abw — букв куска, не совпавших с исходником;
     status: genau (буквально на своём месте), frueher (раньше своего места: лишний дубль или перестановка — ошибка),
             wiederholt (раньше своего места, но этот текст стоит в исходнике несколько раз — не ошибка),
             fehlplatz (найден только далеко впереди, а следующие куски — в пропущенном промежутке: перестановка — ошибка),
             angeglichen (выравнивание на месте, abw > 0), nicht_gefunden.
  dok_quelle (document_id, source, version, woerter, gedeckt, luecke, abw, rueck, nicht_gef, luecke_bsp, abw_bsp)
     luecke — слова исходника, не покрытые ни одним куском (кроме чисел, меток rn/rz/tz и слов реквизитов).
Таблицы создаются заново (DROP); старые данные не меняются.
    python3 quellen_neu/pruefung5/quelle_lauf.py
"""
import difflib
import io
import re
import os
import sys
from multiprocessing import Pool

import psycopg2

# 24.10 ночь: служебные слова в промежутках между кусками — не текст: заголовки разделов решений, колонтитул Drucksachen.
# Вместе с римскими номерами разделов (ii, iii) и масками обезличивания (xx, eee) — как метки rn/rz/tz.
SERVICE = {"tatbestand", "gründe", "entscheidungsgründe", "verfahrensgang", "tenor", "leitsatz", "leitsätze", "orientierungssatz",
           "rubrum", "drucksache", "deutscher", "bundestag", "wahlperiode"}

sys.path.insert(0, "./quellen_neu/pruefung5")
sys.path.insert(0, ".")
from kopf_eigen_probe import MARK, toks, zerlegen
from struktur_text import saeubern

VERSION = "quelle-2026-10-24i"   # d: подписи столбцов отделяются только у строк таблиц
OUT = "./quellen_neu/pruefung5/quelle_lauf_tmp"
_cur = None
ZEILEN_NR = re.compile(r"(?m)^[ \t]*\d{1,4}[ \t]*$")      # «39» отдельной строкой (juris: номер у каждой строки) — метка, не текст


def esc(x):
    return r"\N" if x is None else str(x).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def eins(d):
    global _cur
    if _cur is None:
        con = psycopg2.connect(dbname=os.environ.get("DB", "steuer_corpus_test"), user="postgres", host="localhost"); con.autocommit = True
        _cur = con.cursor()
    cur = _cur
    cur.execute("SELECT source, full_text FROM documents WHERE id = %s", (d,))
    src, ft = cur.fetchone()
    if os.environ.get("ORD_ALT"):                   # сравнение: прежний порядок кусков из резерва sf_ord_backup_20261024
        cur.execute("SELECT s.id, s.text_roh, s.pfad_text, s.vorlauf, s.version FROM struktur_fragmente s LEFT JOIN sf_ord_backup_20261024 b ON b.id = s.id "
                    "WHERE s.document_id = %s AND s.art NOT IN ('para','abs','para_lang','abs_lang','blatt_geteilt') "
                    "ORDER BY coalesce(b.ord, s.ord), s.id", (d,))
    else:
        cur.execute("SELECT id, text_roh, pfad_text, vorlauf, version FROM struktur_fragmente WHERE document_id = %s "
                    "AND art NOT IN ('para','abs','para_lang','abs_lang','blatt_geteilt') ORDER BY ord, id", (d,))
    rows = cur.fetchall()
    q = [x for x in toks(ZEILEN_NR.sub(" ", saeubern(ft or ""))) if x not in MARK]   # 24.10 f: номер отдельной строкой — метка Rn
    S = "".join(q)
    wort_von = []
    for k, x in enumerate(q):
        wort_von.extend([k] * len(x))
    gedeckt = bytearray(len(S))
    kopf_woerter = set()
    pos, abw_sum, rueck, nicht = 0, 0, 0, 0
    abw_bsp = ""
    frag = []
    zaehl = {}
    # 24.10 h (владелец проекта: таблица — отдельный тип документа): табличная часть проверяется по табличному слою tab_zeilen
    cur.execute("SELECT z.zellen FROM tab_katalog k JOIN tab_zeilen z USING (tab_id) WHERE k.document_id = %s ORDER BY k.tab_id, z.zeile", (d,))
    tab_zeilen = [z for (z,) in cur.fetchall()]
    cur.execute("SELECT string_agg(concat_ws(' ', titel, tabellentitel, kopf, enbez, bezug), ' ') FROM tab_katalog WHERE document_id = %s", (d,))
    tab_kopf = set(toks(cur.fetchone()[0] or ""))     # название и шапка таблицы — реквизиты
    zerlegt = []
    bekannt = set()                                 # 24.10 e: « … » — разделитель вводной, только если этот текст уже был в документе;
    for fid, t, pf, vl, ver in rows:                # иначе это многоточие самого исходника («Kennzeichen … zu», «(4) bis (7) …»)
        if (ver or "").endswith("24v"):             # 24.10 v: новое устройство — вводная в поле vorlauf, текст не угадывается
            kopf, eigen = zerlegen((t or "").replace(" … ", " \u2063 "), pf)   # « … » здесь — многоточие исходника, не разделитель
            kopf, eigen = kopf.replace(" \u2063 ", " … "), eigen.replace(" \u2063 ", " … ")
            kopf = " › ".join(x for x in [pf or "", vl or ""] if x) + kopf[len(pf or ""):]
            zerlegt.append((fid, pf, kopf, eigen, "".join(x for x in toks(eigen) if x not in MARK)))
            continue
        teile = (t or "").split(" … ")
        k = 0
        while k < len(teile) - 1 and " ".join(teile[k].split()) in bekannt:
            k += 1
        for x in teile[:-1]:
            bekannt.add(" ".join(x.split()))
        bekannt.add(" ".join(teile[-1].split()))
        t = " … ".join(teile[:k] + [" … ".join(teile[k:])]) if k < len(teile) - 1 else t
        kopf, eigen = zerlegen(t, pf)
        if k < len(teile) - 1:                      # непризнанные части — собственный текст
            vor = teile[:k]
            kopf, eigen = (" › ".join(x for x in [pf or ""] + vor if x), " … ".join(teile[k:]))
        zerlegt.append((fid, pf, kopf, eigen, "".join(x for x in toks(eigen) if x not in MARK)))
    # 24.10 g: строка-шапка таблицы (ячейки без «подпись: значение», и каждая ячейка — подпись в следующей строке) — реквизиты, не текст
    for n in range(len(zerlegt) - 1):
        fid, pf, kopf, eigen, E = zerlegt[n]
        if re.search(r"› Zeilen? \d+$", pf or "") and ": " not in (eigen or "") and " | " in (eigen or ""):
            nxt = rows[n + 1][1] or ""
            zellen = [z.strip() for z in eigen.split(" | ") if z.strip()]
            if zellen and all((z + ":") in nxt for z in zellen):
                zerlegt[n] = (fid, pf, kopf + " [" + eigen + "]", "", "")
    for n, (fid, pf, kopf, eigen, E) in enumerate(zerlegt):
        zusatz = kopf[len(pf or ""):].lstrip(" ›") or None
        if tab_zeilen and re.search(r"› Zeilen? \d+", pf or ""):
            kopf_woerter.update(toks(kopf))            # табличный кусок: таблица проверяется по tab_zeilen (ниже)
            frag.append((fid, d, zusatz, eigen, None, None, 0, "tabelle"))
            continue
        kopf_woerter.update(toks(kopf))
        if not E:
            frag.append((fid, d, zusatz, eigen, None, None, 0, "leer"))
            continue
        i = S.find(E, pos)                          # 24.10 b: без предела окна — дальше по исходнику
        status = "genau"
        if i > pos + 3000:
            # 24.10 c: далёкий прыжок — ложный, если следующие куски (до 3, длиной от 40 букв) стоят в пропущенном промежутке:
            # тогда кусок не на своём месте (перестановка), указатель не двигается
            nach = [z[4] for z in zerlegt[n + 1:n + 8] if len(z[4]) >= 40][:3]
            if any(S.find(x, pos, i) >= 0 for x in nach):
                rueck += 1
                frag.append((fid, d, zusatz, eigen, i, i + len(E), 0, "fehlplatz"))
                continue
        if i < 0:
            i = S.find(E)
            if 0 <= i < pos:
                # раньше своего места: ошибка, только если кусков с этим текстом больше, чем вхождений в исходнике (лишний дубль),
                # или текст в исходнике один (перестановка); текст, стоящий в исходнике дважды (оглавление и тело), — не ошибка
                zaehl[E] = zaehl.get(E, 0) + 1
                if S.count(E) <= 1 or zaehl[E] > S.count(E):
                    rueck += 1; status = "frueher"
                else:
                    status = "wiederholt"
        if i >= 0:
            gedeckt[i:i + len(E)] = b"\x01" * len(E)
            pos = max(pos, i + len(E))
            frag.append((fid, d, zusatz, eigen, i, i + len(E), 0, status))
            continue
        j = -1
        for a in (0, len(E) // 2, max(0, len(E) - 30)):
            j = S.find(E[a:a + 30], max(0, pos - 200), pos + 20000 + len(E))
            if j >= 0:
                j = max(0, j - a); break
        if j < 0:
            nicht += 1; abw_sum += len(E)
            if not abw_bsp:
                abw_bsp = "nicht gefunden: " + E[:60]
            frag.append((fid, d, zusatz, eigen, None, None, len(E), "nicht_gefunden"))
            continue
        fen = S[j:j + len(E) + 200]
        bl = [b for b in difflib.SequenceMatcher(None, E, fen, autojunk=False).get_matching_blocks() if b.size]
        abw = len(E) - sum(b.size for b in bl)
        abw_sum += abw
        if abw and not abw_bsp and bl:
            k = next((b.a + b.size for b in bl if b.a + b.size < len(E)), 0)
            abw_bsp = "abw bei: " + E[max(0, k - 25):k + 25]
        for b in bl:
            gedeckt[j + b.b:j + b.b + b.size] = b"\x01" * b.size
        ende = j + bl[-1].b + bl[-1].size if bl else j
        if bl:
            pos = max(pos, ende)
        frag.append((fid, d, zusatz, eigen, j, ende, abw, "angeglichen"))
    tpos = 0
    for zellen in tab_zeilen:                      # значения строки по порядку (без номера строки отдельной ячейкой — он метка)
        E = "".join(x for x in toks(" ".join(str(v[1]) for v in zellen if not re.fullmatch(r"\s*\d{1,4}\s*", str(v[1])))) if x not in MARK)
        if not E:
            continue
        i = S.find(E, tpos)
        if i < 0:
            i = S.find(E)
        if i < 0:
            abw_sum += len(E)
            if not abw_bsp:
                abw_bsp = "tabelle nicht gefunden: " + E[:60]
            continue
        gedeckt[i:i + len(E)] = b"\x01" * len(E)
        tpos = i + len(E)
    offen = sorted({wort_von[k] for k in range(len(S)) if not gedeckt[k]})
    luecke = [q[w] for w in offen if not q[w].isdigit() and q[w] not in kopf_woerter and q[w] not in tab_kopf and q[w] not in "§%€" and not re.fullmatch(r"x{2,}", q[w]) and not re.fullmatch(r"([a-zäöü])\1{1,}|[ivx]{1,5}", q[w]) and q[w] not in SERVICE]   # «XX» — маска обезличивания, не текст
    dok = (d, src, VERSION, len(q), len(q) - len(offen), len(luecke), abw_sum, rueck, nicht, " ".join(luecke[:12]), abw_bsp)
    # запись — в файл процесса (потом COPY одним шагом)
    pid = os.getpid()
    with open(f"{OUT}/frag_{pid}.tsv", "a") as f:
        f.write("".join("\t".join(esc(x) for x in r) + "\n" for r in frag))
    with open(f"{OUT}/dok_{pid}.tsv", "a") as f:
        f.write("\t".join(esc(x) for x in dok) + "\n")
    return 1


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for x in os.listdir(OUT):
        os.remove(os.path.join(OUT, x))
    con = psycopg2.connect(dbname=os.environ.get("DB", "steuer_corpus_test"), user="postgres", host="localhost"); cur = con.cursor()
    cur.execute("SELECT DISTINCT document_id FROM struktur_fragmente")
    ids = [x[0] for x in cur.fetchall()]
    print(VERSION, "документов", len(ids), flush=True)
    with Pool(int(os.environ.get("PROZ", "24"))) as p:
        for k, _ in enumerate(p.imap_unordered(eins, ids, chunksize=16)):
            if k % 25000 == 0:
                print(k, flush=True)
    cur.execute("""DROP TABLE IF EXISTS fragment_quelle; DROP TABLE IF EXISTS dok_quelle;
        CREATE TABLE fragment_quelle (fragment_id bigint PRIMARY KEY, document_id integer, kopf_zusatz text, eigen text, q_von integer, q_bis integer,
                                      abw integer, status text);
        CREATE TABLE dok_quelle (document_id integer PRIMARY KEY, source text, version text, woerter integer, gedeckt integer, luecke integer,
                                 abw integer, rueck integer, nicht_gef integer, luecke_bsp text, abw_bsp text, geprueft timestamptz DEFAULT now());""")
    for x in sorted(os.listdir(OUT)):
        tab = "fragment_quelle" if x.startswith("frag_") else "dok_quelle (document_id, source, version, woerter, gedeckt, luecke, abw, rueck, nicht_gef, luecke_bsp, abw_bsp)"
        with open(os.path.join(OUT, x)) as f:
            cur.copy_expert(f"COPY {tab} FROM STDIN", f)
    cur.execute("CREATE INDEX ON fragment_quelle (document_id)")
    con.commit()
    cur.execute("SELECT count(*), count(*) FILTER (WHERE luecke = 0 AND abw = 0 AND rueck = 0) FROM dok_quelle")
    print("QUELLE_DONE", cur.fetchone(), flush=True)
