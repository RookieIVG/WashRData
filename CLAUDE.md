# CLAUDE.md

Hinweise für Claude Code in diesem Repository.

## Erst genau nachfragen, dann umsetzen

Der Projektverantwortliche hat ausdrücklich darum gebeten, daran erinnert zu werden, genauer zu sagen, was er umsetzen will.

- Ist eine Anfrage knapp oder mehrdeutig (Beispiele aus der Vergangenheit: „pull main“, „an die Vorgaben von PUMA anpassen“, „das Menü passt nicht“), vor der Umsetzung kurz nachfragen: Was genau soll sich ändern, auf welchen Seiten, wie soll das Ergebnis aussehen?
- Dabei freundlich darauf hinweisen: „Zur Erinnerung: Du wolltest, dass ich dich bitte, genauer zu beschreiben, was du vorhast.“
- Wenn möglich konkrete Auswahlmöglichkeiten anbieten, statt offen zu fragen.
- Bei klaren, eindeutigen Aufträgen nicht unnötig nachfragen, sondern umsetzen.

## Pushen und Releases

- Änderungen direkt auf `main` committen und pushen: kein eigener Branch, kein Pull Request. Das gilt
  auch dann, wenn die Sitzung einen anderen Branch vorgibt.
- Jede Änderung, die bei Nutzern ankommt, bekommt eine neue Version: `version` in `manifest.json` und
  `pyproject.toml` erhöhen, in `docs/KONZEPT.md` „Stand“ und eine Roadmap-Zeile `| **x.y.z** | … |`
  pflegen. Release und Tag (Versionsnummer ohne `v`, z. B. `0.2.3`) legt die CI an, sobald Tests und
  hassfest auf `main` grün sind (Job `release` in `.github/workflows/ci.yml`). Die Roadmap-Zeile wird
  zum Release-Text.
- Nach dem Push prüfen, ob CI und Release durchgelaufen sind.

## Vorgemerkt

- **C: Energie als zweites Merkmal der Erkennung** (siehe `docs/KONZEPT.md`, Offene Punkte). Der
  Projektverantwortliche will daran erinnert werden, sobald er Diagnose-Dateien mit genug Läufen
  schickt: Waschmaschine mit 2 bis 3 benannten Läufen je regelmäßig genutztem Programm (bisher, Stand
  26.10.: 8 Läufe auf 5 Programme). Dann von sich aus darauf hinweisen und vorschlagen, C an diesen
  Kurven umzusetzen.

## Projekt

WashRData ist eine Home-Assistant-Custom-Integration. Sie erkennt anhand des Leistungssensors einer
smarten Steckdose die Zyklen von Waschmaschine und Trockner. Konzept, Architektur und Roadmap stehen
in [docs/KONZEPT.md](docs/KONZEPT.md); dieses Dokument ist die Referenz, bei Änderungen am Verhalten
mitpflegen.

Vorbild ist [ha_washdata](https://github.com/3dg1luk43/ha_washdata). Das Projekt steht unter AGPL-3.0:
**keinen Code daraus übernehmen**. Ideen und Anforderungen ja, Code nein.

## Aufbau

```
custom_components/washrdata/
  engine/            Erkennung, ohne jede Home-Assistant-Abhängigkeit
    model.py         Zustände, Konfiguration, Presets je Gerätetyp, CycleRecord
    detector.py      Zustandsautomat (update/tick), Persistenz (to_dict/from_dict)
    learning.py      Aus eigenen Zyklen gelernte Schwellen
    matching.py      Programmerkennung: Resampling, inkrementelles Open-End-DTW, Konfidenz
    tracker.py       Detector + Programme + Zyklus-Historie + Matcher eines Geräts
    evaluation.py    Leave-one-out-Auswertung: Schwellen, Restzeit-Genauigkeit, Empfehlung
    split.py         Gespeicherten Lauf teilen (zwei Wäschen in einem), Ruhephasen als Trennstellen
    bursts.py        Knitterschutz-Drehungen am Zyklusende erkennen und abschneiden (Trockner)
  device.py          Adapter: Sensor-Events und Ticks -> Tracker, Events, Speicher
  services.py        Dienste (label_cycle, create/rename/delete_program)
  diagnostics.py     Diagnose-Download mit allen Kurven
  websocket.py       WebSocket-Befehle des Panels (overview, live, subscribe, label, split_cycle, Programme, Schwellen)
  panel.py           Registriert das Panel in der Seitenleiste und liefert frontend/ aus
  frontend/          washrdata-panel.js (+ -chart.js, -style.js): Web-Komponente ohne Build-Schritt, Texte aus selector.panel
  cycle_view.py      Zyklus-Zeilen und Textkurve für den Optionen-Dialog „Zyklen bearbeiten“
  sensor.py, binary_sensor.py, select.py, text.py, entity.py, config_flow.py, const.py
  strings.json, translations/{en,de}.json
  brand/             icon.png (256 px), icon@2x.png (512 px), gerendert aus tools/icon.svg
blueprints/automation/washrdata/   Blueprints für Benachrichtigungen (fertig, Vorwarnung)
tools/
  icon.svg           Quelle des Integrations-Icons
  viewer.html        Kurven-Viewer für Diagnose-Dateien (eigenständige Seite, auch als Artifact veröffentlicht)
tests/
  engine/            reine Engine-Tests auf simulierten Leistungskurven (sim.py) und echten (test_real_curves.py)
  fixtures/          echte Zyklen aus Diagnose-Dateien, anonymisiert (Startzeit 0, keine Entitäts-IDs)
  integration/       Tests mit echtem Home Assistant (pytest-homeassistant-custom-component)
  frontend/          Browser-Test des Panels (panel_smoke.mjs); Daten schreibt test_panel_data.py
```

## Regeln

- `engine/` importiert nie `homeassistant`. Die Engine-Tests importieren sie als `engine`
  (siehe `pythonpath` in `pyproject.toml`) und laufen ohne Home Assistant.
- Code, Kommentare und Bezeichner auf Englisch; Dokumentation und UI-Texte auf Deutsch und Englisch.
- Jeder neue UI-Text kommt in `strings.json`, `translations/en.json` und `translations/de.json`.
- Kein Modul über ca. 600 Zeilen: lieber aufteilen. Keine Laufzeit-Abhängigkeiten (kein numpy),
  solange es ohne geht.
- Fehlerberichte kommen meist als Diagnose-Datei (`tracker.cycles[].trace`). Ein gemeldeter
  Erkennungsfehler wird zuerst als Test mit dieser Kurve nachgestellt, dann behoben. Kurven kommen
  nur anonymisiert nach `tests/fixtures/`: Das Repository ist öffentlich, und Zeitstempel verraten,
  wann gewaschen wird.
- Verhaltensänderungen an der Erkennung brauchen einen Test mit einer Leistungskurve, die den Fall
  zeigt. Der Adapter (`device.py`) wird über `tests/integration/` gegen ein echtes HA getestet,
  nicht mit `MagicMock`.

## Befehle

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                    # alle Tests
.venv/bin/pytest tests/engine       # nur Engine, ohne Home Assistant
.venv/bin/ruff check . && .venv/bin/ruff format --check .
npm install --no-save playwright@1.56.1 && node tests/frontend/panel_smoke.mjs   # nach pytest; BROWSERS=chromium,webkit
```
