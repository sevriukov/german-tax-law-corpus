#!/usr/bin/env python3
"""bank_recht_eu.py (18.10.2026, владелец проекта: «банковское законодательство… платёжный оборот — кровообращение хозяйства, скупиться нельзя»).
Акты ЕС и ЕЦБ о платёжном обороте, банках, рынке капитала, кредите, отмывании денег и финансовых санкциях -- на немецком,
в ДЕЙСТВУЮЩЕЙ консолидированной редакции (последняя consolidated version с датой <= сегодня; поиск через SPARQL CELLAR),
если консолидации нет -- в редакции опубликования. Источник -- Publications Office (CELLAR), повторное использование по Beschluss 2011/833/EU.
Уровень 1, source eu_recht. -> de_law_corpus/prepared/eu_bank.jsonl. Пауза 5 с между запросами.
    python3 -u bank_recht_eu.py
"""
import datetime
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "LeckerMath-Forschung"}
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
ACTS = {
    # платёжный оборот
    "32012R0260": "VO (EU) Nr. 260/2012 SEPA-Verordnung (Überweisungen und Lastschriften in Euro, einschl. Echtzeitüberweisungen)",
    "32024R0886": "VO (EU) 2024/886 Echtzeitüberweisungen in Euro (Änderungsverordnung)",
    "32021R1230": "VO (EU) 2021/1230 grenzüberschreitende Zahlungen in der Union",
    "32015R0751": "VO (EU) 2015/751 Interbankenentgelte für kartengebundene Zahlungsvorgänge",
    "32015L2366": "Richtlinie (EU) 2015/2366 Zahlungsdienste im Binnenmarkt (PSD2)",
    "32018R0389": "Delegierte VO (EU) 2018/389 RTS starke Kundenauthentifizierung und Kommunikationsstandards (PSD2)",
    "32009L0110": "Richtlinie 2009/110/EG E-Geld-Institute (E-Geld-Richtlinie)",
    "32014L0092": "Richtlinie 2014/92/EU Zahlungskonten (Vergleichbarkeit, Kontowechsel, Basiskonto)",
    "31998L0026": "Richtlinie 98/26/EG Wirksamkeit von Abrechnungen in Zahlungs- und Wertpapierliefer- und -abrechnungssystemen (Finalität)",
    "32002L0047": "Richtlinie 2002/47/EG Finanzsicherheiten",
    "32023R1113": "VO (EU) 2023/1113 Übermittlung von Angaben bei Geldtransfers und Transfers bestimmter Kryptowerte (Geldtransfer-VO)",
    # банки: надзор, капитал, санация, гарантирование вкладов
    "32013R0575": "VO (EU) Nr. 575/2013 Aufsichtsanforderungen an Kreditinstitute (CRR)",
    "32013L0036": "Richtlinie 2013/36/EU Zugang zur Tätigkeit von Kreditinstituten und Beaufsichtigung (CRD)",
    "32014L0059": "Richtlinie 2014/59/EU Sanierung und Abwicklung von Kreditinstituten (BRRD)",
    "32014L0049": "Richtlinie 2014/49/EU Einlagensicherungssysteme (DGSD)",
    "32014R0806": "VO (EU) Nr. 806/2014 Einheitlicher Abwicklungsmechanismus (SRM)",
    "32013R1024": "VO (EU) Nr. 1024/2013 Einheitlicher Aufsichtsmechanismus (SSM)",
    "32014R0468": "VO (EU) Nr. 468/2014 der EZB SSM-Rahmenverordnung (EZB/2014/17)",
    "32019R2033": "VO (EU) 2019/2033 Aufsichtsanforderungen an Wertpapierfirmen (IFR)",
    "32019L2034": "Richtlinie (EU) 2019/2034 Beaufsichtigung von Wertpapierfirmen (IFD)",
    "32010R1093": "VO (EU) Nr. 1093/2010 Europäische Bankenaufsichtsbehörde (EBA)",
    # рынок капитала
    "32014R0596": "VO (EU) Nr. 596/2014 Marktmissbrauchsverordnung (MAR)",
    "32017R1129": "VO (EU) 2017/1129 Prospektverordnung",
    "32014R1286": "VO (EU) Nr. 1286/2014 Basisinformationsblätter PRIIP",
    "32012R0648": "VO (EU) Nr. 648/2012 OTC-Derivate, zentrale Gegenparteien, Transaktionsregister (EMIR)",
    "32014R0909": "VO (EU) Nr. 909/2014 Zentralverwahrer (CSDR)",
    "32014L0065": "Richtlinie 2014/65/EU Märkte für Finanzinstrumente (MiFID II)",
    "32014R0600": "VO (EU) Nr. 600/2014 Märkte für Finanzinstrumente (MiFIR)",
    "32023R1114": "VO (EU) 2023/1114 Märkte für Kryptowerte (MiCAR)",
    "32011L0061": "Richtlinie 2011/61/EU Verwalter alternativer Investmentfonds (AIFMD)",
    "32009L0065": "Richtlinie 2009/65/EG Organismen für gemeinsame Anlagen in Wertpapieren (OGAW)",
    # кредит
    "32023L2225": "Richtlinie (EU) 2023/2225 Verbraucherkreditverträge",
    "32008L0048": "Richtlinie 2008/48/EG Verbraucherkreditverträge (bis 19.11.2026 anwendbar)",
    "32014L0017": "Richtlinie 2014/17/EU Wohnimmobilienkreditverträge für Verbraucher",
    # отмывание денег
    "32015L0849": "Richtlinie (EU) 2015/849 Verhinderung der Nutzung des Finanzsystems zur Geldwäsche (4. Geldwäscherichtlinie)",
    # финансовые санкции
    "32014R0269": "VO (EU) Nr. 269/2014 restriktive Maßnahmen: Handlungen gegen die territoriale Unversehrtheit der Ukraine (Einfrieren, Anhang I)",
    "32014R0833": "VO (EU) Nr. 833/2014 restriktive Maßnahmen angesichts der Handlungen Russlands (Russland-Embargo)",
    "32012R0267": "VO (EU) Nr. 267/2012 restriktive Maßnahmen gegen Iran",
    "32017R1509": "VO (EU) 2017/1509 restriktive Maßnahmen gegen die Demokratische Volksrepublik Korea",
    "32002R0881": "VO (EG) Nr. 881/2002 restriktive Maßnahmen gegen ISIL (Da'esh) und Al-Qaida",
    "32001R2580": "VO (EG) Nr. 2580/2001 restriktive Maßnahmen gegen Personen und Organisationen zur Bekämpfung des Terrorismus",
    # ЕЦБ: денежная политика, расчёты, статистика
    "31998R2531": "VO (EG) Nr. 2531/98 des Rates Auferlegung einer Mindestreservepflicht durch die EZB",
    "32021R0378": "VO (EU) 2021/378 der EZB Mindestreserven (EZB/2021/1)",
    "32015O0510": "Leitlinie (EU) 2015/510 der EZB Durchführung des geldpolitischen Handlungsrahmens (EZB/2014/60)",
    "32022O0912": "Leitlinie (EU) 2022/912 der EZB TARGET (EZB/2022/8)",
    "32021R0002": "VO (EU) 2021/2 der EZB Bilanz des Sektors der monetären Finanzinstitute (EZB/2021/2)",
    "32020R2011": "VO (EU) 2020/2011 der EZB Zahlungsverkehrsstatistik (EZB/2020/59)",
    "32016R0867": "VO (EU) 2016/867 der EZB Erhebung granularer Kredit- und Kreditrisikodaten (AnaCredit, EZB/2016/13)",
}


