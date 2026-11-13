#!/usr/bin/env python3
"""struktur_gerichte.py (22.10.2026) — нарезка решений судов по структуре (запрет владельца проекта: без резки по знакам).
Разделы по заголовкам (LEITSATZ, TENOR, TATBESTAND, ENTSCHEIDUNGSGRÜNDE, GRÜNDE, «#### Tenor» …); внутри — по нумерации изложения
(«I.», «1.», «a)», «aa)», «Rn. 12», «[12]») и абзацам; длинная единица -> предложения (struktur_laws.fein). Путь: заголовок документа
(суд, Az, дата) › раздел › пункт. Проверка покрытия слов. Только чтение базы; результат — файл.
    python3 struktur_gerichte.py bfh finanzgericht …
"""
import json
import os
import re
import sys
from collections import Counter
from multiprocessing import Pool

import psycopg2

from struktur_laws import fein
from struktur_text import DATUM, VORLAUF, einl_aus, fein_tief, fortsetzung, kleine_zusammen, saeubern

W = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
KOPF = re.compile(r"^\s*(?:#{1,6}\s*)?(LEITS[ÄA]TZE?|TENOR|TATBESTAND|ENTSCHEIDUNGSGR[ÜU]NDE|GR[ÜU]NDE|Leits[äa]tze?|Tenor|Tatbestand|"
                  r"Entscheidungsgr[üu]nde|Gr[üu]nde|Orientierungssatz|ORIENTIERUNGSSATZ|Sachverhalt|Rechtliche Würdigung|Verfahrensgang|"
                  r"VERFAHRENSGANG|Rubrum|RUBRUM|Gründe und Entscheidung|Kostenentscheidung|Schlüsselwörter|Parteien)\s*:?\s*$")
MD_KOPF = re.compile(r"^#{1,6}\s+(.{2,80})$")                      # прочие заголовки разметки («### Verfahrensgang» и т. п.)
GLIED = re.compile(r"(?:(?<=^)|(?<=\s))((?:[IVX]{1,5}|\d{1,2}|[a-z]{1,2}|[a-z]{2})[.)])\s+(?=[A-ZÄÖÜ„(])")
NUR_GLIED = re.compile(r"(?:[IVX]{1,5}|[A-H]|\d{1,2}|[a-z]{1,3})[.)]")
RN = re.compile(r"^\s*(?:Rn\.?|Rz\.?|Randnummer)?\s*\[?(\d{1,3})\]?\s*$")
RN_MEHR = re.compile(r"^(\d{1,3})(?:\s+\d{1,3})+$")                  # «15 16» — пустые Rn. подряд
RN_HINTEN = re.compile(r"\s{3,}(\d{1,3})\s*$")                     # BVerfG: «… liegen nicht vor.            1»
SEITE = re.compile(r"^(?:\d{1,3}\s*/\s*\d{1,3}|-{3,}|\*{3,}|_{3,})$")  # «1/3», «---» — разметка страниц, без слов
RUBRUM_ENDE = re.compile(r"(?:(?:beschlossen|erkannt|entschieden|beschließt|beschlossen worden|für Recht erkannt)\s*:|^(?:folgendes Urteil|folgenden Beschluss)\s*:?)\s*$")
RUBRUM_ANFANG = re.compile(r"^(?:Im Namen des Volkes|In dem Verfahren|In den Verfahren|In der Rechtssache|In den verbundenen Rechtssachen|"
                           r"In der Verwaltungsrechtssache|In dem Rechtsstreit|In der Strafsache|In der Sache|In der Steuersache)\b")
SATZENDE = re.compile(r"[.:;!?“\")\]]\s*$")
EBENE = [("A", re.compile(r"[A-H]\.")), ("I", re.compile(r"[IVX]{1,5}\.")), ("1", re.compile(r"\d{1,2}[.)]")),
         ("a", re.compile(r"[a-z][.)]")), ("aa", re.compile(r"([a-z])\1\)")), ("aaa", re.compile(r"([a-z])\1\1\)"))]


# строка кончается сокращением внутри ссылки — следующая строка её продолжает, даже через пустые строки и номер страницы
VERWEIS_ENDE = re.compile(r"(?:\b(?:Rn|Rz|Tz|Rdnr|Randnr|Nr|Nrn|Abs|Art|S|Satz|Ziff|Buchst|Bl|Az)\.?|§§?|[-–(])\s*$")   # запятая — не ссылка («Der Beklagte beantragt,» ⏎ «19»)
ABK_ENDE = re.compile(r"(?:\b(?:Rn|Rz|Tz|Rdnr|Abs|Nr|Art|S|Bl|Az|Aufl|Slg|ABl|Abl|BStBl|BGBl|vgl|Urt|Beschl|Hrsg|Fn|Anm)\.|[-–]|\bvom|\bzu)\s*$")

