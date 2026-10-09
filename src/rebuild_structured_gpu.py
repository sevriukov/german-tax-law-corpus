#!/usr/bin/env python3
import os
# КРИТИЧЕСКИ ВАЖНО: Зашиваем защиту от OOM прямо в скрипт, до импорта torch/sentence_transformers
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import textwrap

import psycopg2
from psycopg2.extras import execute_values
import re
import time
import json
import argparse
from pathlib import Path
from sentence_transformers import SentenceTransformer

# ==========================================
# 1. АРХИТЕКТУРА УМНОГО ЧАНКИНГА (СТРАТЕГИЯ)
# ==========================================

class BaseChunker:
    """Базовый класс: очистка, проверка мусора, fallback-нарезка и заготовка под Граф."""
    def __init__(self, max_chars=1200):
        self.max_chars = max_chars
        # $ в конце гарантирует, что мы ловим именно оглавления, а не случайные отсылки внутри текста
        self.toc_tail = re.compile(r"[.·_]{5,}\s*\d+\s*$", re.MULTILINE)

        # ВТОРОЙ детектор оглавления (найдено вживую на реальном BaFin/MaComp):
        # номер страницы иногда приклеен к последнему слову БЕЗ какого-либо
        # разделителя вообще ("...unternehmens12"), обычный toc_tail такое
        # не ловит (там {5,} точек/подчёркиваний обязательны). Признак:
        # короткая строка (одна строка оглавления, не полноценный абзац) +
        # заканчивается на "буква вплотную к 1-3 цифрам" без пробела перед ними.
        # ФИКС v3 (audit_chunking.py, 2026-09-28): было r"\d{1,4}\s*$" -- без
        # требования "буква вплотную", как задумано выше. Под фильтр попадал
        # ЛЮБОЙ короткий фрагмент на цифре ("IX R 5/19", "10117 Berlin",
        # строки таблиц договоров BMF) -- тихая потеря текста до 9.5% документа.
        self.toc_glued_number = re.compile(r"[A-Za-zÄÖÜäöüß]\d{1,3}\s*$")
        self.MAX_TOC_LINE_LEN = 150
        self.MAX_TOC_BLOCK = 300
        # ФИКС v3: для laws выключается -- в таблицах законов число приклеено к
        # слову легитимно ("gestalten16" недель, "Kind155" DM), а оглавлений
        # с номерами страниц в HF-датасете нет
        self.detect_glued_toc = True
        # ФИКС v3 (2026-09-29): для bgbl выключается целиком -- после переизвлечения
        # из PDF видно, что строки с точками-заполнителями там -- бюджетные и
        # тарифные ТАБЛИЦЫ ("Bundesverfassungsgericht ..... 01  15 938"), а не
        # оглавления; фильтр выбрасывал до 9% документа
        self.toc_filter = True  # длиннее -- уже похоже на настоящий абзац, не строку оглавления

        # Заготовка для графового RAG: ищет законы (например, "§ 4 Abs. 1 UStG")
        self.ref_pattern = re.compile(r"§§?\s*\d+(?:\s*[a-z]\.)*\s*(?:Abs\.\s*\d+\s*)?[A-Z][a-zA-Z]+")

    # Колонтитул каждой страницы BGBl-PDF -- давал одинаковые чанки-дубли
    _bgbl_footer = re.compile(r"Das Bundesgesetzblatt im Internet:[^\n]*")
    # Союзы после дефиса-сокращения ("Land-\nund Forstwirtschaft")
    _erg_strich = re.compile(r"([a-zA-ZäöüÄÖÜß]+)-\s*\n\s*(und|oder|bzw|sowie|bis|als)\b")

    def sanitize_text(self, text: str) -> str:
        if not text: return ""
        text = re.sub(r"(?i)Seite\s+\d+\s+von\s+\d+", "", text)
        text = self._bgbl_footer.sub("", text)
        # ФИКС v3: строки-номера страниц PDF ("1/15", "Seite 2") -- мусор посреди текста
        text = re.sub(r"(?m)^[ \t]*(?:\d{1,3}[ \t]*/[ \t]*\d{1,3}|Seite[ \t]+\d{1,3})[ \t]*$", "", text)
        # ФИКС v3: неразрывный пробел -- обычный пробел (иначе textwrap не может
        # разрезать таблицы вида "1.1.1929-31.12.1929\xa02\xa0928" -> чанки 2000+ симв.)
        text = text.replace("\xa0", " ")
        # ФИКС v3: мягкий перенос (U+00AD) -- невидимый мусор, убираем; дефис
        # U+2010 на конце строки (bstbl, bmf, fiu) -- обычный перенос, как "-"
        text = text.replace("­\n", "-\n").replace("­", "").replace("‐", "-")
        # ФИКС v3: "Land-\nund" -- это НЕ перенос, а дефис-сокращение
        # (Ergänzungsstrich); старая склейка давала "Landund". Оставляем "Land- und".
        text = self._erg_strich.sub(r"\1- \2", text)
        text = re.sub(r"([a-zA-ZäöüÄÖÜß]+)-\s*\n\s*([a-zäöüß]+)", r"\1\2", text)
        text = re.sub(r"([a-zA-ZäöüÄÖÜß]+)-\s*\n\s*([A-ZÄÖÜ][a-zA-ZäöüÄÖÜß]*)", r"\1-\2", text)
        return text

    def looks_like_toc_entry(self, text: str) -> bool:
        """Проверяет, является ли кусок строкой оглавления (два независимых признака)."""
        if not self.toc_filter:
            return False
        stripped = text.strip()

        matches = self.toc_tail.findall(stripped)
        if matches:
            # Порог снижен до 10%, чтобы гарантированно ловить длинные строки TOC (как в BaFin)
            if sum(len(m) for m in matches) / max(len(stripped), 1) > 0.10:
                return True

        # Второй признак: короткая строка + номер страницы приклеен без
        # разделителя. Ограничение по длине снижает риск ложных срабатываний
        # на легитимном тексте, который случайно кончается словом+цифрой.
        if (self.detect_glued_toc and len(stripped) <= self.MAX_TOC_LINE_LEN
                and self.toc_glued_number.search(stripped)):
            return True

        return False

    # ФИКС v3: разрез по ". " рвал предложения на сокращениях ("(z. " | "B. ...",
    # "BGBl. " | "I S. 3845", "1. " в нумерации, "12. " | "Januar").
    _SENT_END = re.compile(r"[.!?]\s+")
    _ABBR = {
        "z", "b", "vgl", "abs", "nr", "art", "s", "bgbl", "bstbl", "bzw", "rn", "rz",
        "dr", "prof", "d", "h", "u", "a", "i", "v", "ff", "f", "buchst", "ziff", "tz",
        "gem", "evtl", "ggf", "inkl", "incl", "zzgl", "abzgl", "ua", "vs", "insb", "sog", "usw", "etc", "ca", "bspw", "lfd",
        "anm", "aufl", "hrsg", "st", "stpo", "abschn", "kap", "anh", "urt", "beschl",
        "az", "jan", "feb", "apr", "aug", "sept", "okt", "nov", "dez", "mio", "mrd",
    }

    def split_sentences(self, txt: str) -> list[str]:
        """Режет по концу предложения, пропуская сокращения, одиночные буквы,
        порядковые числа/римские цифры и места, где дальше идёт строчная буква."""
        pieces, start = [], 0
        for m in self._SENT_END.finditer(txt):
            end = m.end()
            if txt[m.start()] == ".":
                word = re.search(r"([\wÄÖÜäöüß]+)$", txt[start:m.start()])
                w = word.group(1) if word else ""
                if (w.lower() in self._ABBR or len(w) <= 1 or w.isdigit()
                        or re.fullmatch(r"[IVXLC]+", w)):
                    continue
            if end < len(txt) and txt[end].islower():
                continue
            pieces.append(txt[start:end])
            start = end
        if start < len(txt):
            pieces.append(txt[start:])
        return pieces

    MIN_CHUNK = 150
    # огрызку разрешено раздуть соседа сверх max_chars максимум на MIN_CHUNK --
    # лучше чанк 1300 симв., чем отдельный вектор для "erkennen."
    MERGE_LIMIT_SLACK = MIN_CHUNK

    def merge_small(self, chunks: list[str]) -> list[str]:
        """ФИКС v3: огрызки ("TENOR:", "2.1 Überblick", "1. des Herrn B...,",
        хвост "(z. ") приклеиваются к СЛЕДУЮЩЕМУ чанку (заголовок относится
        к тому, что после него), если влезает в max_chars; иначе к предыдущему.
        Точные дубли внутри документа (>=40 симв.) выкидываются."""
        out, buf = [], ""
        for c in chunks:
            if buf:
                limit = self.max_chars + self.MERGE_LIMIT_SLACK
                if len(buf) + 1 + len(c) <= limit:
                    c = buf.rstrip() + "\n" + c.lstrip()
                elif out and len(out[-1]) + 1 + len(buf) <= limit:
                    out[-1] = out[-1].rstrip() + "\n" + buf.lstrip()
                else:
                    out.append(buf)
                buf = ""
            if len(c.strip()) < self.MIN_CHUNK:
                buf = c
            else:
                out.append(c)
        if buf:
            if out and len(out[-1]) + 1 + len(buf) <= self.max_chars + self.MERGE_LIMIT_SLACK:
                out[-1] = out[-1].rstrip() + "\n" + buf.lstrip()
            else:
                out.append(buf)

        seen, result = set(), []
        for c in out:
            key = c.strip()
            if len(key) >= 40 and key in seen:
                continue
            seen.add(key)
            result.append(c)
        return result

    # Строка оглавления: "Заголовок ......... 12" (номер может быть на следующей строке)
    # {0,150}: строка оглавления короткая -- иначе удалялись целые абзацы-строки
    # решений FG с анонимизацией "....." и числом в конце (потеря до 36%)
    _toc_line = re.compile(r"(?m)^[^\n]{0,150}?[.·_]{5,}[ \t]*\n?[ \t]*\d+[ \t]*$\n?")
    # + строка TOC без номера страницы ("Anwendungsregelung ......") -- ТОЛЬКО
    # для BMF/BaFin: в законах и BGBl так выглядят поля форм ("Hersteller: .....")
    # и строки тарифных таблиц, их вырезать нельзя
    _toc_line_nopage = re.compile(r"(?m)^[^\n]{0,150}?(?:[.·_]{5,}[ \t]*\n?[ \t]*\d+|[.·_]{8,})[ \t]*$\n?")
    strip_toc = False          # построчное вырезание TOC (включено в структурных чанкерах)
    toc_without_page = False   # плюс строки TOC без номера страницы

    def strip_toc_lines(self, text: str) -> str:
        if not self.strip_toc:
            return text
        rx = self._toc_line_nopage if self.toc_without_page else self._toc_line
        return rx.sub("", text)

    def extract_references(self, text: str) -> list[str]:
        """БУДУЩЕЕ: Извлечение ссылок для построения графа связей."""
        return list(set(self.ref_pattern.findall(text)))

    def split_recursive_fallback(self, text: str) -> list[str]:
        """Умная нарезка, если структура не найдена или абзац аномально длинный."""
        if len(text) <= self.max_chars:
            # ВАЖНО (найдено вживую): весь короткий ДОКУМЕНТ целиком (не
            # фрагмент разбиения!) НЕ должен проверяться TOC-фильтром --
            # реальные короткие законы (напр. "Anlage2" на 150 симв.,
            # заканчивающиеся ссылкой на BGBl вида "...925") случайно
            # заканчиваются цифрами и ошибочно отбрасывались целиком, хотя
            # TOC-эвристика задумана для строк ВНУТРИ большого документа,
            # не для атомарного содержимого целого маленького документа.
            return [text] if text.strip() else []

        # ФИКС v3: и здесь сначала вырезаем строки TOC -- иначе фрагмент
        # "преамбула + оглавление" целиком падал в фильтр ниже (BMF без маркеров)
        text = self.strip_toc_lines(text)
        SENT = object()  # маркер: резать по предложениям (split_sentences)
        # ФИКС v3 (spacy_boundary_validator.py): предложения -- РАНЬШЕ одиночного
        # "\n". В PDF-текстах "\n" -- это перенос строки вёрстки, резка по нему
        # рвала предложения ("bis zu einem Betrag von || 10.000 Euro"). "; " перед
        # " " -- естественный разделитель в цепочках цитат BVerfG/BFH.
        separators = ["\n\n", SENT, "\n", "; ", " "]
        def split(txt, seps):
            if len(txt) <= self.max_chars: return [txt]
            if not seps:
                # ФИКС: Бережный перенос по словам, без разрезания строк
                # break_long_words=True -- только для "слов" длиннее max_chars (таблицы без пробелов)
                return textwrap.wrap(txt, width=self.max_chars, break_long_words=True, break_on_hyphens=False)
            sep = seps[0]

            if sep is SENT:
                pieces = self.split_sentences(txt)
            else:
                parts = txt.split(sep)
                pieces = [p + (sep if i < len(parts) - 1 else "") for i, p in enumerate(parts)]
            if len(pieces) == 1: return split(txt, seps[1:])

            result, current = [], ""
            for piece in pieces:
                if len(piece) > self.max_chars:
                    if current:
                        result.append(current)
                        current = ""
                    result.extend(split(piece, seps[1:]))
                    continue
                if len(current) + len(piece) <= self.max_chars:
                    current += piece
                else:
                    if current: result.append(current)
                    current = piece
            if current: result.append(current)
            return result
        
        chunks = split(text, separators)

        # TOC-фильтр применяется ТОЛЬКО к фрагментам, реально родившимся из
        # разбиения большого текста -- именно там (не в целом коротком
        # документе) и живёт настоящая проблема распределённого оглавления.
        return [c for c in chunks if c.strip() and not self.looks_like_toc_entry(c)]


