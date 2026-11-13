#!/usr/bin/env python3
"""kopf_eigen_probe.py (24.10.2026, владелец проекта: «реквизиты — в заголовок чанка, в самом чанке — только его текст»).
Проба, только чтение: каждый фрагмент раскладывается на
  kopf  — реквизиты: путь (pfad_text) + вводная пункта (всё до последнего « … ») + подписи столбцов таблицы («подпись: »);
  eigen — собственный текст (слова исходника);
и eigen ищется в исходнике (после общей чистки saeubern) ПОДРЯД: место q_von–q_bis (номера слов исходника).
Проверка документа по местам:
  luecke    — слова исходника между кусками, которых нет ни в одном куске и ни в заголовках пути (потеря текста);
  nicht_gef — кусок, чей собственный текст не найден в исходнике на своём месте (искажение или чужой текст);
  rueck     — кусок найден раньше предыдущего (перестановка/повтор).
Метки страниц и номера (одиночные числа), служебные метки rn/rz/tz в промежутках допустимы.
Результат: kopf_eigen_probe_<дата>.tsv (документ, источник, слов, покрыто, luecke, nicht_gef, rueck, прошёл ли по старой отметке).
    python3 quellen_neu/pruefung5/kopf_eigen_probe.py [N_на_источник]
"""
import difflib
import re
import sys
from collections import defaultdict
from multiprocessing import Pool

import psycopg2

sys.path.insert(0, ".")
from struktur_text import saeubern

W = re.compile(r"[A-Za-zÄÖÜäöüß]{2,}|\d+|[§%€]")
TRENN = re.compile(r"(?<=[A-Za-zÄÖÜäöüß])-\s+(?!(?:und|oder|bzw|sowie|bis|als|noch|wie|u|o|beziehungsweise)\b)(?=[a-zäöüß])")
MARK = {"rn", "rz", "tz", "rdnr"}
LABEL_ZELLE = re.compile(r"(?:(?<=^)|(?<=\| ))[^|:]{1,400}?: ")      # «подпись: значение» в строке таблицы

_cur = None


def toks(s):
    return W.findall(TRENN.sub("", s or "").lower())


def zerlegen(text_roh, pfad):
    """-> (kopf, eigen)"""
    segs = (text_roh or "").split(" … ")
    vor, eigen = segs[:-1], segs[-1]
    labels = []
    if re.search(r"› (Zeile|Zeilen)\b", pfad or ""):     # 24.10: подписи столбцов — только у строк таблиц (не «Im Menü: …», «Posten: …»)
        labels = [m.group(0) for m in LABEL_ZELLE.finditer(eigen)]
        eigen = LABEL_ZELLE.sub("", eigen)
        # объединённая ячейка (colspan) повторена на каждый столбец — в исходнике она одна: подряд одинаковые ячейки — один раз
        zellen = eigen.split(" | ")
        eigen = " | ".join(z for k, z in enumerate(zellen) if k == 0 or z.strip() != zellen[k - 1].strip())
    kopf = " › ".join(x for x in [pfad or ""] + vor if x) + ((" [" + " ".join(labels) + "]") if labels else "")
    return kopf, eigen


def finden(q, e, ab, fenster):
    """e (список слов) подряд в q, начиная с позиции ab (не дальше ab+fenster); -> позиция или -1"""
    if not e:
        return ab
    n = len(e)
    stop = min(len(q) - n, ab + fenster)
    i = ab
    while i <= stop:
        try:
            i = q.index(e[0], i, stop + 1)
        except ValueError:
            return -1
        if q[i:i + n] == e:
            return i
        i += 1
    return -1