def ebene(marke):
    marke = re.sub(r" \(Rn\. \d+\)$", "", marke)        # «II. (Rn. 8)» — уровень по самой метке
    for k, (_, rx) in enumerate(EBENE):
        if rx.fullmatch(marke):
            return k
    return None


class DefNr(str):
    """строка-номер, за которой в исходнике шла строка-определение «:   …» (номер абзаца решения)"""


def roh_zeilen(text):
    """чистка (struktur_text) + разметка: «:   » (список определений), «**…**» — снимаются"""
    t = re.sub(r"(?<=[.:;])\s*(?=(?:[IVX]{1,5})\.\s*[A-ZÄÖÜ])", "\n", text or "")   # BFH: склеенный текст — границы перед «I.», «II.»
    # BVerfG: перенос перед номером Rn. в конце строки — «Verfassungs-      5» ⏎ «beschwerde …» -> «Verfassungsbeschwerde …      5»
    t = re.sub(r"([a-zäöüß])-[ \t]{3,}(\d{1,3})[ \t]*\n(?:[ \t]*\n|[ \t]*\d{1,3}[ \t]*/[ \t]*\d{1,3}[ \t]*\n){0,6}[ \t]*([a-zäöüß][^\n]*)", r"\1\3     \2", t)
    out = []
    for l in saeubern(t).splitlines():
        if re.match(r"^\s*:\s{2,}\S", l):                   # 29.10: строка-определение — номер перед ней (через пустые) — номер абзаца
            k = next((k for k in range(len(out) - 1, -1, -1) if out[k].strip()), None)
            if k is not None and re.fullmatch(r"\d{1,3}", out[k].strip()):
                out[k] = DefNr(out[k])
        l = re.sub(r"^\s*:\s{2,}", "", l)
        l = re.sub(r"\*\*(.+?)\*\*", r"\1", l).replace("**", "")
        s = l.strip()
        if s == ":":                                        # пустое определение разметки
            l = s = ""
        if re.fullmatch(r"(?:[A-Za-zÄÖÜäöüß] ){3,}[A-Za-zÄÖÜäöüß]\s*:?", s):   # «G r ü n d e :» — заголовок вразрядку
            l = s = s.replace(" ", "").replace(":", " :").rstrip()
        # EuGH (текст из HTML): ссылки и сноски отдельными строками — «(» ⏎ «C‑24/15» ⏎ «,» ⏎ «Rn.» ⏎ «39» ⏎ «ff.),» — к своей строке
        if out and out[-1].strip() and s and (re.match(r"[,.;:)\]]", s) or re.search(r"[(‑]$", out[-1])):
            out[-1] = out[-1].rstrip() + ("" if re.match(r"[,.;:)\]]", s) or out[-1].endswith("(") else " ") + s
            continue
        out.append(l.rstrip())
    return out


