# LeckerMath-Korpus: Skripte zu Aufbau und Prüfung

Skripte, mit denen der offene Teil des Korpus (Datensatz `german-tax-law-corpus`) aus amtlichen Quellen aufgebaut, geteilt und geprüft wurde. Kommentare im Code sind auf Russisch.

*Scripts used to assemble, split and verify the open part of the corpus from official sources. Code comments are in Russian.*

| Schritt | Skripte |
|---|---|
| Sammeln | `collect_gii_aktuell.py` (Gesetze, amtliches XML), `collect_wayback_gii.py` (frühere Fassungen), `collect_rii.py` (Bundesgerichte), `collect_bund_neu.py` (BGBl, Verwaltungsvorschriften), `collect_nrwe_haftung.py` (Landesrechtsprechung) |
| Aufbereiten | `prepare_rii_bverwg_bag.py` (BVerwG/BAG, ab v0.3.0), `prepare_laws_neu.py`, `prepare_historie.py`, `prepare_historie2.py`, `prepare_rii_dump.py`, `prepare_bund_neu.py`, `prepare_handbuecher.py`, `prepare_ustae.py`, `prepare_haftung.py` |
| Teilen nach Gliederung (ab v0.3.0) | `struktur_laws.py`, `struktur_innen_laws.py`, `struktur_historie.py` (Gesetze: § › Abs. › Nr.), `struktur_gerichte.py` (Entscheidungen: Rn.), `struktur_verwaltung.py`, `struktur_verwaltung_hb.py` (Verwaltungsanweisungen, BMF-Handbücher: R/H, UStAE, AEAO), `struktur_baum.py` (Gliederungsbaum), `struktur_text.py`, `token_grenze.py`, `load_struktur_fragmente.py`, `load_struktur_laws.py` |
| Abgleich mit dem Ausgangstext | `quelle_lauf.py`, `quelle_basis.py`, `kopf_eigen_probe.py` (jeder Abschnitt: Stelle im Ausgangstext, Abweichung, Status) |
| Teilen in Abschnitte (bis v0.2) | `rebuild_structured_gpu.py`, `chunk_bmf_structured.py` |
| Laden und Nachbessern | `load_laws_neu.py`, `load_laws_kleb.py` |
| Prüfen | `audit_chunking.py`, `spacy_boundary_validator.py` |
| Ausfuhr | `make_korpus_export.py` |

## Versionen / Versions

| Datum | Skripte | Datensatz (Hugging Face) | Änderung |
|---|---|---|---|
| 20.10.2026 | v0.2.0 | 0.2 (25.10.2026) | Allgemeiner Lader, Qualitätsprüfung, EU-Recht, Grundsteuer der Länder |
| 02.11.2026 | v0.3.0 | 1.0.1 | Teilung entlang der Gliederung jedes Dokuments, Abgleich jedes Abschnitts mit dem Ausgangstext, Ausfuhr `data/fragments` mit Pfad |
| 13.11.2026 | **v0.4.0** | 1.0.1 (nächste Version nach Stand V3 in Vorbereitung) | Abgleich mit Tabellenschicht, Gliederungsbaum, ungekürzte Überschriften, Ausfuhr ohne nicht öffentliche Dokumente, Datenbankzugang ohne Passwort im Code |

Beschreibung der Datenbank: [leckermath.de/steuerbasis.html](https://leckermath.de/steuerbasis.html) · Datensatz: [huggingface.co/datasets/sevriukov/german-tax-law-corpus](https://huggingface.co/datasets/sevriukov/german-tax-law-corpus)

### Neu in v0.2.0 (Stand 20.10.2026)

| Schritt | Skripte | Wozu |
|---|---|---|
| Allgemeiner Lader | `load_new_sources.py`, `emb_sicher.py`, `storozh_laden.py` | Laden beliebiger aufbereiteter Quellen; Vektoren auf zwei GPUs mit Wiederholung bei Speichermangel; Wächter, der hängende Ladeketten neu startet |
| Qualitätsprüfung | `rag_qualitaetstest.py` | Prüfung der ganzen Datenbank (nur Lesen): Integrität, Titel, Abschnitte, Textqualität, Indizes, Selbstsuche |
| Fehlerhafte PDF-Schriften | `kaputt_neu.py` | PDFs, deren Textschicht um 29 Zeichen verschoben ist (Schrift ohne Zeichentabelle), seitenweise neu erkennen (tesseract) |
| Adressen der Gesetze | `url_gesetze.py` | Fehlende Adressen aus dem amtlichen Verzeichnis gesetze-im-internet.de ergänzen |
| Abkommen nach Artikeln | `dba_artikel_prep.py` | Doppelbesteuerungsabkommen in einzelne Artikel teilen (Auffindbarkeit nach Artikeln) |
| Recht der EU | `bank_recht_eu.py`, `eu_daten_ki.py` | Geltende konsolidierte Fassung über CELLAR; Erwägungsgründe gesondert (DSGVO, KI-Verordnung, Produkthaftungsrichtlinie) |
| Grundsteuer der Länder | `landes_grundsteuer_prep.py`, `landes_material_prep.py` | Landesgrundsteuergesetze nach Paragrafen; Begründungen der Landtage und Anwendungserlasse |
| Titel und Abschnitte | `bafin_rs_titel.py`, `bafin_titel_fix.py`, `vektor_umbau.py` | Titel aus dem Text statt aus Cookie-Hinweisen; gleichlautende Gesetzestitel nach Artikel/Datum aus dem amtlichen XML unterscheiden; Sperrschrift zusammenziehen; Abschnitte von Gesetzen mit „Gesetz § N:“ einleiten |
| Abdeckung je Paragraf | `paragraf_abdeckung.py` | Je Paragraf der Steuergesetze: frühere Fassungen, Urteile, Verwaltungsanweisungen, Begründungen, Literatur |
| Offene Literatur, Urteile | `oapen_recht.py`, `oapen_literatur_prep.py`, `oldp_fg_api_laden.py` | Offene Bücher (OAPEN, DOAB; CC-Lizenzen, ohne TDM-Vorbehalt); Urteile der Finanzgerichte über die API von Open Legal Data |

Änderungen: `CHANGELOG.md`.

Noch nicht enthalten: die Sammler für die Gerichtssammlungen (CE-BFH, CE-BVerfG, Open Legal Data) und die Skripte zu den amtlichen Handbüchern des BMF (dort wurden Seiten mit menschlicher Mitwirkung abgerufen).

Zugang zur Datenbank über die Umgebungsvariablen `PGHOST`, `PGUSER`, `PGPASSWORD` (für die Ausfuhr: `LM_DSN`).

Voraussetzungen: PostgreSQL mit pgvector, Python 3.11+, `sentence-transformers` (Modell `BAAI/bge-m3`), spaCy (`de_core_news_sm`).

Kein Rechts- oder Steuerrat. Keine externe Bewertung, keine Prüfung durch eine berufsrechtlich befugte Person.

## Lizenz

Code (Skripte in `src/`): Apache-2.0 (`LICENSE`, `NOTICE`; © 2026 Sergei Sevriukov). Der Datensatz auf Hugging Face steht unter CC BY 4.0; daran ändert sich nichts.
