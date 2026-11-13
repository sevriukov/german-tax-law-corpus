#!/usr/bin/env python3
"""landes_grundsteuer_prep.py (18.10.2026): земельные законы о поземельном налоге из официальных источников -> по параграфам,
в формате уже загруженного BayGrStG («<Kürzel> § N Überschrift (Name, Land)»), source landesrecht, уровень 1.
  NGrStG    -- Nds. GVBl. 2021 Nr. 27 S. 502 (niedersachsen.de, PDF выпуска), редакция опубликования
  LGrStG BW -- Gesetzesbeschluss Landtag BW Drs. 16/9178 (= текст GBl. 2020 S. 974), редакция принятия
  HmbGrStG  -- HmbGVBl. 2021 Nr. 56 S. 600 (luewu.de, PDF выпуска), редакция опубликования
  SächsGrStMG -- REVOSax (действующая редакция), VwV Grundsteuerabzugsbetrag 2024 -- REVOSax
Поздние изменения законов NI/BW/HH в эти тексты не входят -- помечено в заголовке и metadata («Fassung der Verkündung»).
    python3 landes_grundsteuer_prep.py   -> de_law_corpus/prepared/landes_grundsteuer.jsonl
"""
import html
import json
import re
import subprocess

D = "quellen_roh/landes_grundsteuer/2026-10-20"
GESETZE = [  # файл, сокращение, название, земля, начало тела, конец тела, источник, состояние
    ("nds_gvbl_2021_27.pdf", "NGrStG", "Niedersächsisches Grundsteuergesetz", "Niedersachsen", r"Niedersächsisches Grundsteuergesetz \(NGrStG\)\s*\n\s*Vom",
     r"Hannover, den", "https://www.niedersachsen.de/download/171798/Nds._GVBl._Nr._27_2021_vom_13.07.2021_S._469-508.pdf", "Nds. GVBl. 2021 S. 502"),
    ("bw_drs_16_9178.pdf", "LGrStG", "Landesgrundsteuergesetz Baden-Württemberg", "Baden-Württemberg", r"Erster Teil\s*\nAllgemeine Vorschriften",
     r"\nAnlage\s*1\b|\nAnlage\s*\n|Stuttgart, den", "https://www.landtag-bw.de/resource/blob/250730/acc661cc99b62f354ab0f7dd56900a76/16_9178_D.pdf", "Landtag BW Drs. 16/9178 (GBl. 2020 S. 974)"),
    ("hmb_gvbl_2021_56.pdf", "HmbGrStG", "Hamburgisches Grundsteuergesetz", "Hamburg", r"Hamburgisches Grundsteuergesetz\s*\(HmbGrStG\)\s*\n\s*Vom",
     r"Ausgefertigt Hamburg, den|Hamburg, den", "https://www.luewu.de/wp-content/uploads/2025/08/GVBL_HH_2021-56.pdf", "HmbGVBl. 2021 S. 600"),
]


def pdf(f):
    return subprocess.run(["pdftotext", f"{D}/{f}", "-"], capture_output=True, text=True).stdout


def paragraphen(body):
    """тело закона -> [(nr, überschrift, text)] по строкам «§ N» + строка заголовка"""
    body = re.sub(r"(\w)-\n(\w)", r"\1\2", body.replace("\xad", ""))
    teile = re.split(r"\n\s*§\s*(\d+[a-z]?)\s*\n", "\n" + body)
    out = []
    for i in range(1, len(teile) - 1, 2):
        rest = teile[i + 1].strip()
        kopf, _, txt = rest.partition("\n")
        if len(kopf) > 120 or kopf.startswith("("):     # заголовка нет -- текст сразу
            kopf, txt = "", rest
        out.append((teile[i], kopf.strip(), txt.strip()))
    return out


def main():
    n = 0
    with open("de_law_corpus/prepared/landes_grundsteuer.jsonl", "w", encoding="utf-8") as f:
        for datei, kurz, name, land, start, ende, url, fund in GESETZE:
            t = pdf(datei)
            m = list(re.finditer(start, t))
            if not m:
                print("[!] начало не найдено:", kurz)
                continue
            body = t[m[-1].start():]
            e = re.search(ende, body[200:])
            body = body[: 200 + e.start()] if e else body
            ps = paragraphen(body)
            for nr, kopf, txt in ps:
                if len(txt) < 30:
                    continue
                f.write(json.dumps({"doc_id": f"landesrecht_{kurz}_par{nr}", "source": "landesrecht", "tier": 1,
                                    "title": f"{kurz} § {nr} {kopf} ({name}, {land}; Fassung der Verkündung, {fund})".replace("  ", " "),
                                    "text": f"{kurz} § {nr} {kopf}\n\n{txt}", "origin": url, "valid_from": None, "effective_date": None,
                                    "law_state_year": 2021, "fassung": "Verkündung", "fundstelle": fund}, ensure_ascii=False) + "\n")
                n += 1
            print(f"{kurz}: параграфов {len(ps)}; первые: {[p[0] + ' ' + p[1][:30] for p in ps[:3]]}", flush=True)
        for v, kurz, name in (("19470-Saechsisches-Grundsteuermesszahlengesetz-", "SächsGrStMG", "Sächsisches Grundsteuermesszahlengesetz"),
                              ("21296-VwV-Grundsteuerabzugsbetrag-2024", "VwV Grundsteuerabzugsbetrag 2024", "Verwaltungsvorschrift Grundsteuerabzugsbetrag 2024")):
            s = open(f"{D}/sn_{v}.html", encoding="utf-8").read()
            m = re.search(r"(?is)Vollzitat:(.*?)(?:Änderungsvorschriften|Fußnoten|<footer)", s)
            s = m.group(0) if m else s
            s = re.sub(r"(?i)</(p|div|h\d|li|dd|dt)>|<br\s*/?>", "\n", re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s))
            txt = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))).strip()
            f.write(json.dumps({"doc_id": f"landesrecht_sn_{re.sub(r'[^A-Za-z0-9]+', '_', kurz)}", "source": "landesrecht", "tier": 1,
                                "title": f"{kurz} ({name}, Sachsen; aktuelle Fassung REVOSax)", "text": txt,
                                "origin": f"https://www.revosax.sachsen.de/vorschrift/{v}", "valid_from": None, "effective_date": None,
                                "law_state_year": 2026}, ensure_ascii=False) + "\n")
            n += 1
            print(f"{kurz}: {len(txt)} знаков", flush=True)
    print(f"LANDES_GRUNDSTEUER_PREP: документов {n}", flush=True)


if __name__ == "__main__":
    main()
