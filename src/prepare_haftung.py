#!/usr/bin/env python3
"""
prepare_haftung.py -- решения об ответственности налоговых консультантов -> de_law_corpus/prepared/*.jsonl (формат load_new_sources.py).

  olg_haftung      суды земель (OLG, LG, AG): официальная база NRWE (quellen_neu/haftung/nrwe/*.html)
                   + решения других земель из Open Legal Data (quellen_neu/haftung/old/*.json; ODbL, тексты решений -- § 5 UrhG)
  bgh_haftung_alt  BGH, IX сенат, 2000-2009 с сайта суда (quellen_neu/haftung/bgh_alt/*.pdf); источник в базе -- bgh_haftung

Отбор строже, чем при первом просмотре (auswerten.py): слово «Steuerberater» должно встречаться плотно,
иначе в выборку попадают посторонние дела, где консультант лишь упомянут (пример: LG Dortmund 2 O 387/14, 800 тыс. знаков).
    python3 prepare_haftung.py        # только подготовка файлов, в базу не пишет
"""
import glob
import html
import json
import os
import re
import subprocess
from pathlib import Path

H = Path("quellen_neu/haftung")
PREP = Path("de_law_corpus/prepared")
NORM = re.compile(r"§ ?\d+[a-z]? (?:Abs\. ?\d+ )?(?:StBerG|BGB|AO|EStG|UStG|ZPO|VVG|SGB [IVX]+|GmbHG|InsO|HGB|StBGebV|StBVV)")
THEMEN = {"Fristversäumnis": r"Einspruchsfrist|Klagefrist|Fristversäum|Frist versäumt", "Schätzung, verspätete Erklärung": r"Schätzungsbescheid|Schätzung der Besteuerungsgrundlagen|Verspätungszuschl",
          "Säumniszuschläge und Zinsen als Schaden": r"Säumniszuschl|Nachzahlungszinsen|Aussetzungszinsen", "Betriebsprüfung, Kassenführung": r"Betriebsprüfung|Außenprüfung|Kassenführung|Hinzuschätzung",
          "Sozialversicherung": r"Sozialversicherung|Krankenkasse|Krankenversicherung|Rentenversicherung", "Umsatzsteuer": r"Kleinunternehmer|Vorsteuer|Umsatzsteuer",
          "Mandatsumfang, Hinweispflicht": r"Dauermandat|Mandatsumfang|Umfang des Mandats|Warnpflicht|Hinweispflicht", "Beweislast, Kausalität": r"Anscheinsbeweis|beratungsgerechten Verhaltens|Beweislast",
          "Schadensberechnung": r"Gesamtvermögensvergleich|Differenzhypothese|Vorteilsausgleich", "Mitverschulden": r"Mitverschulden|§ 254 BGB", "Verjährung": r"Verjährung",
          "Vorberater, Mandatswechsel": r"Vorberater|Nachfolgeberater|Mandatswechsel|neuen Steuerberater", "Insolvenz": r"Insolvenzreife|Insolvenzverschleppung|Insolvenzvertiefung",
          "Berufshaftpflichtversicherung": r"Berufshaftpflichtversicher|Deckungsschutz", "Handakten, Herausgabe": r"Handakte|Zurückbehaltungsrecht", "Honorar": r"Honoraranspruch|Vergütungsanspruch|Gebührenanspruch"}
KURZ = (("Oberlandesgericht", "OLG"), ("Landgericht", "LG"), ("Amtsgericht", "AG"), ("Kammergericht", "KG"), ("Landesarbeitsgericht", "LAG"), ("Bundesgerichtshof", "BGH"))


def kurz(court):
    for a, b in KURZ:
        court = court.replace(a, b)
    return court