class RegexStructuredChunker(BaseChunker):
    """Общий обработчик для документов с регулярными маркерами структуры."""
    strip_toc = True
    def __init__(self, pattern: str, max_chars=1200):
        super().__init__(max_chars)
        self.pattern = re.compile(pattern)

    def chunk(self, text: str) -> list[str]:
        text = self.sanitize_text(text)
        matches = list(self.pattern.finditer(text))
        
        if not matches:
            return self.split_recursive_fallback(text)

        chunks = []
        # ФИКС v3: преамбулу больше НЕ выбрасываем целиком из-за строк оглавления
        # в ней -- в BMF-письмах она содержит шапку + TOC + первые настоящие
        # абзацы (Rz. 1...), терялось до 8% документа. Вырезаем только сами строки TOC.
        pre_content = self.strip_toc_lines(text[:matches[0].start()]).strip()
        # целиком выбрасываем только КОРОТКИЙ блок -- длинный после strip_toc_lines
        # содержит настоящий текст (преамбулы новых BMF-писем терялись до 21%)
        if pre_content and not (len(pre_content) <= self.MAX_TOC_BLOCK and self.looks_like_toc_entry(pre_content)):
            chunks.extend(self.split_recursive_fallback(pre_content))

        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            paragraph = self.strip_toc_lines(text[start:end]).strip()

            if not paragraph or (len(paragraph) <= self.MAX_TOC_BLOCK and self.looks_like_toc_entry(paragraph)):
                continue
                
            if len(paragraph) > self.max_chars:
                paragraph_flat = re.sub(r"\s*\n\s*", " ", paragraph)
                chunks.extend(self.split_recursive_fallback(paragraph_flat))
            else:
                chunks.append(paragraph)
        return chunks

