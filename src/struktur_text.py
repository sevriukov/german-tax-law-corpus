#!/usr/bin/env python3
"""struktur_text.py (23.10.2026) — общая подготовка текста для нарезчиков писем, BGBl, Drucksachen и судов (шаг 2 плана
PLAN_Fundament_Reparatur_2026-10-23.txt; находки проверки фундамента 0.3 и 0.8):
  - чистка: управляющие знаки, мягкий перенос, лигатуры (ﬁ ﬂ …);
  - склейка переносов из PDF: «nun-⏎mehr» и «nun- mehr» -> «nunmehr», «AIFM-⏎Steuer» -> «AIFM-Steuer»; «Ein- und …» не трогается;
  - признаки строки: дата («20. Februar 2002») — не номер пункта; строка, продолжающая предыдущую (та кончается запятой, дефисом,
    строчной буквой или словом вроде «vom», «Nr.», «§»), — не начало новой единицы;
  - короткие единицы подряд (шапка решения, подписи судей, строки оглавления) — одной единицей.
Исходный текст в documents не меняется; чистится только текст фрагментов (и тот же текст — в проверке покрытия)."""
import re

STEUER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f­​  ﻿]")
LIGATUREN = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
BINDE = r"(?:und|oder|bzw|sowie|bis|als|noch|wie|u|o|beziehungsweise)\b"
TRENN_ZEILE_KLEIN = re.compile(r"([A-Za-zÄÖÜäöüß]{2})-[ \t]*\n(?:[ \t]*\n){0,4}[ \t]*(?!" + BINDE + r")([a-zäöüß])")
TRENN_ZEILE_GROSS = re.compile(r"([A-Za-zÄÖÜäöüß]{2}-)[ \t]*\n(?:[ \t]*\n){0,4}[ \t]*([A-ZÄÖÜ])")
TRENN_INNEN = re.compile(r"([a-zäöüß]{2})- (?!" + BINDE + r")([a-zäöüß]{2})")
# 24.10: сложное прилагательное — дефис настоящий, лишний только пробел/перенос: «bürgerlich- rechtlichen» -> «bürgerlich-rechtlichen»
# (но «freundlich- keit» — перенос: вторая часть — суффикс)
ADJ_TRENN = re.compile(r"([a-zäöüß]{2,}(?:lich|isch|ig))-[ \t]*\n?(?:[ \t]*\n){0,4}[ \t]*(?!" + BINDE + r"|(?:keit|heit|ung|en|er|e|es|em)\b)([a-zäöüß]{3,})")


def trennung(t):
    """общая склейка переносов для всех нарезчиков: сложные прилагательные — с дефисом, прочее — слитно; «Kranken- und …» — как есть"""
    t = ADJ_TRENN.sub(r"\1-\2", t)
    t = TRENN_ZEILE_KLEIN.sub(r"\1\2", t)
    t = TRENN_ZEILE_GROSS.sub(r"\1\2", t)
    return TRENN_INNEN.sub(r"\1\2", t)
MONAT = r"(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|Jan|Febr?|Mär|Apr|Jun|Jul|Aug|Sept?|Okt|Nov|Dez)\b"
DATUM = re.compile(r"^\s*\d{1,2}\.\s?" + MONAT)
FUNKTION = re.compile(r"(?:[,\-–/(]|[a-zäöüß]|\b(?:Nr|Nrn|Abs|S|Art|BGBl|BStBl|ABl|Abl|Slg|Aufl|Fn|Az|Bl|Tz|Tzn|Rz|Rn|Ziff|Buchst|Anl|Kap|Abschn|Unterabschn|Anh|vgl|gem|Rdnr|Randnr)\.|§§?|\b(?:Satz|Nummer|Absatz|"
                      r"Artikel|Buchstabe|Ziffer|Abschnitt|Teil|Kapitel|Anlage))\s*$")
