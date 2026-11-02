#!/usr/bin/env python3
"""struktur_verwaltung.py (22.10.2026) — нарезка по структуре для административных источников (справочники BMF: EStH, LStH, UStAE/UStH,
KStH, GewStH, ErbStR/H, AO-Handbuch; письма BMF; BaFin, Bundesbank, VwV, BZSt, Zoll …). Запрет владельца проекта 22.10: без резки по знакам.
Единица — абзац «(n)» / номер «Rz. n», «Tz. n», «n.» в начале строки; пункты перечня «1.», «a)» — с вводной фразой абзаца; длинная
единица -> разделы «1.1», затем предложения (struktur_laws.fein). Путь — заголовок документа (Abschnitt / R / H + название) + метка.
Проверка покрытия: каждое слово текста базы — во фрагментах. Только чтение базы; результат — файл.
    python3 struktur_verwaltung.py bmf_handbuecher [bmf …]
"""
import json
import os
import re
import sys
from collections import Counter
from multiprocessing import Pool

import psycopg2

from struktur_laws import fein
from struktur_text import DATUM, VORLAUF, einl_aus, fein_eigen, fein_tief, fortsetzung, kleine_zusammen, saeubern

W = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
MARKE = re.compile(r"^\s*(?:\((\d+[a-z]?)\)|(?:Rz\.|Rn\.|Tz\.|Randnummer|Randziffer)\s*(\d+[a-z]?)|(\d{1,3})\.\s+(?=[A-ZÄÖÜ„])|([IVX]{1,5})\.\s+(?=[A-ZÄÖÜ„]))")


PUNKT = re.compile(r"^\s*(?:(\d{1,3})\.|([a-z])\)|([a-z])\3\)|([a-z])\4\4\))\s")   # «1.», «a)», «aa)», «aaa)»
NUR_MARKE = re.compile(r"(?:\(\d+[a-z]?\)|\d{1,3}\.|[IVX]{1,5}(?:\.\d{1,2})*\.|\d{1,2}(?:\.\d{1,2})+\.?|[a-z]{1,3}\)|(?:Rz\.|Rn\.|Tz\.)\s*\d+[a-z]?)")
TYP_NAME = {"n": "Nr.", "a": "Buchst.", "aa": "Doppelbuchst.", "aaa": "Dreifachbuchst."}


def zeilen(text):
    """строки после чистки; строка из одной метки («2.», «(1)») — вместе со следующей (в PDF номер часто стоит отдельной строкой)"""
    roh, neu = [], True
    for l in saeubern(text).splitlines():                  # 29.10: строка после пустой — начало абзаца исходника (для вводной, см. mit_absatz)
        if l.strip():
            roh.append(Zeile(l.strip())); roh[-1].absatz = neu; neu = False
        else:
            neu = True
    out, k = [], 0
    while k < len(roh):
        l = roh[k]
        if NUR_MARKE.fullmatch(l) and k + 1 < len(roh):
            l, k = l + " " + roh[k + 1], k + 1
        elif re.fullmatch(r"(?:Artikel|§)\s*\d+[a-z]?", l) and k + 1 < len(roh) and len(roh[k + 1]) < 120 \
                and roh[k + 1][:1].isupper() and not re.search(r"[.;,:]\s*$", roh[k + 1]):
            l, k = l + " " + roh[k + 1], k + 1             # «Artikel 1» ⏎ «Änderung des Einkommensteuergesetzes» — заголовок целиком
        if not isinstance(l, Zeile):                        # склеенная строка — признак абзаца от первой
            l, l.absatz = Zeile(l), roh[k - 1].absatz
        out.append(l)
        k += 1
    return out


class Zeile(str):
    """строка исходника; absatz = перед ней была пустая строка"""
    absatz = False


def mit_absatz(cur_t):
    """строки куска -> текст для einl_aus: граница абзаца исходника — знак \\x1e (та же длина, что пробел: смещения vorn_len не меняются)"""
    return "".join(("\x1e" if getattr(l, "absatz", False) else " ") * (k > 0) + l for k, l in enumerate(cur_t))


TEIL = re.compile(r"^(?:(?:Erster|Zweiter|Dritter|Vierter|Fünfter|Sechster|Siebter|Achter|Neunter|Zehnter|Elfter|Zwölfter)\s+"
                  r"(?:Teil|Abschnitt|Unterabschnitt|Titel)|(?:Teil|Abschnitt|Unterabschnitt|Kapitel|Titel)\s+[0-9IVX]+[a-z]?)\b(?!\s+(?:Nr|Nummer|Abs|Absatz|Satz|des|der|und|bis|§)\b)")