# --- СПЕЦИФИЧНЫЕ ЧАНКЕРЫ ВЕДОМСТВ ---

class BmfChunker(RegexStructuredChunker):
    toc_without_page = True

    def __init__(self):
        # ФИКС v3: номера разделов -- только 1-2-значные части ("2.4.3"). Было
        # \d+(?:\.\d+){1,3} -- суммы и даты в начале строки PDF ("10.000 Euro",
        # "31.12.2011", "9.600 €") считались началом раздела и рвали предложение.
        super().__init__(r"(?:^|\n)\s*(?:\((\d+)\)|(?:I|V|X|L)+\.|\d{1,2}(?:\.\d{1,2}){1,3}\.?)\s")

class BafinChunker(RegexStructuredChunker):
    toc_without_page = True

    def __init__(self):
        super().__init__(r"(?:^|\n)\s*(?:AT|BT)\s+\d+(?:\.\d+)?\s")



class CourtChunker(RegexStructuredChunker):
    def __init__(self):
        # ФИКС v2 (найдено вживую при проверке перед запуском): версия
        # "\d+\.|[a-z]\)" ловила даты вида "15. Januar 2020" и цитаты вида
        # "Buchstabe a)" как ложные маркеры структуры -- тот же класс
        # проблемы, что раньше чинили для BMF ("Absatz N" внутри ссылки).
        # Убрали [a-z]\) полностью (риск ложных срабатываний на цитатах
        # закона внутри судебных решений не окупается). Для \d+\. добавлено
        # исключение: не маркер, если сразу следует название месяца.
        _months = r"Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
        # ФИКС v3: заголовки разделов решения тоже граница -- иначе "TATBESTAND:"
        # прилипал к ХВОСТУ предыдущего чанка, а не к началу своего раздела.
        _heads = r"TENOR|TATBESTAND|ENTSCHEIDUNGSGRÜNDE|GRÜNDE|Tenor|Tatbestand|Entscheidungsgründe|Gründe"
        super().__init__(
            rf"(?:^|\n)\s*(?:(?:I{{1,3}}|IV|V|VI{{0,3}}|IX|X{{1,3}})+\.|\d+\.(?!\s*(?:{_months}))|\[\d+\]|R[nz]\.\s*\d+|(?:{_heads})\s*:)\s"
        )





