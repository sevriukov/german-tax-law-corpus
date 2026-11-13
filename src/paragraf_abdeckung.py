#!/usr/bin/env python3
"""paragraf_abdeckung.py -- покрытие «уровня комментария» (18.10.2026, вопрос владельца проекта о сравнении с профессором): по КАЖДОМУ действующему параграфу
основных налоговых законов -- есть ли (1) прежние редакции, (2) решения BFH/FG с этой нормой в карточке, (3) указания администрации
(справочники BMF, письма, BStBl) с упоминанием нормы, (4) обоснования законопроектов с упоминанием, (5) книги (база литературы) с упоминанием.
20.10: обоснование считается и по «Zu § N» внутри статьи законопроекта: закон берётся из заголовка статьи («Artikel 3 Änderung des
Einkommensteuergesetzes» / «Zu Artikel 3 (Änderung des …)»), голые «§ N» в разделе относятся к этому закону. Полный список «голых»
параграфов по закону -> quellen_neu/paragraf_nackt_liste.json (для целевого сбора, этап 2.2).
Только чтение. -> quellen_neu/paragraf_abdeckung.json (по закону: доли параграфов с каждым признаком и «голые» параграфы)
"""
import collections
import json
import re

import psycopg2

GESETZE = ["AO", "EStG", "KStG", "GewStG", "UStG", "ErbStG", "BewG", "GrEStG", "UmwStG", "AStG", "FGO", "GrStG", "InvStG"]
REF = re.compile(r"§§?\s*(\d+[a-z]?)(?:\s*(?:Abs\.|Absatz)\s*\d+[a-z]?)?(?:\s*(?:Satz|S\.)\s*\d+)?(?:\s*(?:Nr\.|Nummer)\s*\d+[a-z]?)?(?:\s*(?:Buchst\.|Buchstabe)\s*[a-z])?\s+(" + "|".join(GESETZE) + r")\b")
LANG = {"Abgabenordnung": "AO", "Einkommensteuergesetz": "EStG", "Körperschaftsteuergesetz": "KStG", "Gewerbesteuergesetz": "GewStG",
        "Umsatzsteuergesetz": "UStG", "Erbschaftsteuer- und Schenkungsteuergesetz": "ErbStG", "Bewertungsgesetz": "BewG",
        "Grunderwerbsteuergesetz": "GrEStG", "Umwandlungssteuergesetz": "UmwStG", "Außensteuergesetz": "AStG", "Finanzgerichtsordnung": "FGO",
        "Grundsteuergesetz": "GrStG", "Investmentsteuergesetz": "InvStG"}
ART = re.compile(r"(?:Zu\s+)?Artikel\s+\d+[a-z]?\s*\(?\s*(?:Änderung|Neufassung|Ergänzung)\s+(?:des|der)\s+(" +
                 "|".join(map(re.escape, sorted(LANG, key=len, reverse=True))) + r")(?:es|s)?\b")
NUR_PARA = re.compile(r"§§?\s*(\d+[a-z]?)\b(?!\s*(?:Abs\.|Absatz)?[^§]{0,40}?\b(?:" + "|".join(GESETZE) + r")\b)")


def zu_artikel(txt):
    """(закон, номер) из разделов законопроекта: голые «§ N» после заголовка статьи об изменении закона, до следующей статьи."""
    out = set()
    pos = [(m.start(), LANG[m.group(1)]) for m in ART.finditer(txt)]
    alle = [m.start() for m in re.finditer(r"(?:Zu\s+)?Artikel\s+\d+[a-z]?\b", txt)]
    for a, law in pos:
        ende = next((b for b in alle if b > a + 10), len(txt))
        for m in NUR_PARA.finditer(txt, a, min(ende, a + 200000)):
            out.add((law, m.group(1)))
    return out


