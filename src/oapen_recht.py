#!/usr/bin/env python3
"""oapen_recht.py -- открытые книги по налоговому, финансовому и общему праву из каталога OAPEN (18.10.2026, Сергей: «то же самое, но без запретов»).
Каталог -- через OAI-PMH (library.oapen.org/oai/request, разрешено; поисковый REST-API закрыт по IP). Отбор: язык немецкий, тема права
по названию/теме/ключевым словам, лицензия Creative Commons. Скачивание PDF -- только для отобранных, пауза 3 с.
Книги -> уровень 3 (база steuer_literatur), наружу не уходят (правило проекта), как и прочая литература.

    python3 oapen_recht.py katalog      # -> quellen_neu/literatur_open/oapen_kandidaten.json
    python3 oapen_recht.py laden        # скачать PDF кандидатов -> quellen_neu/literatur_open/oapen/
    python3 oapen_recht.py katalog_doab # 20.10.2026: то же по каталогу DOAB (directory.doabooks.org/oai/request; REST закрыт по IP, OAI открыт)
                                        # -> quellen_neu/literatur_open/doab_kandidaten.json
"""
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

OAI = "https://library.oapen.org/oai/request"
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/", "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/"}
D = "quellen_neu/literatur_open"
THEMEN = {
 "Steuerrecht": r"steuer|abgabe|fiskal|umsatzsteuer|einkommensteuer|grundsteuer|erbschaft|zoll",
 "Finanz- und Haushaltsrecht": r"finanzverfassung|haushalt|finanzausgleich|staatsverschuldung|schuldenbremse|öffentliche finanzen",
 "Bank- und Kapitalmarktrecht": r"bank|kapitalmarkt|finanzmarkt|wertpapier|krypto|zahlungsverkehr|finanzaufsicht|investment",
 "Geldwäsche und Wirtschaftsstrafrecht": r"geldwäsche|wirtschaftsstraf|korruption|compliance|steuerhinterziehung|betrug",
 "Gesellschafts-, Handels-, Insolvenzrecht": r"gesellschaftsrecht|handelsrecht|insolvenz|sanierung|unternehmensrecht|konzern|aktienrecht|gmbh|bilanz|rechnungslegung",
 "Sozialversicherungsrecht": r"sozialversicherung|sozialrecht|rentenversicherung|krankenversicherung|beitrag",
 "Europa- und Verfassungsrecht": r"europarecht|unionsrecht|grundgesetz|verfassungsrecht|staatsorganisation",
 "Zivilrecht": r"vertragsrecht|schuldrecht|bürgerliches recht|privatrecht|zivilrecht",
}
RECHT = r"recht|gesetz|jurist|rechtswissenschaft|law"


def get(url):
    for _ in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LeckerMath-Forschung"}), timeout=180) as r:
                return r.read()
        except Exception:
            time.sleep(30)
    return b""


def katalog(oai=OAI, name="oapen"):
    url, out, n = f"{oai}?verb=ListRecords&metadataPrefix=oai_dc", [], 0
    while url:
        x = get(url)
        if not x:
            break
        root = ET.fromstring(x)
        for rec in root.iter(f"{{{NS['oai']}}}record"):
            n += 1
            md = rec.find(".//oai_dc:dc", NS)
            if md is None:
                continue
            f = lambda tag: [e.text or "" for e in md.findall(f"dc:{tag}", NS)]
            lang = " ".join(f("language")).lower()
            if not re.search(r"\b(ger|deu|de|german|deutsch)\b", lang):
                continue
            title = " ".join(f("title"))
            text = " ".join([title] + f("subject") + f("description")[:1]).lower()
            if not re.search(RECHT, text):
                continue
            th = [k for k, rx in THEMEN.items() if re.search(rx, text)]
            if not th:
                continue
            rights = " ".join(f("rights"))
            if name == "oapen" and not re.search(r"creativecommons|CC[ -]BY|openAccess", rights, re.I):   # DOAB: поля rights в oai_dc нет, все книги -- открытый доступ; лицензия -- при скачивании   # OAPEN в oai_dc пишет «openAccess»; запрета TDM нет -> § 44b Abs. 2, 3 UrhG
                continue
            ids = f("identifier")
            out.append({"titel": title[:300], "jahr": next((d for d in f("date") if re.fullmatch(r"\d{4}", d)), ""), "themen": th, "rechte": rights[:120],
                        "links": [i for i in ids if i.startswith("http")][:4], "verlag": (f("publisher") or [""])[0][:80]})
        tok = root.find(".//oai:resumptionToken", NS)
        url = f"{oai}?verb=ListRecords&resumptionToken={tok.text}" if tok is not None and tok.text else None
        if n % 2000 < 100:
            print(f"просмотрено {n}, отобрано {len(out)}", flush=True)
        time.sleep(2)
    json.dump(out, open(f"{D}/{name}_kandidaten.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"KATALOG_DONE: просмотрено {n}, отобрано {len(out)}", flush=True)


def laden():
    """Раскладка (18.10.2026, Сергей): книги издательства Springer (запрет TDM на их сайте, § 44b Abs. 3 UrhG) -> NUR_LESEN/springer (только чтение,
    в базу не грузить); лицензия CC …-NC (только некоммерческое) -> NUR_LESEN/nc (решает Сергей); прочие открытые -> literatur_open/oapen (для базы).
    Лицензия -- со страницы книги в OAPEN (ссылка creativecommons.org/licenses/…)."""
    import os
    ks = json.load(open(f"{D}/oapen_kandidaten.json", encoding="utf-8"))
    ziele = {"basis": f"{D}/oapen", "springer": "NUR_LESEN/springer", "nc": "NUR_LESEN/nc"}
    for z in ziele.values():
        os.makedirs(z, exist_ok=True)
    stat, liste = {k: 0 for k in ziele}, []
    for c in ks:
        hdl = next((l for l in c["links"] if "handle" in l), None)
        if not hdl:
            continue
        page = get(hdl).decode("utf-8", "ignore")
        pdf = re.search(r'href="(/bitstream/handle/[^"]+\.pdf[^"]*)"', page)
        lic = re.search(r"creativecommons\.org/licenses/([a-z-]+)/([0-9.]+)", page)
        lizenz = f"CC {lic.group(1).upper()} {lic.group(2)}" if lic else "openAccess (Lizenz nicht angegeben)"
        if not pdf:
            continue
        ziel = "springer" if re.search(r"springer", c.get("verlag", "") + page[:20000], re.I) and re.search(r"springer", c.get("verlag", ""), re.I) else \
               ("nc" if lic and "nc" in lic.group(1) else "basis")
        name = re.sub(r"[^\w.-]+", "_", c["titel"])[:90] + ".pdf"
        dst = f"{ziele[ziel]}/{name}"
        if not os.path.exists(dst):
            data = get("https://library.oapen.org" + pdf.group(1).replace("&amp;", "&"))
            if data[:4] != b"%PDF":
                continue
            open(dst, "wb").write(data)
        stat[ziel] += 1
        liste.append({"ziel": ziel, "titel": c["titel"], "jahr": c["jahr"], "verlag": c.get("verlag"), "lizenz": lizenz, "themen": c["themen"], "datei": dst, "quelle": hdl})
        time.sleep(3)
    json.dump(liste, open(f"{D}/oapen_geladen.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"LADEN_DONE: {stat}", flush=True)

if __name__ == "__main__":
    {"katalog": katalog, "laden": laden, "katalog_doab": lambda: katalog("https://directory.doabooks.org/oai/request", "doab")}[sys.argv[1]]()