class FallbackChunker(BaseChunker):
    def chunk(self, text: str) -> list[str]:
        return self.split_recursive_fallback(self.sanitize_text(text))

def _laws_chunker():
    c = FallbackChunker()
    c.detect_glued_toc = False
    return c


class BgblChunker(FallbackChunker):
    """ФИКС v3 (2026-09-29): после переизвлечения bgbl из PDF (bgbl_reextract.py)
    текст идёт постранично -- колонтитул каждой страницы и номер страницы
    вклинивались посреди предложения, а стык страниц "\n\n" выглядел как
    граница абзаца ("...die enthalten sind || sowie notwendige...")."""
    _page_header = re.compile(
        r"(?m)^[ \t]*(?:\d{1,5}[ \t]*\n[ \t]*)?Bundesgesetzblatt Jahrgang \d{4} Teil I+ Nr\. \d+,"
        r"[^\n]*$(?:\n[ \t]*\d{1,5}[ \t]*$)?\n?")
    # страница/колонка кончилась посреди фразы (нет знака конца предложения) --
    # пустая строка после неё НЕ граница абзаца, если дальше не структурный
    # маркер. Заглавная буква продолжения не признак: немецкие существительные
    # ("Ehegatten oder\n\nLebenspartner die Aufbringung...")
    _page_join = re.compile(
        r"([A-Za-zÄÖÜäöüß,;\-–])[ \t]*\n(?:[ \t]*\n)+[ \t]*"
        r"(?!§|Art(?:ikel)?\b|Abschnitt|Anlage|Kapitel|Teil\b|Unterabschnitt|\(?\d+[a-z]?[.)]|[a-z]{1,3}\)|[IVX]+\.)"
        r"(?=\S)")

    def __init__(self):
        super().__init__()
        self.toc_filter = False
        self.detect_glued_toc = False

    # начало строки, которое НЕ продолжение фразы, а новый структурный элемент
    _struct_start = re.compile(
        r"^(?:§|Art(?:ikel)?\b|Abschnitt|Anlage|Kapitel|Teil\b|Unterabschnitt|"
        r"\(?\d+[a-z]?[.)](?!\s*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember))(?:\s|$)"
        r"|[a-z]{1,3}\)(?:\s|$)|[IVX]+\.(?:\s|$)|[-–•]\s)")
    MIN_WRAPPED_LINE = 35  # короче -- скорее ячейка таблицы/заголовок, не перенос абзаца

    def unwrap_lines(self, text: str) -> str:
        """Мягкие переносы строк PDF-колонки -> сплошной текст. Иначе при длинном
        предложении (>1200) резка шла по произвольному переносу вёрстки."""
        lines = text.split("\n")
        out = []
        prev_raw = ""  # ИСХОДНАЯ предыдущая строка (не накопленная склейка)
        for line in lines:
            prev = out[-1] if out else ""
            wrapped = len(prev_raw.strip()) >= self.MIN_WRAPPED_LINE
            prev_raw = line
            if (prev and line.strip() and wrapped
                    and not re.search(r"[.:;!?“\"]\s*$", prev)
                    and not self._struct_start.match(line.strip())):
                if prev.rstrip().endswith("-") and line.strip()[:1].islower():
                    out[-1] = prev.rstrip()[:-1] + line.strip()   # перенос слова
                else:
                    out[-1] = prev.rstrip() + " " + line.strip()
            else:
                out.append(line)
        return "\n".join(out)

    def sanitize_text(self, text: str) -> str:
        text = self._page_header.sub("", text or "")
        text = self._page_join.sub(r"\1\n", text)
        text = super().sanitize_text(text)
        return self.unwrap_lines(text)


