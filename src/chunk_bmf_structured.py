#!/usr/bin/env python3
"""
chunk_bmf_structured.py -- структурно-осознанный чанкинг для официальных
писем с нумерованными абзацами (BMF-Schreiben, возможно BaFin/Bundesbank).

Вместо слепых окон по 1000 символов -- режет ПО ГРАНИЦАМ нумерованных
абзацев "(1)", "(2)" и т.д., которые в таких документах уже являются
самостоятельными смысловыми единицами. Это устраняет Context Isolation --
проблему, найденную вживую: старый чанкинг разрезал предложение со словом
"Berufsbezug" ровно пополам между двумя соседними чанками.

Если отдельный пронумерованный абзац сам по себе длиннее разумного предела --
дробим его дальше через уже существующий _split_recursive (по абзацам/
предложениям), не вслепую по символам.
"""

import re


CHUNK_MAX_CHARS = 1200  # чуть больше, чем в load_v3_2.py -- абзацы BMF часто длиннее 1000


def _split_recursive_fallback(text: str, max_chars: int) -> list[str]:
    """Тот же приём, что в load_v3_2.py -- на случай, если пронумерованный абзац сам слишком длинный."""
    if len(text) <= max_chars:
        return [text]
    separators = ["\n\n", "\n", ". ", " "]

    def split(text, seps):
        if len(text) <= max_chars:
            return [text]
        if not seps:
            return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
        sep = seps[0]
        parts = text.split(sep)
        if len(parts) == 1:
            return split(text, seps[1:])
        result, current = [], ""
        for i, part in enumerate(parts):
            piece = part + (sep if i < len(parts) - 1 else "")
            if len(piece) > max_chars:
                if current:
                    result.append(current)
                    current = ""
                result.extend(split(piece, seps[1:]))
                continue
            if len(current) + len(piece) <= max_chars:
                current += piece
            else:
                if current:
                    result.append(current)
                current = piece
        if current:
            result.append(current)
        return result

    return split(text, separators)


def sanitize_text(text: str) -> str:
    """
    Чистит артефакты PDF-извлечения ДО чанкинга: колонтитулы "Seite X von Y"
    и слова, разорванные переносом строки посреди слова (частый артефакт
    PDF-парсинга многоколоночных/выровненных официальных писем).
    """
    if not text:
        return ""
    text = re.sub(r"(?i)Seite\s+\d+\s+von\s+\d+", "", text)
    text = re.sub(r"([a-zA-ZäöüÄÖÜß]+)-\s*\n\s*([a-zäöüß]+)", r"\1\2", text)
    text = re.sub(r"([a-zA-ZäöüÄÖÜß]+)-\s*\n\s*([A-ZÄÖÜ][a-zA-ZäöüÄÖÜß]*)", r"\1-\2", text)
    return text


_TOC_TAIL = re.compile(r"[.·_]{5,}\s*\d+")


def _looks_like_toc_entry(text: str) -> bool:
    """
    Проверяет, не является ли этот КОНКРЕТНЫЙ получившийся кусок просто
    одной строкой оглавления, ставшей отдельным "абзацем" из-за того же
    номера-маркера, что мы используем для реальной структуры (найдено
    вживую на MaComp/BaFin -- там оглавление НЕ одним блоком в начале, а
    рассредоточено по всему документу перед каждым крупным разделом,
    поэтому единая "граница оглавления" не работает, нужна проверка
    каждого куска отдельно).
    """
    matches = _TOC_TAIL.findall(text)
    if not matches:
        return False
    matched_len = sum(len(m) for m in matches)
    # Если совпадения "точки+номер" занимают заметную долю всего текста --
    # это не абзац с редкой отсылкой к странице, а строка оглавления целиком.
    return matched_len / max(len(text), 1) > 0.15


def chunk_structured_document(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """
    Режет по границам нумерованных абзацев "(N) " и римских разделов
    "I.", "II." -- каждый такой абзац становится отдельным чанком (или
    несколькими, если сам слишком длинный).

    НЕ используем "\\bAbsatz\\s+[0-9]+" как маркер границы -- проверено
    вживую: это ложно режет текст на внутритекстовых ссылках вида
    "vgl. Abschnitt 4.21.1 Absatz 2", если перед ними случайно оказался
    перенос строки (частый артефакт PDF) -- воспроизводит ту же проблему
    Context Isolation, которую мы и чиним, просто в другом месте.
    """
    text = sanitize_text(text)

    # Пять паттернов нумерации, найденные на реальных документах разных
    # источников: "(N) " (BMF/Berufsbezug), "I./II." (разделы верхнего
    # уровня), "N.N.N " (десятичная иерархия, BMF/Betriebsstätte), "N.N. "
    # с точкой в конце (BStBl), "AT N" / "AT N.N" (BaFin, "Allgemeiner Teil").
    pattern = re.compile(r"(?:^|\n)\s*(?:\((\d+)\)|(?:I|V|X|L)+\.|AT\s+\d+(?:\.\d+)?|\d+(?:\.\d+){1,3}\.?)\s")
    matches = list(pattern.finditer(text))

    if not matches:
        return _split_recursive_fallback(text, max_chars)

    chunks = []
    pre_content = text[:matches[0].start()].strip()
    if pre_content and not _looks_like_toc_entry(pre_content):
        chunks.extend(_split_recursive_fallback(pre_content, max_chars))

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        paragraph = text[start:end].strip()
        if not paragraph:
            continue
        # Фильтруем строки оглавления, ставшие отдельными "абзацами" из-за
        # того же номера-маркера -- найдено вживую на MaComp/BaFin, где
        # оглавление рассредоточено перед каждым крупным разделом, не одним
        # блоком в начале (единая глобальная граница тут не работает).
        if _looks_like_toc_entry(paragraph):
            continue
        # ВАЖНО (найдено вживую): раз мы уже определили этот текст как
        # ОДИН смысловой абзац по номеру "(N)", внутренние переносы строк
        # внутри него -- чисто артефакт вёрстки PDF (не структура), но
        # запасной разбивщик (при превышении max_chars) резал именно по
        # ним, разрывая фразы вроде "direkten \nGewerbe-" пополам. Схлопы-
        # ваем внутренние переносы в пробелы ДО запасного разбиения -- тогда
        # оно сможет резать только по границам предложений, не по вёрстке.
        if len(paragraph) > max_chars:
            paragraph_flat = re.sub(r"\s*\n\s*", " ", paragraph)
            chunks.extend(_split_recursive_fallback(paragraph_flat, max_chars))
        else:
            chunks.append(paragraph)

    return chunks