# «Artikel 12 Bundessozialhilfegesetz», «§ 1 Aufhebung der Anerkennung …» — Überschrift (без точки в конце), не фраза «§ 3 gilt …»
ARTIKEL = re.compile(r"^(Artikel\s+\d+[a-z]?)(?:\s+(?!(?:Nr|Nummer|Abs|Absatz|Satz|des|der|und|bis|wird|werden|gilt|ist|§)\b)\S.*)?$")
# 30.10: «Anlage 1 (zu § 1 Absatz 1 …)», «Anhang 3 Muster …» — свой раздел (прежде текст приложений BGBl прилипал к последнему абзацу
# последнего §: 127495 § 105 Abs. 2 Nr. 3 — все приложения с бланками); ссылка в тексте («Anlage 13 zu verwenden.») — не заголовок
ANLAGE = re.compile(r"^(?:Anlage|Anhang)\s+\d+[a-z]?\b(?!\s+(?:Nr|Nummer|Abs|Absatz|Satz|des|der|und|bis|wird|werden|gilt|ist|findet|enthält|zu\s+verwenden)\b)")
BEREICH = re.compile(r"^(Entwurf eines\b.*|Gesetzentwurf\b.*|Begründung\b.*|Stellungnahme des Bundesrates\b.*|Gegenäußerung der Bundesregierung\b.*|"
                     r"Beschlu(?:ss|ß)empfehlung\b.*|Bericht de[rs] Abgeordneten\b.*|Bericht des\b.*Ausschusses\b.*|Vorblatt\b.*)$")
# обоснование законопроекта (Drucksachen): «Zu Artikel 1» -> «Zu § 1» / «Zu Nummer 2» -> «Zu Absatz 3» / «Zu Buchstabe e» -> «Zu Satz 2»
ZU = re.compile(r"^Zu\s+(?:(Artikel|Art\.)|(§§?|Nummer|Nummern|Nr\.)|(Absatz|Absätzen|Abs\.|Buchstabe|Buchstaben|Buchst\.)|(Doppelbuchstabe|Satz|Sätzen))\s*\d*[a-z]?\b")
PARAGRAF = re.compile(r"^(§\s*\d+[a-z]?)(?:\s+(?!(?:Abs|Absatz|Satz|Nummer|Nr|Buchst|Buchstabe|und|bis|des|der|wird|werden|gilt|ist|findet)\b)"
                      r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+(?!\.)\b.*)?$")


