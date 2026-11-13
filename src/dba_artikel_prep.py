#!/usr/bin/env python3
"""dba_artikel_prep.py (19.10.2026, владелец проекта «1а»: DBA делить на статьи). Находимость по международному налоговому праву 45 %:
вопрос о конкретной статье соглашения (Betriebsstätte, Dividenden, Arbeitnehmer …) ищется в целом документе на 30–150 тыс. знаков.
Каждый документ source='dba' из базы -> части по строкам «Artikel N» (отдельной строкой); счёт статей с 1 заново = новая часть
(Vertragsgesetz -> Abkommen -> Protokoll …). Название части -- по словам в тексте перед первой статьёй.
Новый документ на статью: doc_id <doc_id>_t<часть>_art<N>, source dba, заголовок «DBA Deutschland – Land: Abkommen Artikel N Überschrift (…)».
Статьи не на немецком (мало немецких служебных слов) -- пропуск. Целые документы остаются (сравнение находимости до/после).
    python3 dba_artikel_prep.py   -> de_law_corpus/prepared/dba_artikel.jsonl
"""
import json
import re

import psycopg2

ART = re.compile(r"(?m)^[ \t]*Artikel[ \t]+(\d+[a-z]?)[ \t]*$")
DE = re.compile(r"(?i)\b(der|die|das|und|des|nicht|wird|werden|einer|eines|oder|in|zu|den|dem)\b")


FREMD = re.compile(r"(?m)^[ \t]*(Article|Artículo|Articolo|Artigo|Artikkel|Artikel[ \t]+\d+[ \t]*\n[ \t]*Article)[ \t]+\d+")


def teilname(vor, erster):
    """19.10: по 400 знакам перед первой статьёй части и по первой статье (Vertragsgesetz -- «wird zugestimmt»)"""
    if re.search(r"wird\s+(hiermit\s+)?zugestimmt|tritt am Tag nach (der|seiner) Verkündung", erster[:1500]):
        return "Vertragsgesetz"
    v = vor[-400:]
    for w, n in (("Zusatzprotokoll", "Zusatzprotokoll"), ("nderungsprotokoll", "Änderungsprotokoll"), ("Protokoll", "Protokoll"),
                 ("Notenwechsel", "Notenwechsel"), ("Verordnung", "Verordnung")):
        if w in v:
            return n
    return "Abkommen"


def main():
    con = psycopg2.connect(host="localhost", port=5432, user="postgres", dbname="steuer_corpus_test")
    cur = con.cursor()
    cur.execute("SELECT doc_id, title, full_text, url, valid_from, valid_until, effective_date FROM documents WHERE source='dba' AND doc_id !~ '_art[0-9]'")
    n_dok = n_art = n_fremd = 0
    with open("de_law_corpus/prepared/dba_artikel.jsonl", "w", encoding="utf-8") as out:
        for doc_id, title, txt, url, vf, vu, eff in cur.fetchall():
            ms = list(ART.finditer(txt or ""))
            if len(ms) < 3:
                continue
            n_dok += 1
            land = re.sub(r":.*", "", title).strip()
            teil, letzte, name = 0, None, teilname(txt[:ms[0].start()], txt[ms[0].end():ms[1].start()])
            for i, m in enumerate(ms):
                nr = m.group(1)
                if letzte is not None and nr in ("1", "1a") and letzte not in ("1",):
                    teil += 1
                    name = teilname(txt[max(0, m.start() - 3000):m.start()], txt[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(txt)])
                letzte = nr
                ende = ms[i + 1].start() if i + 1 < len(ms) else min(len(txt), m.end() + 20000)
                koerper = txt[m.end():ende].strip()
                f = FREMD.search(koerper)          # двуязычные издания: со строки «Article N» начинается чужой текст
                if f and f.start() > 40:
                    koerper = koerper[:f.start()].strip()
                elif f:
                    koerper = re.sub(r"^\s*(Article|Artículo|Articolo|Artigo|Artikkel)\s+\d+[a-z]?\s*\n", "", koerper)
                kopf, _, rest = koerper.partition("\n")
                if len(kopf) > 120 or kopf.lstrip()[:1] in "({[0123456789" or not re.search(r"[A-Za-zÄÖÜäöü]{4}", kopf):
                    kopf, rest = "", koerper
                kopf = re.sub(r"\s+", " ", kopf).strip()
                if re.match(r"(Article|Artículo|Articolo|Artigo)\b", kopf):
                    kopf = ""
                if len(rest) < 40:
                    continue
                if len(DE.findall(rest[:2000])) < max(3, len(rest[:2000]) // 250):
                    n_fremd += 1
                    continue
                # 19.10: часть закона о ратификации узнаётся по самой статье; иначе Vertragsgesetz не «прилипает» к статьям соглашения
                gesetz = re.search(r"wird\s+(hiermit\s+)?zugestimmt|[Dd]ieses Gesetz tritt|Verkündung in Kraft|im Bundesgesetzblatt bekannt", rest[:1500])
                zeig = "Vertragsgesetz" if gesetz else ("Abkommen" if name == "Vertragsgesetz" else name)
                quelle = re.sub(r"^[^:]*:\s*", "", title).strip()
                out.write(json.dumps({
                    "doc_id": f"{doc_id}_t{teil}_art{nr}"[:250], "source": "dba", "tier": 1,
                    "title": f"{land}: {zeig} Artikel {nr} {kopf} ({quelle})".replace("  ", " ")[:400],
                    "text": f"{land}, {zeig}\nArtikel {nr} {kopf}\n\n{rest}", "origin": url,
                    "valid_from": str(vf) if vf else None, "valid_until": str(vu) if vu else None,
                    "effective_date": str(eff) if eff else None, "law_state_year": eff.year if eff else None,
                    "teil_von": doc_id}, ensure_ascii=False) + "\n")
                n_art += 1
    print(f"DBA_ARTIKEL_PREP: документов {n_dok}, статей {n_art}, не на немецком пропущено {n_fremd}", flush=True)


if __name__ == "__main__":
    main()