W2 = re.compile(r"[A-Za-zÄÖÜäöüß]{2,}")
# колонтитул страницы — только сразу после знака новой страницы «\f» (на первой странице шапка — сведения о документе, остаётся)
KOPFZEILE = (r"[ \t]*(?:\d{1,4}|-[ \t]*\d{1,4}[ \t]*-|Seite[ \t]+\d{1,4}(?:[ \t]+von[ \t]+\d{1,4})?|"
             r"(?:\d{1,4}[ \t]+)?Bundesgesetzblatt[^\n]{0,120}|(?:\d{1,4}[ \t]+)?Drucksache[ \t]+\d{1,2}/\d{1,6}[^\n]{0,60}|"
             r"(?:\d{1,4}[ \t]+)?Deutscher Bundestag[ \t]*[—–-][ \t]*\d{1,2}\.[ \t]*Wahlperiode[^\n]{0,40}|Nr\.[ \t]*\d{1,3}[ \t]*[—–-][ \t]*Tag der Ausgabe[^\n]{0,80}|"
             r"Synthetisierter[ \t]*T[ \t]?ext des BEPS-MLI[^\n]{0,120})[ \t]*\n")
SEITENKOPF = re.compile(r"\f(?:[ \t]*\n)*(?:" + KOPFZEILE + r"(?:[ \t]*\n)*){1,3}")


# 24.10: двойная перекодировка UTF-8 -> Latin-1 при прошлой загрузке («Lâ€¦», «â€“») — однозначная обратная замена
MOJIBAKE = {"â€¦": "…", "â€“": "–", "â€”": "—", "â€ž": "„", "â€œ": "“", "â€\x9d": "”", "â€™": "’", "â€˜": "‘", "â€š": "‚",
            "Ã¤": "ä", "Ã¶": "ö", "Ã¼": "ü", "Ã„": "Ä", "Ã–": "Ö", "Ãœ": "Ü", "ÃŸ": "ß", "Ã©": "é", "Â§": "§", "Â ": " "}
MOJIBAKE_RX = re.compile("|".join(re.escape(k) for k in sorted(MOJIBAKE, key=len, reverse=True)))


def saeubern(text):
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = MOJIBAKE_RX.sub(lambda m: MOJIBAKE[m.group()], t)
    # 24.10: знак переноса шрифта без Unicode («Enhan�­cing», «Deutsch�­land» в PDF BMF) + мягкий перенос — слово целиком
    t = re.sub(r"(?<=[A-Za-zÄÖÜäöüß])\ufffd?\u00ad[ \t]*\n?[ \t]*(?=[a-zäöüß])", "", t)
    t = SEITENKOPF.sub("\n", t)
    t = re.sub(r"(?m)^[ \t]*\d{1,3}[ \t]*/[ \t]*\d{1,3}[ \t]*\n", "", t)        # номера страниц «24/42» (BVerfG) — разрывают переносы
    t = STEUER.sub(lambda m: "\n" if m.group() in "  " else "", t.translate(LIGATUREN))
    return trennung(t)


def fortsetzung(vor):
    """предыдущая строка не закончена — следующая её продолжает (разрыв строки из PDF), а не начинает пункт"""
    return bool(vor) and bool(FUNKTION.search(vor))


def kleine_zusammen(units, grenze=4000, min_woerter=3):
    """подряд идущие крошечные единицы (меньше 3 слов, не конец фразы) — одной единицей: шапка решения по строкам, подписи,
    строки оглавления; одиночная короткая единица остаётся как есть (короткие нормы не склеиваются с чужими)"""
    out, lauf = [], []

    def klein(t):   # 30.10: единица с вводной (VORLAUF) не склеивается — иначе её текст ушёл бы в vorlauf следующей
        return VORLAUF not in t and len(W2.findall(t)) < min_woerter and not re.search(r"[.;!?]\s*[“\")]?\s*$", t)

    def ablegen():
        if len(lauf) >= 2:
            out.append((lauf[0][0], " ".join(t for _, t in lauf)))
        else:
            out.extend(lauf)
        lauf.clear()
    for l, t in units:
        if klein(t) and sum(len(x) for _, x in lauf) + len(t) <= grenze:
            lauf.append((l, t))
        else:
            ablegen()
            out.append((l, t)) if not klein(t) else lauf.append((l, t))
    ablegen()
    return out


