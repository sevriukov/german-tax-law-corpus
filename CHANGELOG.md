# Änderungen

## v0.2.0 – 20.10.2026
- Allgemeiner Lader (`load_new_sources.py`) mit eigenem GPU-Pool (`emb_sicher.py`) und Wächter (`storozh_laden.py`).
- Qualitätsprüfung der ganzen Datenbank (`rag_qualitaetstest.py`); Befunde 19.10.2026: Integrität und Indizes in Ordnung;
  fehlerhafte PDF-Schriften in 32 Bundestags- und BGBl-Dokumenten (22 656 unlesbare Abschnitte -> 53) – neu erkannt (`kaputt_neu.py`).
- Adressen von 12 163 Gesetzesparagrafen aus dem amtlichen Verzeichnis (`url_gesetze.py`).
- Doppelbesteuerungsabkommen nach Artikeln (`dba_artikel_prep.py`, 4 315 Artikel).
- EU-Recht in geltender Fassung über CELLAR, Erwägungsgründe gesondert (`bank_recht_eu.py`, `eu_daten_ki.py`).
- Grundsteuer der Länder: Gesetze, Begründungen der Landtage, Anwendungserlasse (`landes_grundsteuer_prep.py`, `landes_material_prep.py`).
- Titel: BaFin-Seiten (Cookie-Hinweis statt Titel), gleichlautende Gesetzestitel (Artikel/Datum aus dem amtlichen XML); Sperrschrift;
  Gesetzesabschnitte mit „Gesetz § N:“ (`bafin_titel_fix.py`, `vektor_umbau.py`).
- Abdeckung je Paragraf (`paragraf_abdeckung.py`); offene Literatur und Finanzgerichtsurteile (`oapen_recht.py`, `oldp_fg_api_laden.py`).
- Datenbankzugang in allen Skripten über `PGHOST`, `PGUSER`, `PGPASSWORD`.

## v0.1.0 – 09.10.2026
- Erstveröffentlichung: Sammeln, Aufbereiten, Teilen, Prüfen, Ausfuhr.