def eins(d):
    """сравнение ПО БУКВАМ без пробелов: собственный текст куска — буквально подряд в исходнике (пробелы и разбиение на слова
    в PDF не считаются: «gestaltungsmöglich keiten» = «gestaltungsmöglichkeiten»)"""
    global _cur
    if _cur is None:
        con = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost"); con.autocommit = True
        _cur = con.cursor()
    cur = _cur
    cur.execute("SELECT d.source, d.full_text, p.bestanden FROM documents d LEFT JOIN dok_pruefung p ON p.document_id = d.id WHERE d.id = %s", (d,))
    src, ft, best = cur.fetchone()
    cur.execute("SELECT text_roh, pfad_text FROM struktur_fragmente WHERE document_id = %s "
                "AND art NOT IN ('para','abs','para_lang','abs_lang','blatt_geteilt') ORDER BY ord, id", (d,))
    rows = cur.fetchall()
    q = [x for x in toks(saeubern(ft or "")) if x not in MARK]
    S = "".join(q)
    wort_von = []                                   # буква -> номер слова исходника
    for k, x in enumerate(q):
        wort_von.extend([k] * len(x))
    gedeckt = [False] * len(S)
    kopf_woerter = set()
    pos, abw, rueck = 0, 0, 0
    beispiel = ""
    for t, pf in rows:
        kopf, eigen = zerlegen(t, pf)
        kopf_woerter.update(toks(kopf))
        E = "".join(x for x in toks(eigen) if x not in MARK)
        if not E:
            continue
        i = S.find(E, pos, pos + 20000 + len(E))
        if i < 0:
            i = S.find(E)
            if 0 <= i < pos:
                rueck += 1
        if i >= 0:
            for k in range(i, i + len(E)):
                gedeckt[k] = True
            pos = max(pos, i + len(E))
            continue
        # не буквально: якорь 30 букв (начало/середина/конец) и выравнивание по буквам на месте
        j = -1
        for a in (0, len(E) // 2, max(0, len(E) - 30)):
            j = S.find(E[a:a + 30], max(0, pos - 200), pos + 20000 + len(E))
            if j >= 0:
                j = max(0, j - a); break
        if j < 0:
            abw += len(E)
            if not beispiel:
                beispiel = "nicht gefunden: " + E[:60]
            continue
        fen = S[j:j + len(E) + 200]
        bl = [b for b in difflib.SequenceMatcher(None, E, fen, autojunk=False).get_matching_blocks() if b.size]
        abw += len(E) - sum(b.size for b in bl)
        if not beispiel and bl:
            k = next((b.a + b.size for b in bl if b.a + b.size < len(E)), 0)
            beispiel = "abw bei: " + E[max(0, k - 25):k + 25]
        for b in bl:
            for k in range(j + b.b, j + b.b + b.size):
                gedeckt[k] = True
        if bl:
            pos = max(pos, j + bl[-1].b + bl[-1].size)
    # потеря: слова исходника, не покрытые ни одной буквой куска, кроме чисел, меток и слов реквизитов (путь, вводная, подписи)
    offen = sorted({wort_von[k] for k in range(len(S)) if not gedeckt[k]})
    luecke = [q[w] for w in offen if not q[w].isdigit() and q[w] not in kopf_woerter and q[w] not in "§%€"]
    return d, src, len(q), len(q) - len(offen), len(luecke), abw, rueck, best, " ".join(luecke[:12]), beispiel


if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    cur = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost").cursor()
    cur.execute("""SELECT document_id FROM (SELECT p.document_id, p.source, p.bestanden,
                   row_number() OVER (PARTITION BY p.source, p.bestanden ORDER BY md5(p.document_id::text)) r FROM dok_pruefung p
                   WHERE EXISTS (SELECT 1 FROM struktur_fragmente f WHERE f.document_id = p.document_id)) x WHERE r <= %s""", (N,))
    ids = [x[0] for x in cur.fetchall()]
    print("документов", len(ids), flush=True)
    with Pool(16) as p:
        res = list(p.imap_unordered(eins, ids, chunksize=4))
    out = "./quellen_neu/pruefung5/kopf_eigen_probe_2026-10-24.tsv"
    with open(out, "w") as f:
        f.write("doc\tsource\twoerter\tgedeckt\tluecke\tnicht_gef\trueck\tbestanden_alt\tluecke_bsp\tnicht_gef_bsp\n")
        for r in sorted(res, key=lambda x: (x[1], x[0])):
            f.write("\t".join(str(x) for x in r) + "\n")
    st = defaultdict(lambda: [0, 0, 0, 0, 0])
    for d, s, n, g, lu, ng, ru, best, *_ in res:
        ok = lu == 0 and ng == 0 and ru == 0
        k = "alt_bestanden" if best else "alt_nicht"
        st[k][0] += 1; st[k][1] += ok
        st[k][2] += lu > 0; st[k][3] += ng > 0; st[k][4] += ru > 0
    for k, v in st.items():
        print(f"{k}: документов {v[0]}, чисто по местам {v[1]}, с потерей {v[2]}, с расхождением букв {v[3]}, с перестановкой {v[4]}")
    print(out)
