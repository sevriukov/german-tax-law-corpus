#!/usr/bin/env python3
"""struktur_laws.py (22.10.2026) — нарезка законов ПО СТРУКТУРЕ официального XML (gesetze-im-internet, quellen_roh/gii_aktuell/2026-10-08).
владелец проекта 22.10: «идиотское нарезание на куски по количеству знаков ЗАПРЕЩАЮ… структура чанков — выражение семантического слоя».

Фрагмент = абзац (<P>); если в абзаце перечень (<DL>), то каждый пункт — свой фрагмент, а вводная фраза абзаца («Ordnungswidrig
handelt, wer vorsätzlich oder leichtfertig») повторяется в нём; фраза после перечня («Schlusssatz») — свой фрагмент с вводной фразой;
таблица — по строкам, с заголовками столбцов. Каждый фрагмент начинается с пути: «GwG 2017 § 56 Bußgeldvorschriften, Abs. 1 Nr. 30».
Счётчика знаков нет. Только чтение базы; результат — файл (боковая таблица — отдельным шагом).

    python3 struktur_laws.py [--probe GwG,EStG,HGB,AO,UStG,BewG,StBerG]   # проба на законах эталонов
    python3 struktur_laws.py --alle                                        # все законы
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import psycopg2

import prepare_laws_neu as P
from struktur_text import VORLAUF   # 24.10: вводная — отдельным полем

SRC = Path("quellen_roh/gii_aktuell/2026-10-08")
OUT = Path("de_law_corpus/prepared")


def flat(el, skip=("DL", "table")):
    """текст элемента без вложенных перечней/таблиц (они разбираются отдельно)"""
    parts = [el.text or ""]
    for ch in el:
        if ch.tag not in skip:
            parts.append(flat(ch, skip))
            if ch.tag == "SUP":
                parts.append(" ")
        parts.append(ch.tail or "")
    return " ".join("".join(parts).split())


def dl_items(dl):
    """пункты перечня: [(номер, текст пункта целиком — с вложенными a), b) …)]"""
    out, num = [], None
    for ch in dl:
        if ch.tag == "DT":
            num = " ".join("".join(ch.itertext()).split())
        elif ch.tag == "DD":
            out.append((num or "", P.text_of(ch).replace("\n", " ")))
    return out


TRENN_ZELLE = re.compile(r"(?<=[A-Za-zÄÖÜäöüß])-\s+(?!(?:und|oder|bzw|sowie|bis|als|noch|wie|u|o)\b)(?=[a-zäöüß])")


def _zelle(e):
    from struktur_text import trennung                  # общее правило переносов (сложные прилагательные — с дефисом)
    return trennung(" ".join("".join(e.itertext()).split()))


def tabelle_daten(tb):
    """24.10 (владелец проекта: таблицы, шаг 2) — общий разбор таблицы с <thead> для текста фрагментов (шаг 1) и таблиц-данных (шаг 2):
    подпись каждого столбца — из ВСЕХ строк шапки по разметке (colname / namest–nameend: «Zinssatz» над столбцами 2–12, под ним «2,0 %»
    -> «Zinssatz 2,0 %»); ячейка шапки на всю ширину — название таблицы; две таблицы бок о бок (подписи повторяются после пустого столбца:
    GKG/RVG Anlage 2) — одна логическая таблица, строки сначала левой, потом правой половины.
    -> [{"titel": [...], "spalten": [подписи], "kopf": текст шапки, "zeilen": [(подписи и значения [(l, v)], на_всю_ширину)]}]
    или None — если разметки шапки нет"""
    out = []
    for tg in tb.iter("tgroup"):
        cols = [c.get("colname") for c in tg.findall("colspec")]
        thead = tg.find("thead")
        if not cols or thead is None:
            return None
        idx = {c: k for k, c in enumerate(cols)}
        label = [[] for _ in cols]

        def spalten(e, pos):
            a, b = e.get("namest"), e.get("nameend")
            if a in idx and b in idx:
                return list(range(idx[a], idx[b] + 1))
            if e.get("colname") in idx:
                return [idx[e.get("colname")]]
            return [pos] if pos < len(cols) else []
        titel, kopf_zeilen = [], []
        for r in thead.findall("row"):
            pos = 0
            # 24.10: шапка — построчно, как в исходнике (сборка обратно в том же порядке); подписи столбцов — в ячейках строк данных
            kz = " | ".join(t for t in (_zelle(e) for e in r.findall("entry")) if t)
            if kz:
                kopf_zeilen.append(kz)
            for e in r.findall("entry"):
                sp = spalten(e, pos)
                t = _zelle(e)
                if t and len(sp) == len(cols) > 1:              # ячейка на всю ширину шапки — название таблицы, не подпись столбца
                    titel.append(t)
                else:
                    for k in sp:
                        if t and t not in label[k]:
                            label[k].append(t)
                pos = (sp[-1] + 1) if sp else pos + 1
        lab = [" ".join(x) for x in label]
        kopf = " ".join(titel + [" | ".join(x for x in lab if x)])           # для каталога: название + подписи столбцов
        gruppen = [list(range(len(cols)))]
        for k, x in enumerate(lab):
            # подписи должны быть непустыми: пустое == пустое делило надвое обычную таблицу (BewG «Flächen … | in EUR | 27,60»)
            if not x and 0 < k < len(cols) - 1 and any(lab[:k]) and lab[:k] == lab[k + 1:2 * k + 1]:
                gruppen = [list(range(k)), list(range(k + 1, len(cols)))]
                break
        tbody = tg.find("tbody")
        zeilen = []
        body = tbody.findall("row") if tbody is not None else []
        # первый столбец — номера («Lfd. Nr.»: 1, 2, 12): короткая строка-подпункт без номера («e) Hausschafe … | aus Position 0104»)
        # ставится вправо, иначе всё сдвигается на столбец (UStG Anlage 2)
        erste = [_zelle(r.find("entry")) for r in body if r.find("entry") is not None]
        nr_spalte = len(erste) > 3 and sum(1 for x in erste if re.fullmatch(r"\d{1,3}[a-z]?\.?", x)) >= 0.3 * len(erste)
        for r in body:
            werte, pos = [""] * len(cols), 0
            es = r.findall("entry")
            if (nr_spalte and 0 < len(es) < len(cols) and not any(e.get("colname") or e.get("namest") for e in es)
                    and not re.fullmatch(r"\d{1,3}[a-z]?\.?", _zelle(es[0]))):
                pos = len(cols) - len(es)
            for e in es:
                sp = spalten(e, pos)
                if sp:                                          # ячейка на несколько столбцов — под подписью последнего («b) Maultiere» -> Warenbezeichnung)
                    werte[sp[-1] if len(sp) < len(cols) else sp[0]] = _zelle(e)
                pos = (sp[-1] + 1) if sp else pos + 1
            if not any(werte):
                continue
            breit = r.find("entry") is not None and bool(r.find("entry").get("namest"))
            zeilen.append([([(lab[k], werte[k]) for k in g if werte[k]], breit) for g in gruppen])
        geordnet = [z[k] for k in range(len(gruppen)) for z in zeilen]      # левая половина, затем правая
        out.append({"titel": titel, "spalten": [lab[k] for k in gruppen[0]], "kopf": kopf, "kopf_zeilen": kopf_zeilen,
                    "zeilen": [(t, b) for t, b in geordnet if t]})
    return out


def _thead_rows(tb):
    """текст таблицы с <thead> для фрагментов: шапка, затем каждая строка «подпись: значение» (см. tabelle_daten)"""
    daten = tabelle_daten(tb)
    if daten is None:
        return None
    out = []
    for t in daten:
        out += t["kopf_zeilen"]                                 # шапка — построчно, как в исходнике (сборка обратно без потерь)
        for teile, breit in t["zeilen"]:
            # строка на всю ширину (подзаголовок внутри таблицы) — без подписи
            # 30.10: в тексте строки — только слова исходника; подписи столбцов — в заголовок (table_spalten -> eigenschaften.spalten), решение 24.10
            out.append(teile[0][1] if len(teile) == 1 and breit else " | ".join(v for l, v in teile))
    return [x for x in out if x]


def zeilen_roh(tb):
    """строки таблицы без разметки шапки: ячейки по позиции; 24.10 — если первый столбец — номера («Lfd.Nr.»: 1, 2, 12), короткая
    строка-подпункт без номера («e) Hausschafe … | aus Position 0104», UStG Anlage 2) ставится вправо, иначе сдвиг на столбец"""
    rows = [[_zelle(e) for e in r.iter("entry")] for r in tb.iter("row")]
    rows = [r for r in rows if any(r)]
    if not rows:
        return rows
    n = max(len(r) for r in rows)
    nr = sum(1 for r in rows if re.fullmatch(r"\d{1,3}[a-z]?\.?", r[0])) >= max(3, 0.3 * len(rows))
    if nr:
        rows = [([""] * (n - len(r)) + r) if len(r) < n and not re.fullmatch(r"\d{1,3}[a-z]?\.?", r[0]) else r for r in rows]
    return rows


def table_rows(tb):
    mit_kopf = _thead_rows(tb)
    if mit_kopf is not None:
        return mit_kopf
    # 23.10 вечер: перенос слова в ячейке («Gruppen-<BR/> behandlung») склеивается, «Kranken- und …» остаётся
    rows = zeilen_roh(tb)
    if not rows:
        return []
    kopf = rows[0]
    # 23.10 вечер (сборка обратно без потерь): первая строка выводится всегда — часто это не шапка, а вводная фраза на всю ширину
    # (RheinSchPV § 3.18); к ячейкам она приставляется как шапка, только если столбцов столько же и ячейки шапки короткие
    # 24.10: строка с числами — данные, не шапка («Ein- und Zweifamilienhäuser | 80 Jahre», BewG Anlage 38 без <thead>)
    erste = " | ".join(k for k in kopf if k)

    # 30.10: без «подпись: значение» в тексте (решение 24.10: подписи столбцов — поле заголовка, см. table_spalten); прежде повтор подписи
    # в каждой строке давал слова, которых нет в исходнике, и сверка теряла место (KMAnzV-бланки: «… Hauptverwaltung: Institut: ___»)
    return [erste] + [" | ".join(v for v in r if v) for r in rows[1:]]


def table_spalten(tb):
    """30.10: подписи столбцов таблицы (для eigenschaften.spalten строк) или None"""
    daten = tabelle_daten(tb)
    if daten:
        return [x for x in daten[0]["spalten"] if x] or None
    rows = zeilen_roh(tb)
    if len(rows) > 1 and all(len(k) <= 60 for k in rows[0]) and sum(1 for k in rows[0] if k) >= 2 and not any(re.search(r"\d", k) for k in rows[0]):
        return [k for k in rows[0] if k]
    return None


def einheiten(content):
    """-> [(метка, текст)] по структуре Content"""
    out = []
    for p in list(content) or [content]:
        if p.tag not in ("P", "table", "DL", "Content"):    # прочие блоки (Title, TOC, pre, Revision …) -- своей единицей, по своему тексту
            t = P.text_of(p).replace("\n", " ")
            if p.iter("table") and any(True for _ in p.iter("table")):
                out += [(f"Zeile {i}", r) for tb in p.iter("table") for i, r in enumerate(table_rows(tb), 1)]
            elif t.strip():
                out.append(("", t))
            continue
        if p.tag == "table":
            out += [(f"Zeile {i}", r) for i, r in enumerate(table_rows(p), 1)]
            continue
        if p.tag == "DL":                                   # перечень прямо в Content (без абзаца)
            out += [(f"Nr. {n.rstrip('.')}", t) for n, t in dl_items(p)]
            continue
        m = re.match(r"\((\d+[a-z]?)\)", flat(p))
        abs_ = f"Abs. {m.group(1)}" if m else ""
        dls = [x for x in p if x.tag == "DL"]
        tbs = list(p.iter("table"))                         # таблицы и во вложенных элементах
        if not dls and not tbs:
            out.append((abs_, P.text_of(p).replace("\n", " ")))
            continue
        # вводная фраза — текст абзаца до первого перечня; хвост после перечня — «Schlusssatz»
        intro, tail = [p.text or ""], []
        seen = False
        for ch in p:
            if ch.tag in ("DL", "table"):
                seen = True
                if (ch.tail or "").strip():
                    tail.append(ch.tail)
                continue
            (tail if seen else intro).append(" ".join("".join(ch.itertext()).split()) + " " + (ch.tail or ""))
        intro = " ".join("".join(intro).split())
        for dl in dls:
            for n, t in dl_items(dl):
                out.append(((abs_ + " " if abs_ else "") + f"Nr. {n.rstrip('.')}", f"{intro} … {n} {t}".strip()))
        for tb in tbs:
            out += [((abs_ + " " if abs_ else "") + f"Zeile {i}", f"{intro} … {r}".strip()) for i, r in enumerate(table_rows(tb), 1)]
        if "".join(tail).strip():
            out.append(((abs_ + " " if abs_ else "") + "Schlusssatz", f"{intro} … {' '.join(''.join(tail).split())}"))
    return [(l, t) for l, t in out if t.strip()]


def text_einheiten(text):
    """структура по тексту базы (там, где XML нет или он беднее текста базы): строки текста -- это блоки XML (P, LA, DT, DD, row);
    абзац «(n)» начинает единицу; строка-номер «1.»/«a)» + следующая строка -- пункт перечня с вводной фразой абзаца; прочие строки --
    продолжение своей единицы (без счётчика знаков).
    23.10 (повторная проверка фундамента): вложенный пункт (a) под 2.) несёт и вводную своего пункта («2. wenn … Stoffe … a) …»);
    строка вне перечня, кончающаяся «:» или «,» («Durch Rechtsverordnung … kann geregelt werden,»), — новая вводная для следующих пунктов"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    out, abs_, intro, cur_l, cur_t, i, stapel = [], "", "", "", [], 0, []   # stapel: [тип, текст пункта]

    def push():
        if cur_t:
            out.append((cur_l, " ".join(cur_t)))

    def typ(m):
        return "n" if m[0].isdigit() else "aa" if len(m.rstrip(")")) == 2 else "a"
    while i < len(lines):
        l = lines[i]
        m = re.match(r"^\((\d+[a-z]?)\)\s*", l)
        if m:
            push()
            abs_, intro, cur_l, cur_t, stapel = f"Abs. {m.group(1)}", l, f"Abs. {m.group(1)}", [l], []
        elif re.fullmatch(r"\d+[a-z]?\.|[a-z]{1,2}\)", l) and i + 1 < len(lines):
            push()
            t = typ(l)
            typen = [x[0] for x in stapel]
            stapel = stapel[:typen.index(t)] if t in typen else stapel
            eigen = f"{l} {lines[i + 1]}"
            cur_l = (abs_ + " " if abs_ else "") + f"Nr. {l.rstrip('.)')}"
            vor = [x for x in [intro] + [y[1] for y in stapel] if x]
            cur_t = [(" … ".join(vor) + VORLAUF if vor else "") + eigen]   # 24.10: вводная — отдельным полем
            stapel.append([t, eigen])
            i += 1
        elif stapel and re.search(r"[:,]$", l) and re.match(r"[A-ZÄÖÜ§]", l) and re.search(r"[.;]$", cur_t[-1] if cur_t else "."):
            # новая вводная после перечня: новое предложение (с заглавной, предыдущий пункт закончен); 23.10 вечер — иначе строка-продолжение
            # пункта («… nicht vollständig oder nicht» ⏎ «rechtzeitig erstattet,») рвала пункт посреди фразы (laws_historie: 5 -> 2 332 обрыва)
            push()
            intro, cur_l, cur_t, stapel = l, abs_, [l], []
        else:
            # 30.10: вводная до первого абзаца — только текущее предложение (прежде — весь текст с начала: SokaSiG Anlage 27 — 6 тыс. знаков
            # вводной у каждого пункта, текст приложения повторялся под 13 узлами)
            if not out and not abs_:
                intro = (intro + " " + l).strip() if cur_t and not re.search(r"[.;:]$", cur_t[-1]) else l
            cur_t.append(l)
        i += 1
    push()
    return out