def einheiten(text):
    """23.10 (шаг 2): шапка решения (Rubrum — до «… beschlossen:» / «… für Recht erkannt:» или до первого раздела) — одной единицей;
    заголовки-метки («I.», «2.», «### Verfahrensgang») — в путь следующей единицы, не отдельными пустыми листьями; номер Rn. в конце
    строки (BVerfG) — метка своего абзаца; разметка страниц («1/3») — убирается; дата («14. April 2010») и строка-продолжение из PDF —
    не пункт изложения. Путь: раздел › I. 2. b) › Rn. n."""
    lines = roh_zeilen(text)
    out, abschnitt, cur, rn, gl, vor, letzte_rn = [], "", [], None, [], "", 0
    n_out_kopf = -1

    def push():
        if cur:
            label = " › ".join(x for x in (abschnitt, " ".join(gl), f"Rn. {rn}" if rn else "") if x)
            out.append((label, " ".join(cur)))
    # шапка решения: документ начинается не с раздела — строки до конца шапки (не дальше 80 непустых строк)
    k0 = 0
    nicht_leer = [k for k, l in enumerate(lines) if l.strip()]
    if nicht_leer and not KOPF.match(lines[nicht_leer[0]].strip(" :")):
        for n, k in enumerate(nicht_leer[:80]):
            s = lines[k].strip()
            if KOPF.match(s.strip(" :")) or RN.match(s) or RN_HINTEN.search(lines[k]):
                k0 = k
                break
            if RUBRUM_ENDE.search(s):
                k0 = k + 1
                break
        if k0:
            kopf = [lines[k].strip() for k in range(k0) if lines[k].strip() and not SEITE.match(lines[k].strip())]
            if kopf:
                out.append(("Rubrum", " ".join(kopf)))
            abschnitt = "Tenor" if RUBRUM_ENDE.search(lines[k0 - 1]) else ""
    j, luecke, rubrum = k0 - 1, False, bool(k0)
    while j + 1 < len(lines):
        j += 1
        roh = lines[j]
        l = roh.strip(" :") if KOPF.match(roh.strip(" :")) else roh.strip()
        if not l:
            luecke = True
            continue
        if SEITE.match(l):
            continue
        if luecke:                                          # пустая строка — конец абзаца, кроме разрыва посреди фразы (PDF):
            luecke = False                                  # «… damit auseinander» ⏎⏎ «zu setzen, …»
            # 23.10 ночь: и перенос страницы посреди ссылки — «… juris, Rn.» ⏎⏎ «9/16» ⏎⏎ «30). Dazu …», «8. Aufl.» ⏎⏎ «2017, S. 531»
            abk_ende = bool(cur and ABK_ENDE.search(cur[-1]))
            if not (cur and (abk_ende or not SATZENDE.search(cur[-1]) and l[:1].islower()) and not GLIED.match(l)):
                push(); cur = []; vor = ""
        if not rubrum and RUBRUM_ANFANG.match(l):           # шапка после «Leitsätze»: «In dem Verfahren …» до «… beschlossen:»
            ende, n = None, 0
            for k in range(j, len(lines)):
                s = lines[k].strip()
                if not s:
                    continue
                n += 1
                if n > 80 or (k > j and KOPF.match(s.strip(" :"))):
                    break
                if RUBRUM_ENDE.search(s):
                    ende = k
                    break
            if ende is not None:
                push(); cur = []
                out.append(("Rubrum", " ".join(x.strip() for x in lines[j:ende + 1] if x.strip() and not SEITE.match(x.strip()))))
                rubrum, abschnitt, gl, rn, vor, j = True, "Tenor", [], None, "", ende
                continue
        m = KOPF.match(l) or MD_KOPF.match(l)
        if m:
            kopf_rn = rn if rn is not None and not cur else None   # 23.10: «1» ⏎ «Tatbestand:» — номер абзаца стоит на заголовке, не терять
            push(); cur = []; rn = None; gl = []
            # 23.10: раздел без текста («Tenor» ⏎ «Verfahrensgang», перечень «Leitsätze / Entscheidungsgründe / Tenor») — его заголовок
            # остаётся в метке следующего, иначе пропадает
            leer_davor = abschnitt if abschnitt and len(out) == n_out_kopf else ""
            abschnitt = m.group(1).strip(" :#").capitalize() if KOPF.match(l) else m.group(1).strip(" :#")
            if leer_davor:
                abschnitt = f"{leer_davor} / {abschnitt}"
            n_out_kopf = len(out)
            if kopf_rn is not None:
                abschnitt += f" (Rn. {kopf_rn})"
            vor = ""
            continue
        r = RN.match(l) or RN_MEHR.match(l)
        # Rn. растёт; не «Rn.» ⏎ «39» внутри ссылки. 24.10: строго следующий номер принимается и после заголовка со строчной на конце
        # («Gemeinschaftsrecht» ⏎ «3»), если строка перед ним не кончается ссылкой («Rn.», «Nr.», «Abs.», «§»)
        # 29.10: «… Art.» ⏎⏎ «22» ⏎ «:   Dazu gehören …» — строка-определение («:» после номера) — номер абзаца, не ссылка «Art. 22»
        # (сверка staging: Rn. 21 и 22 склеились, 72 решения sozialgericht и др. «было чисто -> расхождение»)
        dl = isinstance(roh, DefNr)
        if r and int(r.group(1)) > letzte_rn and (dl or not fortsetzung(vor) or (int(r.group(1)) == letzte_rn + 1 and not VERWEIS_ENDE.search(vor))):
            push(); cur = []; rn = l.split()[-1].strip("[]") if RN_MEHR.match(l) else r.group(1); letzte_rn = int(rn)
            if abschnitt == "Tenor":                     # 24.10: в Tenor номеров Rn. не бывает — первый Rn. открывает Gründe (BVerfG, BGH)
                abschnitt, gl = "Gründe", []
            continue
        h = RN_HINTEN.search(roh)
        if h and letzte_rn < int(h.group(1)) <= letzte_rn + 5:   # номер абзаца в конце его первой строки — по порядку (пропуски до 4)
            push(); cur = []; rn = h.group(1); letzte_rn = int(rn)
            if abschnitt == "Tenor":
                abschnitt, gl = "Gründe", []
            l = roh[:h.start()].strip()
        if NUR_GLIED.fullmatch(l):                          # строка из одной метки — уровень изложения для следующего текста
            e = ebene(l)
            if e is not None:
                # 24.10: «8» ⏎ «II.» ⏎ «9» — номер стоит на заголовке пункта (за заголовком сразу следующий номер);
                # «9» ⏎ «1.» ⏎ «Nach § 66 …» — номер у абзаца, «1.» лишь его метка
                nxt = next((x.strip() for x in lines[j + 1:j + 6] if x.strip() and not SEITE.match(x.strip())), "")
                glied_rn = rn if rn is not None and not cur and (RN.match(nxt) or NUR_GLIED.fullmatch(nxt)) else None
                if glied_rn is not None:
                    rn = None
                push(); cur = []
                gl = [g for g in gl if ebene(g) is not None and ebene(g) < e] + [l + (f" (Rn. {glied_rn})" if glied_rn is not None else "")]
                vor = ""
                continue
        g = GLIED.match(l) if not DATUM.match(l) and not fortsetzung(vor) else None
        if g:
            e = ebene(g.group(1))
            if cur or e is not None:
                push(); cur = []
            if e is not None:
                gl = [x for x in gl if ebene(x) is not None and ebene(x) < e] + [g.group(1)]
        cur.append(l)
        vor = l
    push()
    return [(lab, x) for lab, x in out if x.strip()]


