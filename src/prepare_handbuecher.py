#!/usr/bin/env python3
"""
prepare_handbuecher.py -- страницы справочников BMF (EStH, LStH, ... из collect_bmf_handbuecher.py)
-> de_law_corpus/prepared/bmf_handbuecher.jsonl (уровень 1). Одна страница = один документ
(R-раздел, H-указания, параграф закона, приложение). Стартовые страницы и дубли пропускаются («inhalt.html» -- это страница параграфа, а не оглавление).
Загрузка: python3 load_new_sources.py load --sources bmf_handbuecher
"""
import glob
import hashlib
import json
import re
from pathlib import Path

SRC = Path("de_law_corpus/bmf_handbuecher")
OUT = Path("de_law_corpus/prepared/bmf_handbuecher.jsonl")
NAMES = {"esth": "EStH", "lsth": "LStH", "ksth": "KStH", "gewsth": "GewStH", "erbsth": "ErbStH", "ao": "AO-Handbuch", "usth": "UStH"}


def main():
    n, skipped, seen = 0, 0, set()
    with open(OUT, "w", encoding="utf-8") as out:
        for d in sorted(SRC.glob("*/")):
            hb = NAMES.get(d.name)
            if not hb:
                continue
            for f in sorted(glob.glob(str(d / f"{d.name}_*.json"))):
                p = json.load(open(f))
                url, text = p["url"], re.sub(r"[ \t\xa0]+", " ", p["text"].replace("\xad", "")).strip()
                leaf = url.rstrip("/").split("/")[-1]
                h = hashlib.sha1(text.encode()).hexdigest()
                if leaf in ("home.html", "index.html", "") or "/" not in url.split("//", 1)[1].rstrip("/") or ".footnote" in url or len(text) < 300 or h in seen:
                    skipped += 1
                    continue
                seen.add(h)
                year = (re.search(r"/(\d{4})/", url) or [None, ""])[1]
                lines = [" ".join(x.split()) for x in text.split("\n") if x.strip() and not re.fullmatch(r"S ?\d[\d\s–a-z-]*", x.strip())]
                first = " ".join(lines[:2])[:160] if lines and len(lines[0]) < 25 else (lines[0][:160] if lines else "")
                kind = re.sub(r"\.html$", "", leaf)
                if kind == "inhalt":                    # страница параграфа: «…/Paragraf-26/inhalt.html»
                    kind = url.rstrip("/").split("/")[-2].replace("Paragraf-", "§ ")
                m = re.match(r"(r|h)-(\d+[a-z]?)(?:-(\d+[a-z]?))?", kind)
                if m:                                   # «r-6a-1» -> «R 6a.1», «h-13-3» -> «H 13.3»
                    kind = f"{m.group(1).upper()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
                title = f"{hb} {year}: {kind} — {first}"
                out.write(json.dumps({"doc_id": f"bmfhb_{d.name}_{year}_{Path(f).stem.split('_')[-1]}", "source": "bmf_handbuecher", "title": title,
                                      "text": text, "tier": 1, "origin": url, "law_state_year": int(year) if year else None}, ensure_ascii=False) + "\n")
                n += 1
    print(f"документов: {n}, пропущено (стартовые, дубли, короткие): {skipped} -> {OUT}")


if __name__ == "__main__":
    main()