def sparql_latest(celex):
    """последняя консолидированная редакция с датой <= сегодня, иначе None"""
    base = "0" + celex[1:]
    q = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> SELECT DISTINCT ?c WHERE { ?w cdm:resource_legal_id_celex ?c . "
         f'FILTER(STRSTARTS(STR(?c), "{base}-")) }}')
    u = SPARQL + "?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(urllib.request.Request(u, headers={**UA, "Accept": "application/sparql-results+json"}), timeout=120) as r:
        vs = [b["c"]["value"] for b in json.load(r)["results"]["bindings"]]
    heute = datetime.date.today().strftime("%Y%m%d")
    return sorted((v for v in vs if re.fullmatch(base + r"-\d{8}", v) and v[-8:] <= heute), reverse=True)   # новейшая первой


def get(celex):
    req = urllib.request.Request(f"https://publications.europa.eu/resource/celex/{celex}",
                                 headers={**UA, "Accept": "application/xhtml+xml", "Accept-Language": "deu"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8", "replace")


def text(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)</(p|div|tr|h\d|li|table)>|<br\s*/?>", "\n", h)
    t = re.sub(r"[ \t\xa0]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def main():
    n = 0
    nur = [x for x in os.environ.get("NUR", "").split(",") if x]   # повтор только для неудавшихся, дописывая файл
    with open("de_law_corpus/prepared/eu_bank.jsonl", "a" if nur else "w", encoding="utf-8") as out:
        for celex, titel in ACTS.items():
            if nur and celex not in nur:
                continue
            t, c, kons = None, None, None
            try:
                kand = sparql_latest(celex)[:4] + [celex]   # 18.10: у новейшей консолидации немецкой версии может ещё не быть (404) -> предыдущая
            except Exception as e:                   # noqa: BLE001
                kand = [celex]
            time.sleep(2)
            for c in kand:
                try:
                    t = text(get(c))
                    kons = c if c != celex else None
                    break
                except Exception as e:               # noqa: BLE001
                    print("нет", c, str(e)[:60], flush=True)
                    time.sleep(5)
            if not t:
                continue
            if not re.search(r"Artikel|Abschnitt", t[:200000]) or not re.search(r"\b(der|die|und)\b", t[:5000]):
                print("не немецкий текст?", c, flush=True)
                continue
            datum = f"{c[-8:-4]}-{c[-4:-2]}-{c[-2:]}" if kons else None
            out.write(json.dumps({"doc_id": f"eu_bank_{c}", "source": "eu_recht", "tier": 1,
                                  "title": titel + (f", konsolidierte Fassung vom {datum}" if datum else ""),
                                  "text": t, "origin": f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{c}",
                                  "valid_from": datum, "effective_date": datum, "law_state_year": int((datum or celex[1:5])[:4]),
                                  "celex": c}, ensure_ascii=False) + "\n")
            out.flush()
            n += 1
            print(f"{c}: {titel[:70]} -- {len(t)} знаков", flush=True)
            time.sleep(5)
    print(f"EU_BANK_DONE: актов {n} из {len(ACTS)}", flush=True)


if __name__ == "__main__":
    main()