def relevant(t):
    n = len(re.findall(r"Steuerberater|steuerliche[rn]? Berater", t))
    dichte = 1000 * n / max(len(t), 1)
    haft = re.search(r"Schadensersatz|Schadenersatz|Regress", t) and re.search(r"Pflichtverletzung|pflichtwidrig|Beratungsfehler|Beratungspflicht|Hinweispflicht|Pflichten aus dem (Steuerberat|Mandat)", t)
    vertrag = len(re.findall(r"Steuerberatervertrag|Steuerberatungsvertrag|Beratungsvertrag|Mandatsvertrag", t))
    return bool(haft) and len(t) < 300_000 and n >= 5 and (dichte >= 0.3 or (dichte >= 0.12 and vertrag >= 2))


def karte(court, az, iso, body, leit=""):
    norms = sorted(set(NORM.findall(body)), key=body.count, reverse=True)[:5]
    them = [k for k, rx in THEMEN.items() if re.search(rx, body)][:6]
    if not leit:
        m = re.search(r"[^.]{0,200}(?:beklagte[nr]? Steuerberater|Steuerberater[^.]{0,80}(?:auf Schadensersatz|in Anspruch|Regress)|Schadensersatz[^.]{0,120}Steuerberat)[^.]{0,200}\.", body)
        leit = m.group(0) if m else body[:300]
    return f"{court} {az} ({iso}). Normen: {', '.join(norms)}. Themen: {'; '.join(them)}. {' '.join(leit.split())[:330]}"


def nrwe():
    meta = {re.sub(r"[^\w.-]+", "_", x["url"].split("nrwe.justiz.nrw.de/")[-1]): x for x in json.load(open(H / "nrwe_treffer.json", encoding="utf-8"))}
    for f in sorted(glob.glob(str(H / "nrwe" / "*.html"))):
        m = meta.get(os.path.basename(f))
        if not m:
            continue
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", open(f, encoding="utf-8", errors="ignore").read(), flags=re.S | re.I)
        lines = [" ".join(l.split()) for l in html.unescape(re.sub(r"<[^>]+>", "\n", raw)).split("\n")]
        lines = [l for l in lines if l and l not in ("Seite drucken", "Entscheidung als PDF runterladen")]
        try:
            i = next(k for k, l in enumerate(lines) if l == "ECLI:" or l.startswith("ECLI:DE"))
        except StopIteration:
            i = 0
        body_lines = [l for l in lines[i + 1:] if not re.fullmatch(r"\d{1,4}", l)]          # номера абзацев отдельной строкой -- убрать
        if body_lines and body_lines[0].startswith("ECLI:"):
            body_lines = body_lines[1:]
        body = "\n".join(body_lines)
        if len(body) < 1500 or not relevant(body):
            continue
        d, mth, y = m["date"].split(".")
        leit = ""
        ml = re.search(r"Leitsätze:\n(.*?)\n(?:Tenor:|Normen:|Schlagworte:|Vorinstanz:)", body, re.S)
        if ml:
            leit = ml.group(1)
        az_id = re.sub(r"[^\w]+", "_", m["az"]).strip("_")
        yield {"doc_id": f"olg_haftung_{y}{mth}{d}_{re.sub(r'[^A-Za-z]+', '', kurz(m['court']))[:14]}_{az_id}", "source": "olg_haftung", "tier": 1,
               "title": karte(kurz(m["court"]), m["az"], f"{y}-{mth}-{d}", body, leit), "text": body, "origin": m["url"], "law_state_year": int(y),
               "az": m["az"], "court": m["court"]}