def einheiten(text, quelle=""):
    """23.10 (шаг 2): уровни Teil/Abschnitt -> Artikel/§ (законы-поправки в BGBl, постановления) -> абзац «(n)»/Rz. -> пункты перечня;
    пункт перечня — до следующей метки, со всеми строками продолжения (раньше брал только свою первую строку — «… wird wie folgt ‖
    gefasst:» рвалось); вложенные перечни (1. -> a) -> aa)) с вводными всех уровней; дата и строка-продолжение из PDF — не метка;
    внутри цитаты «„…“» (новый текст нормы в BGBl) уровни не сбрасываются, абзацы «(n)» — части своего пункта с его вводной."""
    out, cur_l, cur_t = [], "", []
    teil, artikel, absl, basis, stapel = "", "", "", "", []     # stapel: открытые уровни перечня [typ, метка, вводная]
    zu = []                                                     # уровни обоснования [ранг, метка]
    bereich = ""                                                # Drucksachen: часть документа (Entwurf / Begründung / Stellungnahme …)
    vor, q, q_zeilen, zitat_l, zitat_einl, vorn_len = "", 0, 0, "", "", 0
    haengend, kopf_zeile = "", None
    zl = zeilen(text)
    # строка = абзац (письма, справочники) или строка страницы PDF (BGBl, Drucksachen): в первом случае после перечня
    # обычный абзац — отдельная единица, во втором строка после «.» может быть продолжением пункта
    absatzweise = bool(zl) and sorted(len(x) for x in zl)[len(zl) // 2] > 120

    def push():
        nonlocal haengend
        if cur_t == [kopf_zeile]:                           # заголовок без своего текста («Artikel 3», «Abschnitt 2») — к следующей единице
            haengend = (haengend + " " + kopf_zeile).strip()
        elif cur_t:
            out.append((cur_l, " ".join(([haengend] if haengend else []) + cur_t)))
            haengend = ""

    def label(*rest):
        return " › ".join(x for x in (bereich, teil, artikel, " ".join(z[1] for z in zu), " ".join(y for y in (absl,) + rest if y)) if x)
    for l in zl:
        frei = not DATUM.match(l) and not fortsetzung(vor)
        m = MARKE.match(l) if frei else None
        p = PUNKT.match(l) if frei else None
        ueber = frei and len(l) < 160 and not re.search(r"[.;,]\s*$", l)
        # 23.10 (коллеги): в Drucksachen текст законопроекта не должен выглядеть как действующий закон — часть документа в пути
        if quelle in ("bt_drucksachen", "landtag_drucksachen") and q == 0 and len(l) < 90:
            bm = BEREICH.match(l)
            if bm and not (bm.group(1).startswith("Entwurf") and bereich):
                bereich = "Begründung" if bm.group(1).startswith("Begründung") else ("Entwurf" if bm.group(1).startswith(("Entwurf", "Gesetzentwurf")) else bm.group(1))
                teil, artikel, absl, basis, stapel, zu = "", "", "", "", [], []
        if q > 0 and ueber and ARTIKEL.match(l) and not l.startswith("„"):   # незакрытая кавычка (старые сканы) — «Artikel n» её закрывает
            q = 0
        if q > 0:                                           # внутри цитаты: только абзацы «(n)» нового текста
            if m and m.group(1):
                push()
                cur_l = (zitat_l + " › " if zitat_l else "") + f"Abs. {m.group(1)}"
                cur_t = [zitat_einl + VORLAUF + l] if zitat_einl else [l]   # 25.10: вводная цитаты — тоже в поле vorlauf, не в текст
                vorn_len = len(zitat_einl) + 3 if zitat_einl else 0
            else:
                cur_t.append(l)
        elif ueber and TEIL.match(l):                       # Teil / Abschnitt / Kapitel
            push()
            teil, artikel, absl, basis, stapel, zu = l, "", "", "", [], []  # 28.10: без обрезки по знакам (владелец проекта 22.10 / Main 28.10: заголовок целиком)
            cur_l, cur_t, vorn_len, kopf_zeile = label(), [l], 0, l
        elif frei and ZU.match(l):                          # «Zu § 1», «Zu Absatz 3: …» — часть обоснования
            push()
            z = ZU.match(l)
            rang = next(k for k in range(4) if z.group(k + 1))
            zu = [x for x in zu if x[0] < rang] + [[rang, re.split(r"[:.]\s", l, maxsplit=1)[0]]]
            absl, basis, stapel = "", "", []
            cur_l, cur_t, vorn_len, kopf_zeile = label(), [l], 0, l
        elif ueber and (ARTIKEL.match(l) or PARAGRAF.match(l) or ANLAGE.match(l)):   # Artikel n / § n с заголовком / Anlage n (30.10)
            push()
            artikel, absl, basis, stapel, zu = l, "", "", [], []  # 28.10: без обрезки по знакам (владелец проекта 22.10 / Main 28.10: заголовок целиком)
            if ANLAGE.match(l):                             # 30.10: приложение — вне разделов основного текста
                teil = ""
            cur_l, cur_t, vorn_len, kopf_zeile = label(), [l], 0, l
        elif m and (m.group(1) or m.group(2)):              # абзац «(n)» / Rz., Tz.
            push()
            absl, basis, stapel = (f"Abs. {m.group(1)}" if m.group(1) else f"Rz. {m.group(2)}"), "", []
            cur_l, cur_t, vorn_len = label(), [l], 0
        elif p and ([s for s in stapel if s[0] == typ_von(p)] or " ".join(cur_t).rstrip(" „“\"").endswith(":")):
            typ = typ_von(p)
            typen = [s[0] for s in stapel]
            if typ in typen:                                # следующий пункт того же уровня
                stapel = stapel[:typen.index(typ)]
            elif stapel:                                    # вложенный перечень: текущий пункт (без вводных выше) — его вводная
                stapel[-1][2] = einl_aus(mit_absatz(cur_t)[vorn_len:])
            else:                                           # первый пункт: вводная — абзац
                basis = einl_aus(mit_absatz(cur_t))
            push()
            num = next(g for g in p.groups() if g)
            stapel.append([typ, f"{TYP_NAME[typ]} {num * len(typ) if typ != 'n' else num}", ""])
            cur_l = label(*[s[1] for s in stapel])
            vorn = " … ".join(x for x in [basis] + [s[2] for s in stapel[:-1]] if x)
            cur_t = [vorn + VORLAUF + l] if vorn else [l]   # 24.10: вводная — отдельным полем
            vorn_len = len(vorn) + 3 if vorn else 0
        elif m and (m.group(3) or m.group(4)) and len(l) < 160:   # раздел «II.» / заголовок «12. …» вне перечня
            push()
            absl, basis, stapel = (f"Abschnitt {m.group(4)}" if m.group(4) else f"Nr. {m.group(3)}"), "", []
            if m.group(4):                                  # «II. Besonderer Teil» — новый раздел, обоснование заново
                zu = []
            cur_l, cur_t, vorn_len = label(), [l], 0
        elif absatzweise and stapel and re.search(r"[.!?]\s*[“\")]?$", vor) and l[:1].isupper():   # хвост абзаца после перечня
            push()
            basis, stapel = "", []
            cur_l, cur_t, vorn_len = label(), [l], 0
        else:
            cur_t.append(l)
        # цитата «„…“»: открыта, пока «„» больше, чем «“»; страховка — не дольше 150 строк
        d = l.count("„") - l.count("“")
        if q == 0 and d > 0:
            t = " ".join(cur_t)
            i = t.find("„")
            zitat_l, zitat_einl, q_zeilen = cur_l, einl_aus(t[:i]) if i > 0 else "", 0
        q = max(0, q + d)
        q_zeilen += 1
        if q and q_zeilen > 150:
            q = 0
        vor = l
    kopf_zeile = None
    push()
    if haengend:                                            # заголовок в самом конце документа
        out.append((cur_l, haengend))
    return [(l, t) for l, t in out if t.strip()]


def typ_von(p):
    return "n" if p.group(1) else "a" if p.group(2) else "aa" if p.group(3) else "aaa"


def doc(row):
    i, src, title, text = row
    units = kleine_zusammen(fein_eigen(einheiten(text, src), fein)) or [("", " ".join(saeubern(text).split()))]
    kopf = (title or "").strip()
    frs = [{"document_id": i, "pfad": kopf + (f" › {l}" if l else ""), "text": t} for l, t in units]
    wb, wf = Counter(W.findall(saeubern(text))), Counter(W.findall(" ".join(t for _, t in units)))
    return i, src, frs, sum((wb - wf).values())


def main():
    quellen = sys.argv[1:]
    cur = psycopg2.connect(dbname="steuer_staging", user="postgres", password="postgres", host="localhost").cursor()
    # 23.10: и прежние редакции (valid_until задан) — раньше 3 148 редакций BMF/ЕС остались без нарезки (проверка фундамента, п. 1)
    # 23.10 (владелец проекта): NUR_NICHT_BESTANDEN=1 — резать только документы, не прошедшие проверку (dok_pruefung.bestanden = false);
# 24.10: AUCH_IDS=1,2,3 — и эти (исходник заменён после проверки: старая отметка устарела)
    cur.execute("SELECT id, source, title, full_text FROM documents WHERE source = ANY(%s)" + ((" AND id IN (" + ",".join(x.strip() for x in open(os.environ["NUR_IDS"]) if x.strip()) + ")") if os.environ.get("NUR_IDS") else (" AND (id NOT IN (SELECT document_id FROM dok_pruefung WHERE bestanden) OR id IN (" + os.environ.get("AUCH_IDS", "0") + "))" if os.environ.get("NUR_NICHT_BESTANDEN") else "")), (quellen,))
    rows = cur.fetchall()
    st = Counter()
    with Pool(24) as p, open(f"quellen_neu/vorlauf_fix/prepared/struktur_fragmente_{'_'.join(quellen) if len(quellen) <= 6 else 'verw_' + str(len(quellen)) + 'q'}{'_nachschnitt' if os.environ.get('NUR_NICHT_BESTANDEN') or os.environ.get('NUR_IDS') else ''}.jsonl", "w", encoding="utf-8") as f:
        for i, src, frs, fehl in p.imap_unordered(doc, rows, chunksize=50):
            st["docs"] += 1
            st["fragmente"] += len(frs)
            st["verlust_docs"] += fehl > 3
            for x in frs:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("STRUKTUR_VERWALTUNG", dict(st), flush=True)


if __name__ == "__main__":
    main()