def einleitungen(units):
    """вводная фраза («Die Beklagte beantragt,» / «… wie folgt:») — в начало следующих пунктов и строк со строчной буквы
    (в juris-тексте они стоят под отдельным Rn.)"""
    out, intro = [], None
    for l, t in units:
        punkt = bool(GLIED.match(t)) or t[:1].islower()
        if intro and punkt:
            out.append((l, intro + VORLAUF + t))   # 24.10: вводная — отдельным полем
        else:
            out.append((l, t))
            intro = None
        e = t.rstrip()
        # 24.10: пункт перечня («a) Verwarnung») — не вводная для соседнего пункта; вводная остаётся прежней
        if (not SATZENDE.search(e) or e.endswith(":")) and not (punkt and intro):   # «… beantragt,» — фраза продолжается в пунктах
            intro = einl_aus(e)
    return out


def doc(row):
    i, src, title, text = row
    units = kleine_zusammen(fein_tief(einleitungen(einheiten(text)), fein)) or [("", " ".join(saeubern(text).split()))]
    kopf = (title or "").strip()   # 28.10: без обрезки по знакам (владелец проекта 22.10 / Main 28.10: заголовок целиком)
    frs = [{"document_id": i, "pfad": kopf + (f" › {l}" if l else ""), "text": t} for l, t in units]
    # заголовки разделов и метки изложения — в пути, не в тексте: покрытие считается по тексту + путям
    ohne_kopf = "\n".join(l for l in roh_zeilen(text) if not KOPF.match(l.strip(" :")) and not MD_KOPF.match(l.strip()))
    wb = Counter(W.findall(ohne_kopf))
    wf = Counter(W.findall(" ".join(t for _, t in units) + " " + " ".join(l for l, _ in units)))
    return i, frs, sum((wb - wf).values())


def main():
    q = sys.argv[1:]
    cur = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost").cursor()
    # 23.10 (владелец проекта): NUR_NICHT_BESTANDEN=1 — резать только документы, не прошедшие проверку (dok_pruefung.bestanden = false);
# 24.10: AUCH_IDS=1,2,3 — и эти (исходник заменён после проверки: старая отметка устарела)
    cur.execute("SELECT id, source, title, full_text FROM documents WHERE source = ANY(%s)" + ((" AND id IN (" + ",".join(x.strip() for x in open(os.environ["NUR_IDS"]) if x.strip()) + ")") if os.environ.get("NUR_IDS") else (" AND (id NOT IN (SELECT document_id FROM dok_pruefung WHERE bestanden) OR id IN (" + os.environ.get("AUCH_IDS", "0") + "))" if os.environ.get("NUR_NICHT_BESTANDEN") else "")), (q,))   # 23.10: и прежние редакции
    rows = cur.fetchall()
    st = Counter()
    with Pool(24) as p, open(f"de_law_corpus/prepared/struktur_fragmente_{'_'.join(q)}{'_nachschnitt' if os.environ.get('NUR_NICHT_BESTANDEN') or os.environ.get('NUR_IDS') else ''}.jsonl", "w", encoding="utf-8") as f:
        for i, frs, fehl in p.imap_unordered(doc, rows, chunksize=50):
            st["docs"] += 1
            st["fragmente"] += len(frs)
            st["verlust_docs"] += fehl > 3
            for x in frs:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("STRUKTUR_GERICHTE", dict(st), flush=True)


if __name__ == "__main__":
    main()
