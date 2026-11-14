# Änderungen

## v0.4.2 – 14.11.2026
- `funding.json` (Format fundingjson.org v1.1.0) für den Förderkatalog FLOSS/fund: Projekt, Lizenzen, Kontakt kontakt@leckermath.de, Förderplan einmalig 10 000 USD (Pflege, neue Fassungen, offene Messung).

## v0.4.1 – 14.11.2026
- **Lizenz des Codes: Apache-2.0** — `LICENSE` (vollständiger Text der Apache License 2.0) und `NOTICE` (© 2026 Sergei Sevriukov) neu; README mit Abschnitt „Lizenz“. Der Datensatz auf Hugging Face bleibt unter CC BY 4.0.

## v0.4.0 – 13.11.2026 (Skripte; Datensatz nach Stand V3 folgt)
- Abgleich mit dem Ausgangstext (`quelle_lauf.py`): Tabellen werden über die eigene Tabellenschicht geprüft, Randnummern als eigene Zeile, Spaltenüberschriften nur bei Tabellenzeilen.
- Sperrschrift und zerrissene Wörter (`vektor_umbau.py`): Zusammenführung nach den Worthäufigkeiten der Datenbank selbst; zusammengesetzte Wörter werden nicht getrennt.
- Gesetze als Gliederungsbaum bis zur Ebene Buchstabe (`struktur_baum.py`); Verwaltungsanweisungen, Handbücher und Entscheidungen (`struktur_verwaltung*.py`, `struktur_gerichte.py`): Überschriften ungekürzt, Einleitungssatz als eigenes Feld, neu geteilt werden nur Dokumente, die die Prüfung nicht bestanden haben.
- Abdeckung je Paragraf (`paragraf_abdeckung.py`): Paragrafen aus Änderungsartikeln von Gesetzentwürfen.
- Ausfuhr (`make_korpus_export.py`): Dokumente mit Kennzeichen „klassifiziert“, „geschützt“ oder Lizenz „intern …“ werden nicht ausgeführt. **Hinweis:** In Datensatz 1.0.1 waren 3 133 Entscheidungen aus Open Legal Data mit Lizenz „intern (§ 87a UrhG)“ enthalten; sie werden in der nächsten Datensatzversion entfernt.
- Wächter (`storozh_laden.py`): Stillezeit ab Start, letzte Fehlerzeile im Bericht.
- Datenbankzugang ohne Passwort im Code (`~/.pgpass` bzw. `PGPASSFILE`).
- Bewusst nicht im Korpus: Bundesgesetzblatt (wiederholt geltende Gesetze und Fassungen; Originaldateien bleiben in der Sammlung) und abgeschlossene Gesetzentwürfe (nur laufende Entwürfe).

## v0.3.0 – 02.11.2026 (Datensatz 1.0.1)
- Teilung nur nach der eigenen Gliederung jedes Dokuments, nicht nach Zeichenzahl: Gesetz § › Abs. › Satz/Nr. › Buchst. (`struktur_laws.py`, `struktur_innen_laws.py`, `struktur_historie.py`), Entscheidung Leitsatz/Tenor/Tatbestand/Gründe › Rn. (`struktur_gerichte.py`), Verwaltungsanweisung Abschnitt/Rz./Tz. (`struktur_verwaltung.py`); jeder Abschnitt trägt seinen Gliederungspfad, der Einleitungssatz einer Aufzählung steht in eigenem Feld.
- BMF-Handbücher (`struktur_verwaltung_hb.py`): Überschriften R/H, UStAE-Abschnitte, AEAO; „§ N“ im Fließtext (im Ausgangstext eigene Zeile) gilt nicht mehr als Überschrift – 452 Dokumente mit verschobenem Pfad werden in 1.1 neu geteilt.
- Abgleich jedes Abschnitts mit dem Ausgangstext (`quelle_lauf.py`): Stelle, Abweichung, Status (`genau` bei 98,4 % der Abschnitte).
- `make_korpus_export.py`: Ausfuhr der geteilten Abschnitte (`data/fragments`: Pfad, Einleitung, Text, Abgleich), nur Dokumente des eingefrorenen Fundaments.
- `prepare_rii_bverwg_bag.py`: Entscheidungen von BVerwG und BAG aus rechtsprechung-im-internet.de (Gründe bei Urteil und Beschluss).

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
