#!/usr/bin/env python3
"""vektor_umbau.py (20.10.2026, владелец проекта: «тогда 2 делай» -- три правки с пересчётом векторов, только затронутые фрагменты, не вся база).
  titel     -- заголовки законов, одинаковые внутри одного закона (Artikelgesetze: «HStruktG § 2» в Art 1 и в Art 3): статья/раздел
               из официального XML gesetze-im-internet.de (quellen_roh/gii_aktuell/2026-10-08/<каталог>.zip, по порядку норм) ->
               documents.title «HStruktG Art 3 § 2», заголовок-фрагмент «<Langtitel>: HStruktG Art 3 (Bundesbeamtengesetz) § 2» + вектор.
  sperrsatz -- текст «в разрядку» («A b s c h n i t t 1», «D e r B u n d e s m i n i s t e r») во фрагментах всех источников
               склеивается тем же правилом, что в anonymizer.vorbereiten (пробел перед заглавной -- граница слова) + вектор.
  paragraf  -- фрагменты законов получают приставку «<Закон> § N: » (у прежних редакций «… (frühere Fassung): »), чтобы фрагмент
               из середины длинного параграфа знал, чей он + вектор. --quelle laws | laws_historie.
Резерв: прежний текст каждого изменённого фрагмента/заголовка -- таблица umbau_backup_20261020 (id, art, alt); повторный запуск
пропускает уже сделанное (продолжение после сбоя). Векторы -- emb_sicher.Pool на обеих картах; только под сторожем.
    python3 -u vektor_umbau.py titel | sperrsatz | paragraf --quelle laws [--probe]   (--probe: без записи, примеры)
"""
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

import psycopg2

con = psycopg2.connect(host="localhost", user="postgres", dbname="steuer_corpus_test")
cur = con.cursor()
PROBE = "--probe" in sys.argv
BATCH = 2000
cur.execute("CREATE TABLE IF NOT EXISTS umbau_backup_20261020 (id integer, art text, alt text, PRIMARY KEY (id, art))")
con.commit()

_pool = None


def emb(texts):
    global _pool
    if _pool is None:
        from emb_sicher import Pool
        _pool = Pool(["cuda:0", "cuda:1"])
    return _pool.encode(texts, int(os.environ.get("EMB_BATCH", "64")))


def vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# ---------- titel ----------
_XML = {}


def xml_info(slug):
    """(jurabk, ausfertigung-datum, langue, [(enbez, gliederungsbez, gliederungstitel)] по порядку норм) из XML закона"""
    if slug not in _XML:
        z = zipfile.ZipFile(f"quellen_roh/gii_aktuell/2026-10-08/{slug}.zip")
        x = ET.fromstring(z.read([n for n in z.namelist() if n.endswith(".xml")][0]))
        titel, out = {}, []
        for n in x.iter("norm"):
            md = n.find("metadaten")
            g = md.find("gliederungseinheit")
            bez = (g.findtext("gliederungsbez") or "").strip() if g is not None else ""
            gt = re.sub(r"\s+", " ", g.findtext("gliederungstitel") or "").strip() if g is not None else ""
            if gt:
                titel[bez] = gt
            eb = (md.findtext("enbez") or "").strip()
            if eb:
                out.append((re.sub(r"\s+", " ", eb), bez, titel.get(bez, "")))
        k = x.find(".//metadaten")
        _XML[slug] = ((k.findtext("jurabk") or "").strip(), (k.findtext("ausfertigung-datum") or "").strip(),
                      re.sub(r"\s+", " ", k.findtext("langue") or "").strip(), out)
    return _XML[slug]


def _slug(u):
    m = re.match(r"https?://www\.gesetze-im-internet\.de/([^/]+)/", u or "")
    return m.group(1) if m and os.path.exists(f"quellen_roh/gii_aktuell/2026-10-08/{m.group(1)}.zip") else None


