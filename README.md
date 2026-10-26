# WashRData

Home-Assistant-Integration, die Waschmaschine und Trockner über den Leistungssensor einer smarten
Steckdose überwacht. Sie erkennt Start, Pausen und Ende eines Zyklus, misst Dauer und Energie und
lernt aus den eigenen Zyklen, wie lange ein Programm ruhig sein kann, ohne fertig zu sein.

> **Status:** 0.2: Zyklus-Erkennung, Programme, Programmerkennung und Restzeit. Weiteres siehe
> [Konzept und Roadmap](docs/KONZEPT.md).

> [!CAUTION]
> Waschmaschine und Trockner ziehen oft über 2000 W, stundenlang. Die Steckdose muss dafür
> ausgelegt sein (bei 230 V und 16 A sind das 3680 W). Unterdimensionierte Steckdosen können
> überhitzen.

## Funktionen

- Zyklus-Erkennung mit Hysterese und Startenergie: Standby und kurze Spitzen lösen nichts aus.
- Endverzögerung, die mitlernt: Einweichen und lange Pausen beenden den Zyklus nicht.
- Ein zu früh beendeter Zyklus wird fortgesetzt statt geteilt, und der Fehler wird gelernt.
- Zwei Läufe in einem (nächste Wäsche gleich hinterher)? In der App an der Pause teilen.
- Knitterschutz beim Trockner als eigener Zustand, nicht als neuer Zyklus, auch wenn die Trommel
  danach im 30-Sekunden-Takt weiterdreht: „fertig“ kommt, wenn das Trocknen fertig ist.
- Programme lernen: Zyklen benennen, danach erkennt WashRData das Programm beim nächsten Lauf,
  zeigt Restzeit, Fortschritt und Ende-Uhrzeit und benennt sicher erkannte Läufe selbst.
- Funktioniert mit Steckdosen, die nur Änderungen melden; übersteht Neustarts mitten im Programm.
- Nur lokal, keine Abhängigkeiten.

## Installation

**HACS:** dieses Repository als benutzerdefiniertes Repository (Typ *Integration*) hinzufügen,
WashRData installieren, Home Assistant neu starten.

**Manuell:** `custom_components/washrdata` nach `<config>/custom_components/` kopieren und neu starten.

Danach **Einstellungen > Geräte & Dienste > Integration hinzufügen > WashRData**: Name, Gerätetyp und
Leistungssensor wählen. Schwellen und Endverzögerung lassen sich unter **Konfigurieren >
Erkennungseinstellungen** anpassen, die Standardwerte
passen für die meisten Geräte.

## Programme anlernen

WashRData bringt keine Programme mit; jede Maschine ist anders. So lernt es deine:

1. **Waschen** wie immer. Jeder Lauf wird mit Leistungskurve gespeichert.
2. **Neues Programm benennen:** auf der Geräteseite in das Feld **Neues Programm** den Namen tippen
   (z. B. „Baumwolle 60“) und bestätigen. Das legt das Programm an und ordnet es dem laufenden,
   sonst dem letzten Zyklus zu. Danach ist das Feld wieder leer.
3. **Bekanntes Programm benennen:** in der Auswahl **Programm** wählen. Auch das benennt den
   laufenden Zyklus, danach den letzten.

Nach **zwei benannten Läufen** pro Programm erkennt WashRData es meist schon nach wenigen Minuten.
Programme, die sich nur in Temperatur oder Schleuderdrehzahl unterscheiden, sehen ähnlich aus. Hier
helfen mehr Läufe, oder du fasst sie zu einem Programm zusammen.

**Zyklen prüfen und korrigieren:** **Einstellungen > Geräte & Dienste > WashRData > Konfigurieren >
Zyklen bearbeiten** zeigt alle gespeicherten Läufe, neueste zuerst: Start, Dauer, Energie, Programm
(von Hand oder erkannt, mit Sicherheit) und die Leistungskurve als kleine Textgrafik. Einen Lauf
wählen, Programm setzen oder einen neuen Namen eintippen, speichern. Danach gleich den nächsten
Lauf bearbeiten oder fertig.

- Ist die Erkennung sicher genug (Attribut `confidence` am Programm-Sensor), zeigen Programm,
  Restzeit, Fortschritt und Ende-Uhrzeit Werte. Die Auswahl zeigt das erkannte Programm.
- Am Ende benennt WashRData sicher erkannte Läufe selbst (`program_source: auto`). So werden die
  Referenzen aktueller. Falsch? In der Auswahl korrigieren: Von Hand gewählt geht immer vor.
- Weiß WashRData, dass das Programm noch läuft, meldet es während einer Einweich-Pause nicht
  „fertig“.

## Entitäten

