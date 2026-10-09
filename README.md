# LeckerMath-Korpus: Skripte zu Aufbau und Prüfung

Skripte, mit denen der offene Teil des Korpus (Datensatz `german-tax-law-corpus`) aus amtlichen Quellen aufgebaut, geteilt und geprüft wurde. Kommentare im Code sind auf Russisch.

*Scripts used to assemble, split and verify the open part of the corpus from official sources. Code comments are in Russian.*

| Schritt | Skripte |
|---|---|
| Sammeln | `collect_gii_aktuell.py` (Gesetze, amtliches XML), `collect_wayback_gii.py` (frühere Fassungen), `collect_rii.py` (Bundesgerichte), `collect_bund_neu.py` (BGBl, Verwaltungsvorschriften), `collect_nrwe_haftung.py` (Landesrechtsprechung) |
| Aufbereiten | `prepare_laws_neu.py`, `prepare_historie.py`, `prepare_historie2.py`, `prepare_rii_dump.py`, `prepare_bund_neu.py`, `prepare_handbuecher.py`, `prepare_ustae.py`, `prepare_haftung.py` |
| Teilen in Abschnitte | `rebuild_structured_gpu.py`, `chunk_bmf_structured.py` |
| Laden und Nachbessern | `load_laws_neu.py`, `load_laws_kleb.py` |
| Prüfen | `audit_chunking.py`, `spacy_boundary_validator.py` |
| Ausfuhr | `make_korpus_export.py` |

Noch nicht enthalten: der allgemeine Lader für die übrigen Quellen, die Sammler für die Gerichtssammlungen (CE-BFH, CE-BVerfG, Open Legal Data) und die Skripte zu den amtlichen Handbüchern des BMF (dort wurden Seiten mit menschlicher Mitwirkung abgerufen).

Zugang zur Datenbank über die Umgebungsvariablen `PGHOST`, `PGUSER`, `PGPASSWORD` (für die Ausfuhr: `LM_DSN`).

Voraussetzungen: PostgreSQL mit pgvector, Python 3.11+, `sentence-transformers` (Modell `BAAI/bge-m3`), spaCy (`de_core_news_sm`).

Kein Rechts- oder Steuerrat. Keine externe Bewertung, keine Prüfung durch eine berufsrechtlich befugte Person.