def main():
    con = psycopg2.connect(host="localhost", user="postgres", dbname="steuer_corpus_test")
    cur = con.cursor()
    cur.execute("SET statement_timeout = '3h'")
    para = collections.defaultdict(dict)        # (law, nr) -> признаки
    for law in GESETZE:
        cur.execute("SELECT title FROM documents WHERE source='laws' AND valid_until IS NULL AND NOT (metadata ? 'formel') AND length(full_text) >= 120 "
                    "AND title ~ %s", (rf"^{law}( [0-9]{{4}})? § [0-9]",))
        for (t,) in cur.fetchall():
            m = re.match(rf"^{law}(?: \d{{4}})? § (\d+[a-z]?)", t)
            if m:
                para[(law, m.group(1))] = {"hist": 0, "urteile": 0, "verwaltung": 0, "begruendung": 0, "literatur": 0}
    cur.execute("SELECT title FROM documents WHERE source='laws_historie'")
    for (t,) in cur.fetchall():
        m = re.match(r"^(\S+)(?: \d{4})? § (\d+[a-z]?)", t or "")
        if m and (m.group(1), m.group(2)) in para:
            para[(m.group(1), m.group(2))]["hist"] += 1
    cur.execute("SELECT t.norms FROM document_titles t JOIN documents d ON d.id=t.document_id WHERE d.source IN ('bfh','finanzgericht') AND t.kind='card'")
    for (ns,) in cur.fetchall():
        for n in set(ns or []):
            m = re.match(r"§\s*(\d+[a-z]?).*?\b(" + "|".join(GESETZE) + r")$", n.strip())
            if m and (m.group(2), m.group(1)) in para:
                para[(m.group(2), m.group(1))]["urteile"] += 1
    for key, srcs, db in (("verwaltung", ("bmf_handbuecher", "bmf", "bstbl", "bmf_formulare", "bzst"), "steuer_corpus_test"),
                          ("begruendung", ("bt_drucksachen",), "steuer_corpus_test"), ("literatur", None, "steuer_literatur")):
        c2 = psycopg2.connect(host="localhost", user="postgres", dbname=db).cursor(name=f"c_{key}")
        if srcs:
            c2.execute("SELECT full_text FROM documents WHERE source = ANY(%s)", (list(srcs),))
        else:
            c2.execute("SELECT full_text FROM documents")
        for (txt,) in c2:
            refs = {(law, nr) for nr, law in REF.findall(txt or "")}
            if key == "begruendung":
                refs |= zu_artikel(txt or "")
            for k in refs:
                if k in para:
                    para[k][key] += 1
        print(key, "готово", flush=True)
    res = {}
    for law in GESETZE:
        ps = {k: v for k, v in para.items() if k[0] == law}
        n = len(ps) or 1
        nackt = sorted((k[1] for k, v in ps.items() if not v["urteile"] and not v["verwaltung"]), key=lambda x: (int(re.match(r"\d+", x).group()), x))
        res[law] = {"paragraphen": len(ps), **{f"mit_{f}": round(100 * sum(1 for v in ps.values() if v[f]) / n) for f in ("hist", "urteile", "verwaltung", "begruendung", "literatur")},
                    "voll": round(100 * sum(1 for v in ps.values() if v["urteile"] and v["verwaltung"] and v["begruendung"]) / n),
                    "ohne_urteil_und_verwaltung": len(nackt), "beispiele_nackt": nackt[:25]}
        print(law, json.dumps({k: v for k, v in res[law].items() if k != "beispiele_nackt"}), flush=True)
    json.dump({law: {f: sorted((k[1] for k, v in para.items() if k[0] == law and not v[f]), key=lambda x: (int(re.match(r"\d+", x).group()), x))
                     for f in ("urteile", "verwaltung", "begruendung")} for law in GESETZE},
              open("quellen_neu/paragraf_nackt_liste.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(res, open("quellen_neu/paragraf_abdeckung.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("PARAGRAF_DONE", flush=True)


if __name__ == "__main__":
    main()