| Entität | Inhalt |
| :--- | :--- |
| `sensor.<name>_state` | `idle`, `starting`, `running`, `paused`, `finished`, `anti_crease` |
| `binary_sensor.<name>_running` | An während des ganzen Zyklus, auch in Pausen |
| `sensor.<name>_program` | Erkanntes oder gewähltes Programm; Attribute `confidence` (%), `candidates` |
| `select.<name>_program` | Programm des laufenden bzw. letzten Zyklus anzeigen und setzen |
| `text.<name>_new_program` | Neues Programm anlegen und dem laufenden bzw. letzten Zyklus zuordnen |
| `sensor.<name>_recognition_confidence` | Sicherheit der Erkennung im laufenden Zyklus (%) |
| `sensor.<name>_recognition_accuracy` | Trefferquote der Erkennung (%), je Programm als Attribut |
| `sensor.<name>_cycle_cost` / `_last_cycle_cost` | Kosten des laufenden bzw. letzten Zyklus (nur mit Strompreis) |
| `sensor.<name>_time_remaining` | Restzeit (min) |
| `sensor.<name>_expected_end` | Voraussichtliches Ende (Uhrzeit) |
| `sensor.<name>_progress` | Fortschritt (%) |
| `sensor.<name>_cycle_duration` / `_cycle_energy` | Aktueller Zyklus |
| `sensor.<name>_last_cycle_duration` / `_last_cycle_energy` | Letzter Zyklus (Details als Attribute) |
| `sensor.<name>_cycle_count` | Anzahl Zyklen |
| `sensor.<name>_total_energy` | Gesamtenergie, für das Energie-Dashboard |
| `sensor.<name>_end_delay` | Diagnose: gelernte Endverzögerung |

## Dienste

| Dienst | Zweck |
| :--- | :--- |
| `washrdata.label_cycle` | Zyklus einem Programm zuordnen (`cycle`: `current`, `last` oder ID; ohne `program`: Zuordnung entfernen) |
| `washrdata.create_program` | Programm anlegen |
| `washrdata.rename_program` | Programm umbenennen, samt Zyklen |
| `washrdata.delete_program` | Programm löschen, Zyklen bleiben unbenannt |

```yaml
action: washrdata.label_cycle
data:
  device_id: 0123456789abcdef
  program: Baumwolle 60
  cycle: last
```

## Automationen

**Benachrichtigungen per Blueprint:** zwei fertige Vorlagen für die Companion-App, Import mit einem
Klick:

- [**Meldung, wenn fertig**](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FRookieIVG%2FWashRData%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fwashrdata%2Fnotify_finished.yaml): Gerät und Handy wählen, fertig. Der Text
  enthält Programm, Dauer, Energie und Kosten; abgebrochene Läufe werden ausgelassen.
- [**Vorwarnung vor dem Ende**](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FRookieIVG%2FWashRData%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fwashrdata%2Fnotify_ending_soon.yaml): Restzeit-Sensor und Minuten wählen
  (Standard 10). Braucht ein erkanntes Programm, sonst gibt es keine Restzeit.

Titel und Text sind anpassbar, weitere Aktionen (Licht, Sprachausgabe) lassen sich anhängen.

Eigene Automationen:

Events: `washrdata_cycle_started`, `washrdata_program_detected`, `washrdata_cycle_finished`,
`washrdata_cycle_resumed`, `washrdata_anti_crease_started`, `washrdata_anti_crease_ended`.
`washrdata_program_detected` enthält zusätzlich `program`, `confidence` und `remaining_min`.

```yaml
triggers:
  - trigger: event
    event_type: washrdata_cycle_finished
    event_data:
      name: Waschmaschine
conditions:
  - condition: template
    value_template: "{{ not trigger.event.data.cycle.interrupted }}"
actions:
  - action: notify.mobile_app_handy
    data:
      message: >
        Waschmaschine fertig nach {{ trigger.event.data.cycle.duration_min | round }} min,
        {{ trigger.event.data.cycle.energy_kwh }} kWh.
```

## Kosten

In **Konfigurieren > Erkennungseinstellungen** eine Entität mit dem Strompreis pro kWh wählen, z. B.
`input_number.strompreis_energie`. Preise in ct/kWh werden in Euro umgerechnet. Die Kosten laufen
während des Zyklus mit: Ändert sich der Preis (dynamischer Tarif), gilt ab dann der neue. Dazu kommen
`sensor.<name>_cycle_cost` und `sensor.<name>_last_cycle_cost`, die Kosten stehen im Event
`washrdata_cycle_finished`, in der App und in der Statistik je Programm. Läufe von vor der Einrichtung
haben keine Kosten.

## WashRData-App

In der Seitenleiste steht der Menüpunkt **WashRData**, für alle Benutzer. Er zeigt je Gerät:

- **Jetzt:** Zustand, Programm, Erkennung mit Sicherheit, Restzeit und die Kurve des laufenden
  Zyklus über den Referenzen des erkannten Programms.
- **Erkennungsgenauigkeit:** Trefferquote gesamt und je Programm, mit Zahl der Referenzen.
- **Zyklen:** alle gespeicherten Läufe mit Kurve, Programm (direkt änderbar, auch neu anlegen) und
  was die Erkennung gesagt hat (✓ Treffer, ✗ Fehler). Ein Klick auf den Lauf zeigt die große Kurve,
  wahlweise über den Referenzen eines Programms: So sieht man, warum etwas verwechselt wurde.