def einl_aus(t):
    """вводная часть для пунктов: весь текст, если короткий, иначе последняя фраза перед двоеточием"""
    t = t.strip()
    if len(t) <= 500:
        return t.replace("\x1e", " ")
    teile = re.split(r"(?<=[.;])\s+(?=[A-ZÄÖÜ„(§])", t)
    # 28.10 (Владелец проекта: «Базу надо исправлять»): последняя фраза ЦЕЛИКОМ. Прежде фраза длиннее 500 знаков обрезалась до последних
    # 500 знаков (до 24.10 — посреди слова: «… iben oder gestatten») — нарезка по счётчику, запрещена 22.10.
    letzte = teile[-1]
    # 29.10: фраза не найдена (оглавление, таблица перед вводной: «… Grundzügen Zum Geschäftsbereich … folgende Aufgabengebiete:») —
    # граница абзаца исходника (\x1e, пустая строка; передаёт struktur_verwaltung.mit_absatz), если последний абзац начинается как фраза
    if len(letzte) > 500 and "\x1e" in letzte:
        davor, block = (x.strip() for x in letzte.rsplit("\x1e", 1))
        if re.match(r"[A-ZÄÖÜ„(§]", block) and not re.search(r"\b[a-zäöüß]+[,]?$", davor):   # «… nach ⏎⏎ Maßgabe» — перенос страницы, фраза продолжается
            letzte = block
    return "… " + letzte.replace("\x1e", " ")


def fein_tief(units, fein, grenze=24000):
    """struktur_laws.fein делит единицу длиннее 4 000 знаков на один уровень (разделы ИЛИ предложения); часть-раздел может остаться
    длиннее предела модели (8 192 токена ~ 24 000 знаков — хвост не попадает в вектор): только такие части делить ещё раз"""
    units = fein(units)
    for _ in range(3):
        if all(len(t) <= grenze for _, t in units):
            break
        neu = []
        for u in units:
            neu.extend(fein([u]) if len(u[1]) > grenze else [u])
        if neu == units:
            break
        units = neu
    return units


def fein_eigen(units, fein, grenze=24000):
    """30.10: fein_tief только по собственному тексту единицы; вводная (до VORLAUF) — полем у каждого куска. Прежде fein резал и вводную:
    её первое предложение становилось отдельным «Satz 1», а куски дальше получали чужую вводную (BGBl 127495 § 105 Abs. 2 Nr. 3;
    в struktur_historie то же исправлено 29.10)"""
    out = []
    for l, t in units:
        vor, _, eigen = t.rpartition(VORLAUF)
        out += [(l2, (vor + VORLAUF if vor else "") + t2) for l2, t2 in fein_tief([(l, eigen)], fein, grenze)]
    return out


# 24.10 (Владелец проекта: реквизиты — в заголовок чанка, не в текст): знак между вводной пункта и собственным текстом куска при выдаче нарезчиков.
# Загрузчики делят по нему: вводная -> поле vorlauf, собственный текст -> text_roh. В исходниках этого знака нет
# (« … » нельзя: это и многоточие самих исходников — «Kennzeichen … zu»).
VORLAUF = "\x1f"


def vorlauf_teilen(t):
    """«вводная␟вводная2␟текст» -> (vorlauf «вводная … вводная2» или None, собственный текст)"""
    teile = (t or "").split(VORLAUF)
    return (" … ".join(x for x in teile[:-1] if x.strip()) or None), teile[-1]