GLIED = re.compile(r"(?<=\s)(?<!vgl\.\s)(?<!Tz\.\s)(?<!Tzn\.\s)(?<!Nr\.\s)(?<!Nrn\.\s)(?<!Art\.\s)(?<!Rz\.\s)(?<!Rn\.\s)(?<!Abs\.\s)(?<!Ziff\.\s)(?<!Abschn\.\s)(?<!Kap\.\s)(?<!Anh\.\s)(?<!\bund\s)(?<!\bbis\s)(?<!\boder\s)(?<!\bsowie\s)(?<!\bgemäß\s)(?<!\bnach\s)(?<!\bsiehe\s)(?<!Ziffer\s)(?<!Nummer\s)(?<!Abschnitt\s)(?<!Kapitel\s)(?<!Randziffer\s)(?<!Textziffer\s)(?=(?:(?:Abschnitt|Teil|Kapitel|Unterabschnitt|Titel)\s+[0-9IVX]+[a-z]?\b|\d{1,2}\.\d{1,2}(?:\.\d{1,2})*\s+[A-ZÄÖÜ]))")
# 23.10 вечер (проверка 5): номер «N.N» после «vgl.», «Tz.», «Abschn.», «bis», «und» … — часть ссылки («vgl. 15.20 Abs. 2»), не раздел
ABK = (r"(?<!\b[A-Za-zÄÖÜäöü]\.)(?<!\bAbs\.)(?<!\bNr\.)(?<!\bS\.)(?<!\bvgl\.)(?<!\bBGBl\.)(?<!\bArt\.)(?<!\bbzw\.)(?<!\bz\.\ B\.)(?<!\bu\.\ a\.)(?<!\bd\.)(?<!\bff\.)(?<!\bf\.)(?<!\bInh\.)(?<!\bGes\.)(?<!\bBuchst\.)(?<!\bRz\.)(?<!\bTz\.)(?<!\bZiff\.)(?<!\blit\.)(?<!\bi\.\ V\.\ m\.)(?<!\bi\.\ S\.\ d\.)(?<!\bi\.\ S\.\ v\.)(?<!\bi\.\ d\.\ F\.)(?<!\ba\.\ F\.)(?<!\bn\.\ F\.)(?<!\bAnm\.)(?<!\bBd\.)(?<!\bKap\.)(?<!\bAbschn\.)(?<!\bSatz\.)(?<!\bsog\.)(?<!\bggf\.)(?<!\bbspw\.)(?<!\binsb\.)(?<!\bevtl\.)(?<!\bz\.\ T\.)(?<!\bu\.\ U\.)(?<!\bUnterabs\.)(?<!\bVorbem\.)(?<!\bAnh\.)(?<!\bNrn\.)(?<!\bAbss\.)(?<!\bSätze\.)(?<!\bJan\.)(?<!\bFebr\.)(?<!\bAug\.)(?<!\bSept\.)(?<!\bOkt\.)(?<!\bNov\.)(?<!\bDez\.)(?<!\bMio\.)(?<!\bMrd\.)(?<!\bTsd\.)(?<!\bSt\.)(?<!\bEUR\.)(?<!\bca\.)(?<!\bDr\.)(?<!\bProf\.)"
       r"(?<!\bBStBl\.)(?<!\bABl\.)(?<!\bAbl\.)(?<!\bAz\.)(?<!\bBl\.)(?<!\bUrt\.)(?<!\bBeschl\.)(?<!\bRn\.)(?<!\bRdnr\.)(?<!\bRdNr\.)"
       r"(?<!\bAufl\.)(?<!\bHrsg\.)(?<!\bBAnz\.)(?<!\bSlg\.)(?<!\bRGBl\.)(?<!\bGVBl\.)(?<!\bGBl\.)(?<!\bVO\.)(?<!\bLfd\.)(?<!\blfd\.)"
       r"(?<!\bBundesgesetzbl\.)(?<!\bReichsgesetzbl\.)(?<!\bGesetzbl\.)(?<!\bAmtsbl\.)(?<!\bVerordnungsbl\.)(?<!\bBundesanz\.)")   # 23.10 повторная проверка: рез на «BStBl. ‖ II 2010», «ABl. ‖ EG Nr. L 204», «(Az. ‖ 23 TaBV …)»   # 22.10 проверка: сокращения С ТОЧКОЙ (прежде без точки — не срабатывали; 374 ложных разреза)