- **Statistik je Programm:** Zahl der Läufe, Ø Dauer mit Spanne, Ø Energie, Ø Kosten, Läufe pro Woche.
- **Auswertung:** Jeder gespeicherte, benannte Lauf wird gegen die übrigen als Referenzen
  nachgespielt, Minute für Minute. Die Tabelle zeigt je Schwelle, wie oft zwischendurch ein falsches
  Programm erschienen wäre, ab welcher Minute das richtige erscheint und wie die Selbstbenennung
  ausgegangen wäre. Dazu kommt die Genauigkeit der Restzeit bei 25, 50 und 75 % des Laufs (Ø
  Abweichung und Tendenz). Ab fünf ausgewerteten Läufen schlägt die App Schwellen vor, ein Klick auf
  **Übernehmen** stellt sie ein. Von Hand geht das unter **Konfigurieren > Erkennungseinstellungen**.
- **Programme:** umbenennen und löschen.

**Zwei Wäschen in einem Lauf:** Startet die nächste Wäsche kurz nach dem Ende der vorigen, innerhalb
des Zeitfensters zum Fortsetzen (Waschmaschine 15 min, Trockner 10 min), legt WashRData beide zu einem
Lauf zusammen. Dann den Lauf in der App aufklappen und unter der Kurve **Teilen bei …** wählen: Die
App schlägt die Pause zwischen den Wäschen vor, du kannst auch in die Kurve tippen. Beide Teile
bekommen eigene Energie und Kosten und lassen sich einzeln benennen. Damit es nicht wieder passiert:
unter **Konfigurieren > Erkennungseinstellungen** das **Zeitfenster zum Fortsetzen** kürzer stellen
als die Zeit, die du zum Umladen brauchst (z. B. 5 min). Die **maximale Endverzögerung** begrenzt, wie
lange WashRData nach dem Gelernten auf das Ende wartet.

Treffer heißt: Die Erkennung war sich am Ende sicher (ab der Schwelle für die Selbstbenennung) und lag
richtig, du musstest nicht korrigieren. Läufe von vor Version 0.2.4 werden einmalig nachträglich
bewertet (Markierung „nachträglich“), damit die Quote nicht bei null anfängt.

## Läufe grafisch ansehen

Der **Kurven-Viewer** zeigt die Diagnose-Datei grafisch: alle Zyklen als Leistungskurven, mehrere
übereinandergelegt, je Programm gefiltert, ruhige Phasen und Schwellen markiert, dazu Statistik je
Programm. Lief beim Download gerade ein Waschgang, siehst du ihn mit den Referenzkurven, der
Restzeit und dem erwarteten Ende.

1. Diagnose-Datei herunterladen (siehe unten).
2. Den Viewer öffnen ([`tools/viewer.html`](tools/viewer.html), als veröffentlichte Seite oder lokal
   im Browser) und die Datei hineinziehen. Die Datei verlässt deinen Browser nicht.

Für die Zeitachse direkt in Home Assistant reicht eine *Verlaufsdiagramm*-Karte mit dem
Leistungssensor und `sensor.<name>_state`.

## Fehler melden und Verbesserungen vorschlagen

Am meisten hilft die **Diagnose-Datei**. Sie enthält Einstellungen, gelernte Werte, Programme und die
Leistungskurven der letzten Zyklen, also alles, um ein Problem genau nachzustellen.

1. **Einstellungen > Geräte & Dienste > WashRData**, das Gerät öffnen, im Menü ⋮
   **Diagnose herunterladen**. Am besten direkt nach dem Problem, solange der Zyklus zu den
   letzten 50 gehört.
2. Kurz notieren:
   - **Was** ist passiert, und **was** hättest du erwartet? (z. B. „fertig gemeldet, obwohl noch
     geschleudert wurde“, „Baumwolle als Kurz erkannt“, „Restzeit 20 min zu kurz“)
   - **Wann** ungefähr (Datum, Uhrzeit), damit sich der Zyklus finden lässt.
   - **Welches Programm** tatsächlich lief.
3. Schicken, auf einem dieser Wege:
   - **Hier im Chat mit Claude:** Datei hochladen und die Notizen dazuschreiben.
   - **Als GitHub-Issue** in diesem Repository: Notizen als Text, Datei per Drag & Drop anhängen
     (lehnt GitHub `.json` ab, die Datei vorher zippen). In einer Claude-Sitzung reicht dann
     „schau dir Issue #N an“.

Für Fehler im Home-Assistant-Log zusätzlich Debug-Logging einschalten und den Log-Auszug mitschicken:

```yaml
logger:
  logs:
    custom_components.washrdata: debug
```

Die Diagnose enthält keine Passwörter, aber Zeitstempel und Verbrauch. Daran ist ablesbar, wann du
wäschst. Bei einem öffentlichen Repository also abwägen.

Ein Wunsch braucht keine Datei: beschreiben, was du in welcher Situation erreichen willst.

## Entwicklung

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Aufbau und Regeln: [CLAUDE.md](CLAUDE.md). Konzept: [docs/KONZEPT.md](docs/KONZEPT.md).