def titel():
    """две причины одинаковых заголовков: (1) разные законы с одним названием (объявления о монетах, распоряжения разных лет) --
    различаются сокращением из XML («Münz10EuroBek 2002-03») или датой; (2) один Artikelgesetz -- статья из XML («HStruktG Art 3 § 2»)"""
    cur.execute("""SELECT d.id, d.doc_id, d.title, d.url, t.title_text FROM documents d JOIN document_titles t ON t.document_id = d.id AND t.kind = 'title'
                   WHERE d.source = 'laws' AND t.title_text IN (SELECT t2.title_text FROM documents d2 JOIN document_titles t2 ON t2.document_id = d2.id
                   AND t2.kind = 'title' WHERE d2.source = 'laws' GROUP BY 1 HAVING count(*) > 1)
                   AND d.id NOT IN (SELECT id FROM umbau_backup_20261020 WHERE art = 'titel')""")
    rows = cur.fetchall()
    slug_key = {}
    for i, d, t, u, tt in rows:
        if _slug(u):
            slug_key.setdefault(d.split("__")[0], _slug(u))
    gruppen = {}
    for r in rows:
        gruppen.setdefault(r[4], []).append(r)
    neu, ohne = [], 0
    nr = lambda d: int(re.search(r"_(\d{3})_[^_]*$", d).group(1)) if re.search(r"_(\d{3})_[^_]*$", d) else 0      # noqa: E731
    for tt0, rs in gruppen.items():
        keys = {r[1].split("__")[0] for r in rs}
        seen = {}
        for i, doc_id, t, u, tt in sorted(rs, key=lambda r: (r[1].split("__")[0], nr(r[1]))):
            key = doc_id.split("__")[0]
            slug = _slug(u) or slug_key.get(key)
            m = re.match(r"^(.*?) ((?:§|Art)\s*\d+\w*|Eingangsformel|Schlußformel|Schlussformel|Anlage\b.*?|Inhaltsübersicht)(.*)$", t)
            if not slug or not m:
                ohne += 1
                continue
            jur, eb, rest = m.groups()
            xjur, datum, langue, norms = xml_info(slug)
            nj, zusatz, lt = jur, "", None
            if len(keys) > 1:                                  # разные законы
                nj = xjur if xjur and xjur != jur else f"{jur} ({datum})"
                lt = langue or None
            same = [r for r in rs if r[1].split("__")[0] == key]
            if len(same) > 1:                                  # внутри одного закона -- статья
                kand = [n for n in norms if n[0] == re.sub(r"\s+", " ", eb)]
                k = seen.get((key, eb), 0)
                seen[(key, eb)] = k + 1
                if k < len(kand) and kand[k][1] and kand[k][1] != "-":
                    zusatz = kand[k][1]
                    gt = kand[k][2]
            if nj == jur and not zusatz:
                ohne += 1
                continue
            nt = f"{nj} {zusatz + ' ' if zusatz else ''}{eb}{rest}"
            kopf, _, _ = tt.partition(": ")
            ntt = f"{lt or kopf}: {nj} {zusatz + (' (' + gt[:120] + ')' if gt else '') + ' ' if zusatz else ''}{eb}{rest}"
            neu.append((i, t, tt, nt, ntt))
    rest_dubl = len(neu) - len({x[4] for x in neu})
    print(f"заголовков к исправлению: {len(neu)}; не различить по XML: {ohne}; после правки всё ещё совпадают: {rest_dubl}", flush=True)
    for x in neu[::max(1, len(neu) // 10)][:10]:
        print("   ", x[1][:60], "->", x[3][:70], "|", x[4][:110], flush=True)
    if PROBE or not neu:
        return
    e = emb([x[4] for x in neu])
    for (i, t, tt, nt, ntt), v in zip(neu, e):
        cur.execute("INSERT INTO umbau_backup_20261020 VALUES (%s, 'titel', %s) ON CONFLICT DO NOTHING", (i, t + "\n" + tt))
        cur.execute("UPDATE documents SET title = %s WHERE id = %s", (nt[:500], i))
        cur.execute("UPDATE document_titles SET title_text = %s, embedding = %s::vector WHERE document_id = %s AND kind = 'title'", (ntt, vec(v), i))
    con.commit()
    print(f"UMBAU_TITEL_DONE: {len(neu)}", flush=True)


# ---------- sperrsatz ----------
SPERR = re.compile(r"(?<![\w.])(?:[A-Za-zÄÖÜäöüß] ){3,}[A-Za-zÄÖÜäöüß](?![\w])")


# 20.10.2026 (владелец проекта: «придётся проводить анализ таких длинных слов… база должна быть вылизана до блеска»): строчные слова в разрядке
# без заглавной на границе склеиваются («p s y c h o s o m a t i s c h e u n d …») -> разбиение по частотам слов самой базы
# (wortfrequenz_bauen.py, de_wortfrequenz_basis.tsv.gz), минимальная сумма -log(частоты); разбиение принимается, только если склейки
# нет в словаре и среди частей есть служебное слово -- составные слова («grundsteuerreformgesetz») не режутся.
_F, _N = {}, 1
FUNK = set("der die das den dem des ein eine einer eines einem einen und oder sowie von vom zu zum zur im in am an auf aus bei mit nach "
           "über unter vor für gegen durch ohne bis als wie auch nicht ist sind wird werden wurde hat haben sich es er sie wir ihr "
           "so dass daß nach soweit sofern wenn ob".split())


def _wf():
    global _N
    if not _F:
        import gzip
        for l in gzip.open("de_wortfrequenz_basis.tsv.gz", "rt", encoding="utf-8"):
            w, k = l.rstrip("\n").split("\t")
            _F[w] = int(k)
        _N = sum(_F.values())
    return _F


def segment(w):
    import math
    F = _wf()
    s0 = w.lower()
    if len(s0) < 6 or s0 in F:
        return w
    n, maxl = len(s0), 40
    best, back = [0.0] + [math.inf] * n, [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - maxl), j):
            k = F.get(s0[i:j])
            if k and (j - i > 1) and best[i] - math.log(k / _N) < best[j]:
                best[j], back[j] = best[i] - math.log(k / _N), i
    if best[n] == math.inf:
        return w
    teile, j = [], n
    while j > 0:
        teile.append(w[back[j]:j])
        j = back[j]
    teile.reverse()
    # части, кроме служебных слов, -- не короче 4 букв и не реже 10 раз в базе (иначе режутся фамилии: «Faltlhauser» -> «Fa ltl haus er»)
    gut = all(t.lower() in FUNK or (len(t) >= 4 and F.get(t.lower(), 0) >= 10) for t in teile)
    # повод разбить -- служебное слово от 3 букв (der, und, von, für, zum); «er», «in» сами по себе -- нет («Systemplanerin»)
    return " ".join(teile) if len(teile) > 1 and gut and any(t.lower() in FUNK and len(t) >= 3 for t in teile) else w


def sperr_fix(t):
    def lauf(m):
        woerter = re.sub(r" (?=[A-ZÄÖÜ])", "  ", m.group(0)).replace("  ", "\x00").replace(" ", "").split("\x00")
        return " ".join(segment(x) for x in woerter)
    return SPERR.sub(lauf, t)


def sperrsatz2():
    """уже свёрнутые первым правилом (резерв art='sperrsatz') -- заново из прежнего текста с разбиением склеек"""
    cur.execute("""SELECT b.id, b.alt, c.chunk_text FROM umbau_backup_20261020 b JOIN document_chunks c ON c.id = b.id
                   WHERE b.art = 'sperrsatz' AND b.id NOT IN (SELECT id FROM umbau_backup_20261020 WHERE art = 'sperrsatz2')""")
    rows = cur.fetchall()
    neu = [(i, akt, sperr_fix(alt)) for i, alt, akt in rows]
    neu = [x for x in neu if x[2] != x[1]]
    print(f"sperrsatz2: из {len(rows)} свёрнутых меняется {len(neu)}", flush=True)
    for x in neu[:5]:
        d = [w for w in x[2].split() if w not in set(x[1].split())][:6]
        print("    новое:", d, flush=True)
    if PROBE:
        return
    for s in range(0, len(neu), BATCH):
        part = neu[s:s + BATCH]
        e = emb([x[2] for x in part])
        for (i, akt, nt), v in zip(part, e):
            cur.execute("INSERT INTO umbau_backup_20261020 VALUES (%s, 'sperrsatz2', %s) ON CONFLICT DO NOTHING", (i, akt))
            cur.execute("UPDATE document_chunks SET chunk_text = %s, embedding = %s::vector WHERE id = %s", (nt, vec(v), i))
        con.commit()
        print(f"  sperrsatz2: изменено {s + len(part)}", flush=True)
    print(f"UMBAU_SPERRSATZ2_DONE: {len(neu)}", flush=True)


def bearbeiten(art, sql, args, umbau):
    """фрагменты по запросу (id, текст) пачками: новый текст + вектор; резерв прежнего текста"""
    last, n_all = 0, 0
    while True:
        cur.execute(sql + " AND c.id > %s AND c.id NOT IN (SELECT id FROM umbau_backup_20261020 WHERE art = %s) ORDER BY c.id LIMIT %s",
                    args + (last, art, BATCH * 5))
        rows = cur.fetchall()
        if not rows:
            break
        last = rows[-1][0]
        neu = [(i, t, umbau(i, t)) for i, t in rows]
        neu = [x for x in neu if x[2] and x[2] != x[1]]
        if PROBE:
            for x in neu[:6]:
                print("   ", repr(x[1][:120]), "\n -> ", repr(x[2][:120]), flush=True)
            print(f"пример: из {len(rows)} изменилось бы {len(neu)}", flush=True)
            return
        for s in range(0, len(neu), BATCH):
            part = neu[s:s + BATCH]
            e = emb([x[2] for x in part])
            for (i, t, nt), v in zip(part, e):
                cur.execute("INSERT INTO umbau_backup_20261020 VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (i, art, t))
                cur.execute("UPDATE document_chunks SET chunk_text = %s, embedding = %s::vector WHERE id = %s", (nt, vec(v), i))
            con.commit()
            n_all += len(part)
            print(f"  {art}: фрагментов изменено {n_all} (до id {part[-1][0]})", flush=True)
    print(f"UMBAU_{art.upper()}_DONE: {n_all}", flush=True)


def sperrsatz():
    bearbeiten("sperrsatz", "SELECT c.id, c.chunk_text FROM document_chunks c WHERE c.chunk_text ~ %s",
               (r"(^|[^[:alnum:].])([[:alpha:]] ){3,}[[:alpha:]]([^[:alnum:]]|$)",), lambda i, t: sperr_fix(t))


# ---------- paragraf ----------
def paragraf():
    q = sys.argv[sys.argv.index("--quelle") + 1]
    cur.execute("SELECT id, title FROM documents WHERE source = %s", (q,))
    kurz = {}
    for i, t in cur.fetchall():
        m = re.match(r"^(.*?(?:§|Art)\s*\d+\w*)", t)
        k = m.group(1) if m else re.split(r" - | \(frühere Fassung", t)[0][:80]
        kurz[i] = k + (" (frühere Fassung)" if q == "laws_historie" else "")
    pref = lambda d, t: None if t.startswith(kurz[d] + ":") else f"{kurz[d]}: {t}"      # noqa: E731
    cur.execute("SELECT c.id, c.document_id FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE d.source = %s", (q,))
    dok = dict(cur.fetchall())
    bearbeiten(f"paragraf_{q}", "SELECT c.id, c.chunk_text FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE d.source = %s",
               (q,), lambda i, t: pref(dok[i], t))


if __name__ == "__main__":
    {"titel": titel, "sperrsatz": sperrsatz, "sperrsatz2": sperrsatz2, "paragraf": paragraf}[sys.argv[1]]()
    if _pool is not None:
        _pool.close()
