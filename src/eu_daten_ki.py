#!/usr/bin/env python3
"""eu_daten_ki.py (19.10.2026): акты ЕС, нужные самому конвейеру и названные в критике (DeepSeek/Gemini 18.10), которых нет в базе:
DSGVO, KI-Verordnung (AI Act), новая Produkthaftungsrichtlinie. Сборщик тот же, что для банковских актов (bank_recht_eu.py):
действующая консолидированная редакция через SPARQL CELLAR, иначе редакция опубликования; Publications Office, Beschluss 2011/833/EU.
Консолидированный текст Erwägungsgründe не содержит -- они берутся из редакции опубликования отдельным документом «… — Erwägungsgründe»
(цель и предмет нормы читаются по ним). Уровень 1, source eu_recht. -> de_law_corpus/prepared/eu_daten_ki.jsonl. BDSG, TTDSG/TDDDG, DDG, ProdHaftG, KI-MIG -- в базе уже есть.
    python3 -u eu_daten_ki.py
"""
import json
import re
import time

import bank_recht_eu as b

ACTS = {
    "32016R0679": "VO (EU) 2016/679 Datenschutz-Grundverordnung (DSGVO)",
    "32024R1689": "VO (EU) 2024/1689 Künstliche Intelligenz (KI-Verordnung, AI Act)",
    "32024L2853": "Richtlinie (EU) 2024/2853 Haftung für fehlerhafte Produkte (neue Produkthaftungsrichtlinie)",
}


def main():
    n = 0
    with open("de_law_corpus/prepared/eu_daten_ki.jsonl", "w", encoding="utf-8") as out:
        for celex, titel in ACTS.items():
            t, c, kons = None, None, None
            try:
                kand = b.sparql_latest(celex)[:4] + [celex]
            except Exception:                        # noqa: BLE001
                kand = [celex]
            time.sleep(2)
            for c in kand:
                try:
                    t = b.text(b.get(c))
                    kons = c if c != celex else None
                    break
                except Exception as e:               # noqa: BLE001
                    print("нет", c, str(e)[:60], flush=True)
                    time.sleep(5)
            if not t or not re.search(r"Artikel", t[:200000]) or not re.search(r"\b(der|die|und)\b", t[:5000]):
                print("не получен / не немецкий текст", celex, flush=True)
                continue
            datum = f"{c[-8:-4]}-{c[-4:-2]}-{c[-2:]}" if kons else None
            out.write(json.dumps({"doc_id": f"eu_recht_{c}", "source": "eu_recht", "tier": 1,
                                  "title": titel + (f", konsolidierte Fassung vom {datum}" if datum else ""),
                                  "text": t, "origin": f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{c}",
                                  "valid_from": datum, "effective_date": datum, "law_state_year": int((datum or celex[1:5])[:4]),
                                  "celex": c}, ensure_ascii=False) + "\n")
            out.flush()
            n += 1
            print(f"{c}: {titel[:70]} -- {len(t)} знаков", flush=True)
            try:                                     # Erwägungsgründe -- из редакции опубликования
                o = b.text(b.get(celex))
                m = re.search(r"(?s)in Erwägung nachstehender Gründe:(.*?)(HABEN FOLGENDE (?:VERORDNUNG|RICHTLINIE) ERLASSEN)", o)
                if m and len(m.group(1)) > 2000:
                    out.write(json.dumps({"doc_id": f"eu_recht_{celex}_erwg", "source": "eu_recht", "tier": 1,
                                          "title": titel + " — Erwägungsgründe (Fassung bei Veröffentlichung)",
                                          "text": "in Erwägung nachstehender Gründe:" + m.group(1),
                                          "origin": f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{celex}",
                                          "law_state_year": int(celex[1:5]), "celex": celex}, ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"{celex}: Erwägungsgründe -- {len(m.group(1))} знаков", flush=True)
                else:
                    print("Erwägungsgründe не выделены", celex, flush=True)
            except Exception as e:                   # noqa: BLE001
                print("нет редакции опубликования", celex, str(e)[:60], flush=True)
            time.sleep(5)
    print(f"EU_DATEN_KI_DONE: актов {n} из {len(ACTS)}", flush=True)


if __name__ == "__main__":
    main()
