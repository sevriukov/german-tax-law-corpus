#!/usr/bin/env python3
"""landes_material_prep.py (20.10.2026, план развития базы, II.Б и II.Г): материалы к законам земель о поземельном налоге из официальных
источников -- обоснования законопроектов (Landtag) и разъяснения министерств финансов (Anwendungserlasse).
  landtag_drucksachen (уровень 1, группа «verwaltung», как bt_drucksachen): Bayern Drs. 18/15755 (Gesetzentwurf BayGrStG mit Begründung),
      18/16068 und 18/18651 (Änderungsanträge mit Begründung); Hessen Drs. 20/6379 (Gesetzentwurf HGrStG mit Begründung + Korrektur).
  laender_erlasse (уровень 1, группа «verwaltung»): AE HGrStG (HMdF, StAnz. Hessen 24/2022 S. 642); AENGrStG (MF Niedersachsen,
      Nds. MBl. 11/2022 S. 325, с Bek. LStN S. 342).
robots.txt сайтов (bayern.landtag.de, starweb.hessen.de, finanzamt.hessen.de, niedersachsen.de) эти пути не запрещают; § 5 UrhG.
    python3 landes_material_prep.py   -> de_law_corpus/prepared/landes_material.jsonl
"""
import json
import re
import subprocess

D = "quellen_roh/landes_grundsteuer/2026-10-20"
DOCS = [  # файл, doc_id, source, заголовок, адрес, год
    ("by_0000010126.pdf", "landtag_by_18_15755", "landtag_drucksachen", "Bayerischer Landtag Drs. 18/15755 (10.05.2021): Gesetzentwurf der Staatsregierung — Bayerisches Grundsteuergesetz (BayGrStG), mit Begründung",
     "https://www.bayern.landtag.de/www/ElanTextAblage_WP18/Drucksachen/Basisdrucksachen/0000010000/0000010126.pdf", 2021),
    ("by_0000010364.pdf", "landtag_by_18_16068", "landtag_drucksachen", "Bayerischer Landtag Drs. 18/16068 (02.06.2021): Änderungsantrag zum Gesetzentwurf BayGrStG (Drs. 18/15755), mit Begründung",
     "https://www.bayern.landtag.de/www/ElanTextAblage_WP18/Drucksachen/Basisdrucksachen/0000010000/0000010364.pdf", 2021),
    ("by_0000011655.pdf", "landtag_by_18_18651", "landtag_drucksachen", "Bayerischer Landtag Drs. 18/18651 (27.10.2021): Änderungsantrag zum BayGrStG, mit Begründung",
     "https://www.bayern.landtag.de/www/ElanTextAblage_WP18/Drucksachen/Basisdrucksachen/0000011500/0000011655.pdf", 2021),
    ("he_drs_20_6379.pdf", "landtag_he_20_6379", "landtag_drucksachen", "Hessischer Landtag Drs. 20/6379 (2021): Gesetzentwurf der Landesregierung — Hessisches Grundsteuergesetz (HGrStG), mit Begründung und Korrektur zu § 7",
     "https://starweb.hessen.de/cache/DRS/20/9/06379.pdf", 2021),
    ("he_ae_hgrstg_2022.pdf", "erlass_he_ae_hgrstg_2022", "laender_erlasse", "AE HGrStG — Anwendungserlass zum Hessischen Grundsteuergesetz (Hessisches Ministerium der Finanzen, StAnz. Hessen 24/2022 S. 642)",
     "https://finanzamt.hessen.de/sites/finanzamt.hessen.de/files/2022-06/220603_ae_hgrstg_verlag_final_stanz_2022_642_am_13_6_2022.pdf", 2022),
    ("nds_mbl_2022_11.pdf", "erlass_ni_aengrstg_2022", "laender_erlasse", "AENGrStG — Anwendung des Niedersächsischen Grundsteuergesetzes (RdErl. d. MF v. 22.2.2022, G 1002-6, Nds. MBl. 11/2022 S. 325) mit Bek. LStN v. 21.3.2022",
     "https://www.niedersachsen.de/download/182149/Nds._MBl._Nr._11_2022_vom_21.03.2022_S._325-391.pdf", 2022),
]


def main():
    with open("de_law_corpus/prepared/landes_material.jsonl", "w", encoding="utf-8") as out:
        for f, doc_id, src, titel, url, jahr in DOCS:
            t = subprocess.run(["pdftotext", "-raw", f"{D}/{f}", "-"], capture_output=True, text=True).stdout
            t = re.sub(r"(\w)-\n(\w)", r"\1\2", t.replace("\xad", ""))
            t = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", t)).strip()
            out.write(json.dumps({"doc_id": doc_id, "source": src, "tier": 1, "title": titel, "text": t, "origin": url,
                                  "law_state_year": jahr}, ensure_ascii=False) + "\n")
            print(f"{doc_id}: {len(t)} знаков", flush=True)


if __name__ == "__main__":
    main()
