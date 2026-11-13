"""Решения Finanzgerichte из API Open Legal Data, которых нет в базе (17.10.2026, пробел А4 плана PLAN_Sammeln_Stufe2).
В выгрузке OLD на Hugging Face 13 716 решений FG (все уже в базе), в API -- 23 288. robots.txt OLD: «do not crawl us to get all the data:
download everything via our data dumps or API» -- берём через API, пауза 1 с. Тексты решений -- § 5 UrhG (амтлихе Werke), база -- ODbL.
Шаг 1: список (100 на страницу) -> сверка с базой по ECLI или Az+дата. Шаг 2: текст каждого недостающего -> de_law_corpus/prepared/oldp_fg_api.jsonl
(формат prepare_oldp.py, source finanzgericht, уровень 1). Резюмируемо (уже записанные id пропускаются). В базу не пишет.
    python3 -u oldp_fg_api_laden.py"""
import html
import json
import os
import re
import subprocess
import time
import urllib.request

API = "https://de.openlegaldata.io/api/cases/"
OUT = "de_law_corpus/prepared/oldp_fg_api.jsonl"


def get(url):
    for v in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LeckerMath-Forschung (research)"}), timeout=90) as r:
                return json.loads(r.read())
        except Exception as e:                   # noqa: BLE001
            print("повтор", url, e, flush=True)
            time.sleep(60 if "429" in str(e) else 10 * (v + 1))     # 429: OLD просит реже
    return None


def text(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr|dd|dt)>", "\n", h)
    h = re.sub(r"(?i)<h(\d)[^>]*>", lambda m: "\n" + "#" * min(int(m[1]) + 1, 4) + " ", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", t).strip()


db = subprocess.run(["docker", "exec", "steuer_db", "psql", "-U", "postgres", "-d", "steuer_corpus_test", "-tAF", "\t", "-c",
                     "select coalesce(metadata->>'ecli',''), coalesce(reference_number,''), coalesce(effective_date::text,''), doc_id from documents where source in ('finanzgericht','bfh')"],
                    capture_output=True, text=True).stdout.split("\n")
ecli, az, ids = set(), set(), set()
for z in db:
    if z.count("\t") == 3:
        e, r, d, i = z.split("\t")
        ecli.add(e)
        az.add((r, d))
        ids.add(i)
fertig = {json.loads(z)["doc_id"] for z in open(OUT, encoding="utf-8")} if os.path.exists(OUT) else set()

# API отдаёт не больше 10 страниц (1 000 записей) на запрос -> список по полугодиям (17.10.2026)
fehlt, n, gesehen, abbruch = [], 0, set(), None
for jahr in range(int(os.environ.get("AB_JAHR", "1990")), 2027):     # 17.10: дневная норма OLD (429, Retry-After ≈ 23 ч) -- с 1990, раньше решений FG почти нет
    for von, bis in ((f"{jahr}-01-01", f"{jahr}-06-30"), (f"{jahr}-07-01", f"{jahr}-12-31")):
        url = f"{API}?format=json&court__jurisdiction=Finanzgerichtsbarkeit&date_after={von}&date_before={bis}&page_size=100"
        while url and not abbruch:
            d = get(url)
            if not d:                            # 20.10.2026: норма исчерпана -- не пропускать полугодие молча, а остановить список
                abbruch = jahr
                break
            if d["count"] > 1000 and "page=" not in url:
                print("больше 1000 за полугодие", von, d["count"], flush=True)
            for c in d["results"]:
                if c["id"] in gesehen:
                    continue
                gesehen.add(c["id"])
                n += 1
                if (c["ecli"] and (c["ecli"] in ecli or "finanzgericht_" + c["ecli"] in ids)) or (c["file_number"], c["date"]) in az or f"oldp_{c['id']}" in fertig:
                    continue
                fehlt.append(c)
            url = d["next"]
            time.sleep(6)                        # 18.10: 4 -> 6 с, дневная норма OLD
    print(f"список: {jahr}, просмотрено {n}, недостающих {len(fehlt)}", flush=True)
    if abbruch:
        print(f"СПИСОК ПРЕРВАН (норма OLD) на {abbruch}: тексты -- для собранного; продолжить AB_JAHR={abbruch}", flush=True)
        break
print(f"LISTE_DONE просмотрено {n}, недостающих {len(fehlt)}", flush=True)

ok = 0
with open(OUT, "a", encoding="utf-8") as out:
    for k, c in enumerate(fehlt, 1):
        d = get(f"{API}{c['id']}/?format=json")
        t = text(d.get("content")) if d else ""
        if len(t) >= 500:
            out.write(json.dumps({"doc_id": f"oldp_{c['id']}", "source": "finanzgericht", "tier": "1",
                                  "title": f"{c['court']['name']} {c['file_number']} ({c['date']}). {c.get('type') or ''}".strip(),
                                  "text": t, "origin": f"https://de.openlegaldata.io/case/{c['id']}", "reference_number": c["file_number"],
                                  "effective_date": c["date"], "law_state_year": c["date"][:4], "ecli": c["ecli"] or ""}, ensure_ascii=False) + "\n")
            out.flush()
            ok += 1
        if k % 200 == 0:
            print(f"тексты: {k} из {len(fehlt)}, записано {ok}", flush=True)
        time.sleep(6)                            # 18.10: 4 -> 6 с, дневная норма OLD
print(f"OLDP_FG_DONE записано {ok} из {len(fehlt)}", flush=True)
