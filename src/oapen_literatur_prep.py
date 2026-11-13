#!/usr/bin/env python3
"""oapen_literatur_prep.py (19.10.2026, владелец проекта «2а»): 12 открытых книг OAPEN из NUR_LESEN/1_lesen -> база литературы steuer_literatur
(уровень 3: служебное пользование, наружу не уходит, перед использованием проверяется по уровню 1). Лицензии проверены по титульным
страницам 19.10: CC BY 4.0 (Nomos: Abel, Glenk, Hoppe; Mohr: Möslein/Omlor), CC BY-SA 4.0 (Niemann, Plesdonat, Bueren),
CC BY-NC-ND 4.0 (Mohr: Renning, Wizemann, Badenhoop, Wengert, Omlor/Möslein/Grundmann) -- лицензия пишется в заголовок.
    python3 oapen_literatur_prep.py   -> de_law_corpus/prepared/literatur_oapen.jsonl
"""
import hashlib
import json
import re
import subprocess

D = "NUR_LESEN/1_lesen"
BUECHER = [  # файл, автор, название, год, лицензия
    ("Abel_2022_Steuerliche_Gewinnverteilung_Personengesellschaften.pdf", "Abel", "Steuerliche Gewinnverteilung bei Personengesellschaften", 2022, "CC BY 4.0, Nomos"),
    ("Glenk_2022_Verlustverrechnung_Personengesellschaften.pdf", "Glenk", "Verlustverrechnung bei Personengesellschaften", 2022, "CC BY 4.0, Nomos"),
    ("Plesdonat_2024_Korrespondenz_im_Steuerrecht.pdf", "Plesdonat", "Korrespondenz im Steuerrecht", 2024, "CC BY-SA 4.0, Mohr Siebeck"),
    ("Niemann_2020_Der_Syndikus-Steuerberater.pdf", "Niemann", "Der Syndikus-Steuerberater", 2020, "CC BY-SA 4.0, Universitätsverlag Göttingen"),
    ("Hoppe_2025_Sanierungsprivilegierung_und_Beihilfenrecht.pdf", "Hoppe", "Sanierungsprivilegierung und Beihilfenrecht", 2025, "CC BY 4.0, Nomos"),
    ("Renning_2024_Fintech_im_Bankaufsichtsrecht.pdf", "Renning", "Fintech im Bankaufsichtsrecht", 2024, "CC BY-NC-ND 4.0, Mohr Siebeck"),
    ("Wizemann_2024_Finanzmarktregulierung_und_Kartellrecht.pdf", "Wizemann", "Finanzmarktregulierung und Kartellrecht", 2024, "CC BY-NC-ND 4.0, Mohr Siebeck"),
    ("Badenhoop_2020_Europaeische_Bankenregulierung_private_Haftung.pdf", "Badenhoop", "Europäische Bankenregulierung und private Haftung", 2020, "CC BY-NC-ND 4.0, Mohr Siebeck"),
    ("Moeslein_Omlor_2024_Kryptoaktien.pdf", "Möslein/Omlor (Hrsg.)", "Kryptoaktien", 2024, "CC BY 4.0, Mohr Siebeck"),
    ("Wengert_2024_Schuldrecht_der_Kryptowaehrungen.pdf", "Wengert", "Schuldrecht der Kryptowährungen", 2024, "CC BY-NC-ND 4.0, Mohr Siebeck"),
    ("Omlor_Moeslein_Grundmann_2021_Elektronische_Wertpapiere.pdf", "Omlor/Möslein/Grundmann (Hrsg.)", "Elektronische Wertpapiere", 2021, "CC BY-NC-ND 4.0, Mohr Siebeck"),
    ("Bueren_2024_Short-termism_Aktien_Kapitalmarktrecht.pdf", "Bueren", "Short-termism im Aktien- und Kapitalmarktrecht", 2024, "CC BY-SA 4.0, Mohr Siebeck"),
]
n = 0
with open("de_law_corpus/prepared/literatur_oapen.jsonl", "w", encoding="utf-8") as out:
    for datei, autor, titel, jahr, lizenz in BUECHER:
        t = subprocess.run(["pdftotext", f"{D}/{datei}", "-"], capture_output=True, text=True, errors="ignore").stdout
        t = re.sub(r"(?m)^\s*https://doi\.org/\S+ https://www\.inlibra\.com/de/page/agb - Open Access -\s*$", "", t.replace("\xad", ""))
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        out.write(json.dumps({"doc_id": "literatur_oapen_" + hashlib.sha1(datei.encode()).hexdigest()[:12], "source": "literatur", "tier": 3,
                              "title": f"{autor}, {titel} ({jahr}; Open Access, {lizenz})", "text": t, "origin": "https://library.oapen.org",
                              "valid_from": None, "effective_date": None, "law_state_year": jahr}, ensure_ascii=False) + "\n")
        n += 1
        print(f"{autor}: {len(t)} знаков", flush=True)
print(f"OAPEN_LIT_PREP: книг {n}", flush=True)