SATZ = re.compile(ABK + r"(?<=[a-zäöüß\)\]\d][.;])\s+(?!(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|Jan|Febr|Aug|Sept|Okt|Nov|Dez)\b)(?!(?:I|II|III)\s+(?:S\.|Nr\.|\d)|(?:EG|EU|EWG)\s+(?:Nr\.|L\s|C\s))(?=[A-ZÄÖÜ„(§]|\d{1,3} ?[A-ZÄÖÜ])")   # 22.10: и «… verfolgen. 2Begünstigt» (номер предложения приклеен)
GRENZE = 4000   # не место резки, а признак: единица длиннее предела реранкера (1024 токена) -- спуститься на уровень ниже по структуре


def fein(units):
    """единица длиннее предела реранкера -> следующий уровень структуры: разделы (Abschnitt/Teil/Kapitel, «2.1»), иначе предложения;
    вводная фраза с двоеточием -- в каждую часть"""
    out = []
    for l, t in units:
        if len(t) <= GRENZE:
            out.append((l, t))
            continue
        teile = [x for x in GLIED.split(t) if x.strip()]
        art = "Teil"
        if len(teile) < 2:
            teile, art = [x for x in SATZ.split(t) if x.strip()], "Satz"
        if len(teile) < 2:
            out.append((l, t))          # нет следующего уровня (строка таблицы, сплошной текст) -- единица остаётся целой
            continue
        intro = teile[0] if teile[0].rstrip().endswith(":") else ""
        for k, x in enumerate(teile, 1):
            pre = intro + VORLAUF if intro and k > 1 else ""
            mk = re.match(r"((?:Abschnitt|Teil|Kapitel|Unterabschnitt|Titel)\s+[0-9IVX]+[a-z]?|\d{1,2}(?:\.\d{1,2})+)", x) if art == "Teil" else None
            out.append(((l + " " if l else "") + (mk.group(1) if mk else f"{art} {k}"), pre + x.strip()))
    return out