def _bgbl_chunker():
    return BgblChunker()


# --- РОУТЕР ---

class DocumentRouter:
    def __init__(self):
        self.chunkers = {
            "bmf": BmfChunker(),
            "bstbl": BmfChunker(),
            "bundesbank": BmfChunker(),
            "fiu_reports": BmfChunker(),
            "bafin": BafinChunker(),
            "bfh": CourtChunker(),
            "bverfg": CourtChunker(),
            "finanzgericht": CourtChunker(),
            # ФИКС v3: решения EuGH и BGH раньше шли в fallback -- номер абзаца
            # ("2.") оставался в конце одного чанка, а текст абзаца уходил в следующий
            "eugh": CourtChunker(),
            "bgh_criminal": CourtChunker(),  # ФИКС: в БД поле source='finanzgericht' (ед.ч.), было 'finanzgerichte' -- 15961 документ молча уходили в fallback
            "bgbl": _bgbl_chunker(),
            "laws": _laws_chunker(), # Законы уже порезаны по параграфам, их просто чистим
        }
        self.fallback = FallbackChunker()

    def route_and_chunk(self, source: str, text: str) -> list[str]:
        chunker = self.chunkers.get(source, self.fallback)
        return chunker.merge_small(chunker.chunk(text))


# ==========================================
# 2. ДВИЖОК БД И МУЛЬТИ-GPU
# ==========================================