def old(seen_az):
    for f in sorted(glob.glob(str(H / "old" / "*.json"))):
        j = json.load(open(f, encoding="utf-8"))
        court = (j.get("court") or {}).get("name") or ""
        az = j.get("file_number") or ""
        if not court or court.startswith("Bundes") or az in seen_az or not j.get("date"):
            continue
        if not re.match(r"(Oberlandes|Land|Amts|Kammer|Pfälzisches Oberlandes|Hanseatisches Oberlandes|Schleswig-Holsteinisches Oberlandes|Brandenburgisches Oberlandes|Saarländisches Oberlandes|Thüringer Oberlandes)", court):
            continue
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", j.get("content") or "", flags=re.S | re.I)
        body = "\n".join(l for l in (" ".join(x.split()) for x in html.unescape(re.sub(r"<[^>]+>", "\n", raw)).split("\n")) if l and not re.fullmatch(r"\d{1,4}", l))
        if len(body) < 1500 or not relevant(body):
            continue
        y, mth, d = j["date"].split("-")
        yield {"doc_id": f"olg_haftung_{y}{mth}{d}_{re.sub(r'[^A-Za-z]+', '', kurz(court))[:14]}_{re.sub(r'[^\w]+', '_', az).strip('_')}", "source": "olg_haftung", "tier": 1,
               "title": karte(kurz(court), az, j["date"], body), "text": body, "origin": j.get("source_url") or f"https://de.openlegaldata.io/case/{j.get('slug')}",
               "law_state_year": int(y), "az": az, "court": court}


def bgh_alt():
    for f in sorted(glob.glob(str(H / "bgh_alt" / "*.pdf"))):
        t = subprocess.run(["pdftotext", "-layout", f, "-"], capture_output=True, text=True).stdout
        t = re.sub(r"\n\s*-\s*\d+\s*-\s*\n", "\n", t)                                   # номера страниц «- 5 -»
        flat = "\n".join(" ".join(l.split()) for l in t.split("\n"))
        flat = re.sub(r"(\w)-\n(\w)", r"\1\2", flat)                                    # переносы слов
        flat = re.sub(r"\n{3,}", "\n\n", flat)
        az = re.search(r"IX Z[RB] \d+/\d+", flat).group(0)
        md = re.search(r"(?:Verkündet am:|vom)\s*(\d{1,2})\. (\w+) (\d{4})", flat)
        mon = {"Januar": 1, "Februar": 2, "März": 3, "April": 4, "Mai": 5, "Juni": 6, "Juli": 7, "August": 8, "September": 9, "Oktober": 10, "November": 11, "Dezember": 12}
        iso = f"{md.group(3)}-{mon[md.group(2)]:02d}-{int(md.group(1)):02d}"
        ml = re.search(r"(?:BGHZ|BGHR):[^\n]*\n(?:\s*(?:BGHZ|BGHR|Nachschlagewerk):[^\n]*\n)*(.*?)\nBGH, (?:Urteil|Beschluß|Beschluss|Versäumnisurteil)", flat, re.S)
        leit = ml.group(1) if ml else ""
        yield {"doc_id": f"bgh_haftung_{iso.replace('-', '')}_{az.replace(' ', '_').replace('/', '_')}", "source": "bgh_haftung", "tier": 1,
               "title": karte("BGH", az, iso, flat, leit), "text": flat, "origin": "https://www.bundesgerichtshof.de", "law_state_year": int(iso[:4]), "az": az, "court": "Bundesgerichtshof"}


def main():
    a = list(nrwe())
    b = list(old({x["az"] for x in a}))
    seen, olg = set(), []
    for x in a + b:
        if x["doc_id"] not in seen:
            seen.add(x["doc_id"])
            olg.append(x)
    c = list(bgh_alt())
    for name, rows in (("olg_haftung", olg), ("bgh_haftung_alt", c)):
        with open(PREP / f"{name}.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        z = sum(len(r["text"]) for r in rows)
        print(f"{name}: {len(rows)} решений, {z:,} знаков, примерно {z // 1300:,} фрагментов")
    import collections
    print("NRWE:", len(a), "| другие земли (Open Legal Data):", len(b))
    print(collections.Counter(kurz(r["court"]).split()[0] for r in olg).most_common(6))
    print(sorted(collections.Counter(str(r["law_state_year"])[:3] + "0" for r in olg).items()))
    for r in c:
        print("  ", r["title"][:170])


if __name__ == "__main__":
    main()