def parse(path):
    """-> {(jurabk, enbez): (titel, [(метка, текст)])}"""
    res = {}
    try:
        z = zipfile.ZipFile(path)
        root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".xml"))))
    except Exception:
        return res
    abk0 = None
    for n in root.iter("norm"):
        md = n.find("metadaten")
        if md is None:
            continue
        abk = (md.findtext("jurabk") or md.findtext("amtabk") or "").strip()
        abk0 = abk0 or abk
        enbez = P.enbez_norm(md.findtext("enbez"))
        c = n.find("textdaten/text/Content")
        if not enbez or c is None:
            continue
        tt = md.find("titel")
        # одинаковые номера внутри закона (EGBGB: «§ 3» в разных статьях) -- храним все, выбираем по заголовку
        res.setdefault((abk or abk0, enbez), []).append((" ".join("".join(tt.itertext()).split()) if tt is not None else "", einheiten(c)))
    return res


def main():
    probe = sys.argv[sys.argv.index("--probe") + 1].split(",") if "--probe" in sys.argv else None
    cur = psycopg2.connect(dbname="steuer_corpus_test", user="postgres", host="localhost").cursor()
    cur.execute("SELECT id, title, full_text FROM documents WHERE source = 'laws' AND valid_until IS NULL")
    db = {}
    for i, title, text in cur.fetchall():
        k = P.key_of_title(title)
        if probe and k[0].split(" ")[0] not in probe:
            continue
        db.setdefault(k, []).append((i, title, text))
    zips = sorted(SRC.glob("*.zip"))
    with Pool(24) as pool:
        xml = {}
        for r in pool.imap_unordered(parse, zips, chunksize=20):
            xml.update(r)
    out = OUT / ("laws_struktur_probe.jsonl" if probe else "laws_struktur.jsonl")
    st = Counter()
    with open(out, "w", encoding="utf-8") as f:
        for k, docs in db.items():
            if k not in xml:
                st["ohne_xml"] += len(docs)
                for i, title, text in docs:
                    pf = title.split(" - ")[0] + (f" {title.split(' - ', 1)[1]}" if " - " in title else "")
                    frs = [f"{pf}{', ' + l if l else ''}\n{t}" for l, t in fein(text_einheiten(text))]
                    st["frags"] += len(frs)
                    f.write(json.dumps({"document_id": i, "title": title, "status": "text_ohne_xml", "fehlwoerter": 0, "fragmente": frs},
                                       ensure_ascii=False) + "\n")
                continue
            for i, title, text in docs:
                kand = xml[k]
                dt = title.split(" - ", 1)[1].strip() if " - " in title else ""
                titel, units = next((c for c in kand if c[0] and c[0] == dt), None) or max(
                    kand, key=lambda c: len(set(re.findall(r"[a-zäöüß]{4,}", (text or "").lower())) & set(re.findall(r"[a-zäöüß]{4,}", " ".join(t for _, t in c[1]).lower()))))
                pfad0 = f"{k[0]} {k[1]}" + (f" {titel}" if titel else "")
                units = fein(units)
                frs = [f"{pfad0}{', ' + l if l else ''}\n{t}" for l, t in units]
                # покрытие: каждое слово текста базы должно быть во фрагментах (иначе нарезка что-то потеряла)
                wb = Counter(re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", text or ""))
                wf = Counter(re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", " ".join(t for _, t in units)))
                fehl = sum((wb - wf).values())
                status = "ok"
                if fehl > 3:   # XML беднее текста базы (картинка вместо текста, другая редакция таблицы) -- структура по тексту базы
                    units, status = fein(text_einheiten(text)), "text_xml_beden"
                    frs = [f"{pfad0}{', ' + l if l else ''}\n{t}" for l, t in units]
                    wf = Counter(re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", " ".join(t for _, t in units)))
                    fehl = sum((wb - wf).values())
                    st["text_xml_beden"] += 1
                st["docs"] += 1
                st["frags"] += len(frs)
                st["fehlwoerter_docs"] += fehl > 3
                f.write(json.dumps({"document_id": i, "title": title, "status": status, "fehlwoerter": fehl,
                                    "fragmente": frs}, ensure_ascii=False) + "\n")
    print(f"STRUKTUR_LAWS: {dict(st)} -> {out}", flush=True)


if __name__ == "__main__":
    main()