DB_CONFIG = {
    "dbname": "steuer_corpus_test",
    "user": "postgres",
    "password": os.environ.get("PGPASSWORD"),
    "host": "localhost",
    "port": "5432",
}
MODEL_NAME = "BAAI/bge-m3"
CHUNK_BUDGET = 2000
STATE_FILE = Path("_rebuild_progress.json")

def flush_batch(conn, model, pool, pending_docs, pending_chunks, done_ids):
    if not pending_docs: return 0
        
    chunk_texts = [c[1] for c in pending_chunks]
    print(f"  [GPU] Векторизация {len(chunk_texts)} чанков (batch=64)...")
    
    # batch_size=64 -- проверено на этой же связке (мультипроцессный пул +
    # 2xQuadro M4000) ранее сегодня: 96 давал CUDA OOM, 32 работал слишком
    # осторожно, 64 -- рабочий безопасный компромисс без сбоев.
    embeddings = model.encode(chunk_texts, pool=pool, batch_size=64, convert_to_numpy=True)
    
    chunks_by_doc_id = {}
    for (db_id, chunk_txt), emb in zip(pending_chunks, embeddings):
        chunks_by_doc_id.setdefault(db_id, []).append((chunk_txt, emb))

    saved = 0
    cur = conn.cursor()
    for db_id, doc_id in pending_docs:
        try:
            cur.execute("BEGIN;")
            cur.execute("DELETE FROM document_chunks WHERE document_id = %s;", (db_id,))

            doc_chunks = chunks_by_doc_id.get(db_id, [])
            if doc_chunks:
                # Один вызов execute_values на ВЕСЬ документ разом, вместо
                # цикла с отдельным INSERT на каждый чанк -- найдено вживую
                # (по температуре GPU!): при большом batch_size вычисление
                # эмбеддингов стало заметно быстрее записи в БД, и карты
                # начали простаивать/остывать именно во время записи.
                # Подсказка: если в БД добавить колонку metadata (JSONB),
                # сюда можно писать router.fallback.extract_references(chunk_txt)
                values = [
                    (db_id, chunk_idx, chunk_txt, emb.tolist())
                    for chunk_idx, (chunk_txt, emb) in enumerate(doc_chunks)
                ]
                execute_values(
                    cur,
                    "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding) VALUES %s;",
                    values,
                )

            cur.execute("COMMIT;")
            saved += 1
            done_ids.add(doc_id)
        except Exception as e:
            cur.execute("ROLLBACK;")
            print(f"  [!] Ошибка записи в БД ({doc_id}): {e}")
            
    cur.close()
    
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(done_ids), f)
        
    return saved, len(chunk_texts)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="+", default=None,
                         help="Ограничить конкретными source (для разделения работы с параллельным CPU-воркером). По умолчанию -- все.")
    args = parser.parse_args()

    done_ids = set()
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            done_ids = set(json.load(f))
        print(f"🔄 Резюмирую работу: {len(done_ids)} документов пропущено.")

    print(f"Загружаю модель {MODEL_NAME} и стартую Multi-GPU пул...")
    model = SentenceTransformer(MODEL_NAME, device="cuda", model_kwargs={"use_safetensors": True})
    pool = model.start_multi_process_pool()
    router = DocumentRouter()
    
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()
    
    if args.sources:
        print(f"Ограничиваю источниками: {', '.join(args.sources)}")
        # Приоритет: сначала источники, критичные для полноценного теста
        # системы (laws/bmf/bstbl/eugh/eu_vat/bfh/bverfg -- тот самый
        # "Вариант А" из более раннего разговора), самые тяжёлые и менее
        # срочные для проверки (bgbl, finanzgericht) -- в конец очереди.
        # Чтобы не наступить на те же грабли снова: если их не отложить,
        # они (773 тыс. чанков, больше половины всей работы GPU) съедят
        # первую половину суток, и до нужных для теста источников можно
        # не дойти долго.
        cur.execute("""
            SELECT id, doc_id, source, full_text FROM documents
            WHERE full_text IS NOT NULL AND source = ANY(%s)
            ORDER BY CASE WHEN source IN ('bgbl', 'finanzgericht') THEN 1 ELSE 0 END, id;
        """, (args.sources,))
    else:
        # Глобальный охват: тянем всю базу, роутер сам рассортирует
        cur.execute("SELECT id, doc_id, source, full_text FROM documents WHERE full_text IS NOT NULL;")
    documents = cur.fetchall()
    cur.close()
    
    total_docs = len(documents)
    start_time = time.time()
    pending_docs, pending_chunks = [], []
    total_processed = 0
    total_chunks_processed = 0

    def print_speed():
        elapsed_min = (time.time() - start_time) / 60
        if elapsed_min <= 0:
            return
        docs_per_min = total_processed / elapsed_min
        chunks_per_min = total_chunks_processed / elapsed_min
        # было total_docs - i - len(done_ids): i уже включает пропущенные done_ids -> ETA уходил в минус
        remaining_docs = total_docs - i
        eta_min = remaining_docs / docs_per_min if docs_per_min > 0 else float("inf")
        print(f"  ⏱  Скорость: {docs_per_min:.1f} документов/мин, {chunks_per_min:.0f} чанков/мин "
              f"| Прошло: {elapsed_min:.1f} мин | Осталось примерно: {eta_min:.0f} мин ({eta_min/60:.1f} ч)")

    for i, (db_id, doc_id, source, full_text) in enumerate(documents, 1):
        if doc_id in done_ids: continue
            
        new_chunks = router.route_and_chunk(source, full_text)
        if not new_chunks: continue
            
        pending_docs.append((db_id, doc_id))
        for c in new_chunks:
            pending_chunks.append((db_id, c))
            
        if len(pending_chunks) >= CHUNK_BUDGET:
            flushed_docs, flushed_chunks = flush_batch(conn, model, pool, pending_docs, pending_chunks, done_ids)
            total_processed += flushed_docs
            total_chunks_processed += flushed_chunks
            pending_docs.clear()
            pending_chunks.clear()
            print(f"  ...пройдено {i}/{total_docs} документов")
            print_speed()

    if pending_docs:
        flushed_docs, flushed_chunks = flush_batch(conn, model, pool, pending_docs, pending_chunks, done_ids)
        total_processed += flushed_docs
        total_chunks_processed += flushed_chunks
        
    model.stop_multi_process_pool(pool)
    conn.close()
    
    elapsed = (time.time() - start_time) / 60
    print(f"\n✅ Глобальная пересборка завершена! Обработано {total_processed} документов за {elapsed:.1f} мин.")

if __name__ == "__main__":
    main()
