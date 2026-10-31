# WashRData: Konzept

Stand: Version 0.2.9 (Programme und Erkennung), Oktober 2026.

## Ziel

WashRData überwacht Waschmaschine und Trockner über den Leistungssensor einer smarten Steckdose in
Home Assistant. Es erkennt Start, Pausen und Ende eines Zyklus und misst Dauer und Energie. Später
kommen Programmerkennung, Restzeit, Kosten, Zusatzsensoren und Wartung dazu.

„Besser als das Vorbild“ heißt hier drei Dinge:

1. **Genauere Erkennung:** kein vorzeitiges Ende beim Einweichen, kein neuer Zyklus durch
   Knitterschutz, keine verlorenen Messwerte.
2. **Schlanke Architektur:** kleine, verständliche Module und eine Erkennung, die ohne Home Assistant
   testbar ist.
3. **Mehr Features:** Kosten und Tarife, Zusatzsensoren, Wartungsprognose, Wasserverbrauch.

## Ausgangslage: ha_washdata

[ha_washdata](https://github.com/3dg1luk43/ha_washdata) (Version 0.5.8, AGPL-3.0) ist ausgereift und
verbreitet. Es kann Zyklen erkennen, Programme zuordnen und die Restzeit schätzen. Es hat ein eigenes
Panel, einen Community-Store und 35 Sprachen.

| Thema | Beobachtung | Folgerung für WashRData |
| :--- | :--- | :--- |
| Umfang | ~60.000 Zeilen Python in der Integration; `manager.py` 9.500, `profile_store.py` 11.400, `cycle_detector.py` 6.000 Zeilen | Module klein halten (Richtwert 600 Zeilen); Erkennung als eigene Bibliothek |
| Einstellungen | Viele Tuning-Parameter (Off Delay, Min Off Gap, Start Energy ...), dazu eine Vorschlags-Engine, die sie nachjustiert | Wenige Einstellungen; die Schwellen, auf die es ankommt, aus den eigenen Zyklen lernen |
| Abhängigkeiten | `numpy` als Laufzeit-Abhängigkeit | Reines Python, solange es reicht |
| Häufige Issues | Knitterschutz (#68, #296, #343), Ende-Erkennung bei hängenden oder gleichbleibenden Messwerten (#101, #363), Fehler im HA-Log (#144, #245, #405), verlorene Profile (#57) | Knitterschutz als eigener Zustand; Tick und `state_reported`; robuste Persistenz; Tests gegen echtes HA |
| Tests | Viele Tests, laut eigener Doku ein Drittel mit `MagicMock` statt echtem HA; Fehler blieben deshalb monatelang unentdeckt | Engine-Tests ohne HA, Adapter-Tests immer gegen echtes HA |

Code aus ha_washdata wird nicht übernommen (AGPL). WashRData ist eine Neuentwicklung.

## Architektur

```
 Steckdose (sensor.*_power)
        │ state_changed / state_reported      Tick alle 30 s
        ▼                                          │
 ┌─────────────────── device.py (Adapter) ─────────┴──────────┐
 │ Einheit -> W, unavailable -> None, Zeitstempel ordnen       │
 │                    │                                        │
 │                    ▼                                        │
 │  ┌──────────── engine/ (ohne Home Assistant) ───────────┐  │
 │  │ CycleDetector: Zustandsautomat, Energie, Verlauf      │  │
 │  │ LearnedStats:  gelernte Endverzögerung                │  │
 │  └───────────────────────────────────────────────────────┘  │
 │                    │ Events                                  │
 │                    ▼                                        │
 │  HA-Events, Speicher (Store), Dispatcher -> Entitäten       │
 └─────────────────────────────────────────────────────────────┘
```

- **engine/** bekommt nur Paare aus Zeitstempel und Watt. Sie kennt weder Home Assistant noch Uhrzeit
  noch Einheiten, ist deterministisch und lässt sich Kurve für Kurve testen.
- **device.py** übersetzt zwischen Home Assistant und Engine, speichert den Zustand und feuert Events.
- **Entitäten** lesen nur vom Gerät, sie rechnen nichts selbst.

## Zustandsmodell

| Zustand | Bedeutung |
| :--- | :--- |
| `idle` | Nichts los (aus oder Standby) |
| `starting` | Aktivität erkannt, aber noch nicht genug Energie für einen Zyklus |
| `running` | Zyklus läuft, Leistung über der Stoppschwelle |
| `paused` | Zyklus läuft, aber gerade ruhig (Einweichen, Pause oder ein noch nicht bestätigtes Ende) |
| `finished` | Zyklus beendet. Kommt kurz danach anhaltend Leistung, wird er fortgesetzt |
| `anti_crease` | Knitterschutz: kurze Trommelbewegungen nach dem Ende, kein neuer Zyklus |

`binary_sensor.<name>_running` ist in `running` und `paused` an, also über den ganzen Zyklus.

## Erkennung

**Hysterese.** Ab der Startleistung gilt das Gerät als aktiv, unter der Stoppleistung als ruhig.
Dazwischen bleibt der vorige Zustand. Ein Display-Standby zwischen den Schwellen startet so nichts, und
eine kurze Schwankung im Lauf beendet nichts.

**Start über Energie.** Ein Zyklus beginnt erst, wenn die Aktivität genug Energie verbraucht hat
(Waschmaschine 2 Wh, Trockner 5 Wh). Kurze Spitzen fallen so heraus. Als Startzeit gilt der Beginn der
Aktivität, nicht der Moment der Bestätigung.

**Ende mit lernender Verzögerung.** Ein Zyklus endet, wenn das Gerät eine bestimmte Zeit ruhig ist.
Als Endzeit zählt der Beginn der Ruhe, nicht ihr Ablauf. Die Verzögerung ist ein Mindestwert
(Waschmaschine 5 min, Trockner 3 min). WashRData merkt sich die längste Pause der letzten 20 Zyklen und
setzt die Verzögerung auf das 1,25-Fache plus 1 min, höchstens bis zu einer Obergrenze (Waschmaschine
60 min, Trockner 20 min, in den Optionen einstellbar). Ein Programm mit 12 Minuten Einweichen bringt dem Gerät also bei, nicht nach
5 Minuten Ruhe das Ende auszurufen.

**Fortsetzen statt Teilen.** Kommt innerhalb des Fortsetzungsfensters (Waschmaschine 15 min, Trockner
10 min) nach einem Ende wieder anhaltend Leistung, wird der Zyklus wieder geöffnet (Event
`washrdata_cycle_resumed`), statt einen zweiten anzulegen. Die Lücke zählt als Pause und fließt ins
Lernen ein. Derselbe Fehler passiert beim nächsten Mal nicht wieder. Das Fenster ist in den Optionen
einstellbar, 0 schaltet das Fortsetzen ab.

Die Kehrseite: Startet die nächste Wäsche innerhalb des Fensters, werden zwei Läufe zu einem (an einer
echten Kurve gefunden, 0.2.7: zweite Wäsche 12 min nach der ersten). Die Lücke treibt dann auch die
gelernte Endverzögerung hoch, was das nächste Zusammenlegen wahrscheinlicher macht. Abhilfe: Fenster
kürzer als die Zeit zum Umladen stellen (z. B. 5 bis 10 min) und den Lauf teilen.

**Teilen.** `engine/split.py` teilt einen gespeicherten Lauf. Liegt die Trennstelle in einer Ruhephase
(nach der Hysterese des Detectors), endet der erste Teil an ihrem Beginn und der zweite beginnt an
ihrem Ende; die Lücke gehört zu keinem. Energie und Kosten werden nach dem Anteil an der Kurve
aufgeteilt, Pausen und Spitzenleistung je Teil aus der Kurve neu bestimmt. Beide Teile verlieren
Programm und Erkennung und werden neu benannt. Der Eintrag des Laufs in der gelernten Endverzögerung
wird durch die längsten Pausen der beiden Teile ersetzt, die Lücke vergisst das Gerät also wieder.
Das Panel bietet die längsten Ruhephasen (ab 2 min) als Trennstelle an, man kann auch in die Kurve
tippen (`washrdata/split_cycle`).

**Knitterschutz.** Nach dem Ende eines Trocknerzyklus gelten kurze Aktivitäten (höchstens 4 min) als
Knitterschutz. Sie werden gezählt, sind aber kein neuer Zyklus. Nach 15 Minuten ohne Bewegung (oder
spätestens 2 Stunden nach dem Ende) ist der Knitterschutz vorbei. Erst eine längere Aktivität startet
einen neuen Zyklus. Bei Waschmaschinen lässt sich das in den Optionen einschalten.

Manche Trockner drehen die Trommel nach Trocknen und Kühlen etwa alle 30 Sekunden für 5 bis 10 Sekunden
(an einem echten Wärmepumpentrockner gefunden, 0.2.8; `tests/fixtures/dryer_anti_crease.json`). Die
Pausen dazwischen sind viel kürzer als jede Endverzögerung, also zählten die Drehungen zum Zyklus und
„fertig“ kam 20 bis 30 Minuten zu spät. `engine/bursts.py` erkennt das am Ende jeder Aktivität (mit
eingeschaltetem Knitterschutz): Folgen auf die letzte lange Aktivität mindestens 6 Drehungen von
höchstens 60 s (Pausen unter 10 s überbrückt), endet der Zyklus dort, wo die Drehungen begannen. Die
Drehungen werden Knitterschutz-Stöße, ihre Energie, Kosten und Pausen fallen aus dem Zyklus. Nach dem
Ende zählt eine Drehung, sobald die Trommel 10 s steht (vorher 30 s), sodass ein Stoß-Takt von 30 s
nicht als ein langer, neuer Zyklus durchgeht. Gespeicherte Zyklen, die noch Drehungen enthalten,
werden beim Start einmalig gekürzt.

**Messwerte, die ausbleiben.** Viele Steckdosen melden nur Änderungen. Damit Zeitgrenzen trotzdem
greifen, bekommt die Engine alle 30 s einen Tick mit dem letzten Wert. Zusätzlich wertet der Adapter
`state_reported` aus, also Meldungen mit gleichem Wert. Ein Messwert, der wegen eines dazwischen
gelaufenen Ticks älter wirkt, wird auf den letzten Zeitpunkt gehoben statt verworfen.

**Ausfall.** Ist der Sensor `unavailable`, läuft die Zeit weiter, aber ohne Energie und ohne
Zustandswechsel. Erst nach 15 Minuten ohne Messwert endet ein laufender Zyklus, markiert als
unterbrochen. Lücken über 15 Minuten (etwa ein HA-Neustart) werden nicht hochgerechnet.

**Unterbrochen.** Ein beendeter Zyklus, der kürzer als die Mindestdauer ist (Waschmaschine 10 min,
Trockner 15 min), wird als `interrupted` markiert und nicht zum Lernen benutzt.

## Programme und Erkennung

**Programme** sind Namen, die der Nutzer anlegt. Ein Zyklus wird einem Programm zugeordnet: von Hand
über die Auswahl, im Optionen-Dialog unter „Zyklen bearbeiten“ oder mit dem Dienst `label_cycle`, oder
automatisch, wenn die Erkennung am Ende sicher ist. „Zyklen bearbeiten“ listet die gespeicherten
Zyklen, neueste zuerst, mit Start, Dauer, Energie, Programm, Herkunft (von Hand oder erkannt, mit
Sicherheit) und der Leistungskurve als Textgrafik aus Blockzeichen (je Zeitabschnitt die höchste
Leistung, auf die Spitze skaliert, mit Wurzelskala, damit der Motor neben der Heizung sichtbar bleibt).
Wer ein Programm dort setzt oder ein erkanntes bestätigt, benennt den Zyklus von Hand. Ein neuer Name
legt das Programm an. Die **Referenzen** eines Programms sind seine letzten fünf benannten, nicht
unterbrochenen Zyklen. Von Hand benannte gehen vor.

**Kurven vergleichen.** Jede Kurve wird auf einen Mittelwert pro Minute gebracht und mit
`log1p(W / 20)` gestaucht. Ein Heizstab mit 2000 W und eine Trommel mit 250 W unterscheiden sich so
deutlich, 0 W und 3 W Standby dagegen kaum. Der laufende Zyklus wird per **Dynamic Time Warping mit
offenem Ende** mit jeder Referenz verglichen. Das ganze bisherige Stück wird gegen den
bestpassenden *Anfang* der Referenz gelegt. Daraus kommen zwei Dinge:

- die **Distanz**: mittlere Abweichung pro Minute, ergibt das Programm,
- die **Position** in der Referenz, ergibt Restzeit und Fortschritt. Die Restzeit ist der Median
  über die passenden Referenzen des Programms, damit verdirbt eine falsch benannte Referenz nichts.

Zeitverzerrung (etwa eine längere Heizphase im Winter) kostet einen kleinen Aufschlag (0,05 pro
Schritt). Gewarpt wird nur, wo es die Kurven wirklich besser übereinanderlegt. Insgesamt darf der
bisherige Lauf höchstens auf ein 1,5-mal kürzeres oder längeres Stück der Referenz passen (plus 5 min
Spielraum). Sonst könnte ein kurzes, kaltes Programm am Anfang einer heißen Referenz „hängen
bleiben“ und wie eine sehr langsame, perfekte Übereinstimmung aussehen (an einer echten Kurve
gefunden, 0.2.1). Die Rechnung ist
inkrementell: Jede neue Minute fügt pro Referenz eine Zeile hinzu. Das sind wenige Millisekunden pro
Minute. Ein Neuaufbau, etwa nach einem Neustart mitten im Programm, dauert im ungünstigsten Fall
(10 Programme × 5 Referenzen, 3 h) rund 0,7 s.

**Sicherheit (confidence).** Sie setzt sich zusammen aus der Güte des besten Programms
(`1 − Distanz / 0,8`) und dem Abstand zum Zweitbesten. Zwei Programme, die gleich beginnen, ergeben
also eine niedrige Sicherheit, bis sie sich unterscheiden. „Kein bekanntes Programm“ tritt immer mit
der Distanz 0,25 als Gegenkandidat an. Auch ein einzelnes Programm braucht deshalb eine wirklich gute
Übereinstimmung, nicht nur die einzige.

Grenze bei nur einer Referenz: Ein längeres, heißeres Programm lässt sich unterwegs nicht von
demselben Programm mit kaltem Zulauf unterscheiden und kann als dieses angezeigt werden. Am Ende wird
es aber nicht automatisch benannt. Sobald es einmal von Hand benannt ist, werden beide sicher getrennt.

| Schwelle | Wert | Wirkung |
| :--- | :--- | :--- |
| Anzeige | ≥ 25 % ab 5 min Laufzeit (einstellbar) | Programm, Restzeit, Fortschritt und Ende-Uhrzeit werden gezeigt |
| Ende zurückhalten | ≥ 50 % und noch ≥ 10 min Rest | Eine ruhige Phase beendet den Zyklus erst nach der maximalen Endverzögerung |
| Selbst benennen | ≥ 60 % am Ende (einstellbar), Länge passt (Faktor ≤ 1,4) | Zyklus bekommt das Programm (`program_source: auto`) |

Am Ende zählt der Vergleich bis zum Beginn der letzten Ruhephase, nicht die Ruheminuten danach.
Eine Auswahl von Hand während des Laufs gewinnt immer: Restzeit und Fortschritt kommen dann aus den
Referenzen des gewählten Programms, und die automatische Benennung wird nicht angewendet.

**Genauigkeit.** Am Ende jedes Laufs, für den es schon Referenzen gab, hält der Zyklus fest, was die
Erkennung allein sagt (`recognised`, `match_confidence`), auch wenn er von Hand benannt ist. Das
bleibt beim späteren Umbenennen des Zyklus stehen. Dazu kommen die Programme, die zu dem Zeitpunkt
Referenzen hatten (`known_programs`). Bewertet werden vollständige, benannte Läufe, deren Programm
darunter war. Ein Treffer ist eine sichere Erkennung (≥ 60 %, die Schwelle der Selbstbenennung) genau
dieses Programms. Jede Korrektur einer Selbstbenennung und jeder von Hand benannte Lauf, den die
Erkennung verfehlt hat, ist ein Fehler. Hat die Erkennung das richtige Programm genannt, aber unter
dieser Schwelle, zählt der Lauf als „unsicher“ (`unsure`, im Panel ○ statt ✗); er bleibt ein Fehler
der Selbstbenennung, wird aber getrennt ausgewiesen. Der erste Lauf eines Programms wird nicht
bewertet, im Panel aber mit ✗ markiert, wenn die Erkennung ihn sicher einem anderen Programm
zugeordnet hat. Läufe von vor 0.2.4 haben diese Angaben nicht und zählen
nicht, bis die Auswertung sie nachträglich bewertet (`recognition_retro`, siehe unten). Live zeigt
`sensor.<name>_recognition_confidence` die Sicherheit der Erkennung, auch während ein von Hand
gewähltes Programm sie überstimmt.

**Auswertung (leave-one-out).** `engine/evaluation.py` spielt jeden gespeicherten, benannten,
vollständigen Lauf Minute für Minute nach, mit den übrigen Läufen als Referenzen (ein Lauf ist nie
seine eigene Referenz). Je Minute hält sie fest, was die Erkennung allein sagt und wie sicher, und
welche Restzeit die Referenzen des wirklichen Programms ergeben. Daraus:

- je Schwelle von 15 bis 80 %: Läufe, die zwischendurch ein falsches Programm gezeigt hätten; Läufe,
  die das richtige nie gezeigt hätten; Median der Minute, ab der das richtige erscheint; wie die
  Selbstbenennung am Ende ausginge (richtig, falsch, keine),
- die Restzeit bei 25, 50 und 75 % des Laufs: mittlere absolute Abweichung und Tendenz
  (vorhergesagt minus tatsächlich, + heißt zu lang),
- eine Empfehlung ab fünf ausgewerteten Läufen: Anzeige ab der niedrigsten Schwelle (mindestens
  15 %), bei der höchstens 10 % der Läufe zwischendurch falsch gezeigt hätten; Selbstbenennung ab der
  niedrigsten Schwelle (mindestens 40 %) ohne falsche Benennung; die Anzeigeschwelle nie über der
  für die Selbstbenennung.

**Neue Programme.** Ein Lauf, dessen Programm sonst keinen Lauf hat, wird auch nachgespielt, gegen
die übrigen Programme. Erkennt die Erkennung ihn sicher als ein anderes Programm, ist das eine falsche
Selbstbenennung (an echten Kurven gefunden, 0.2.8: der erste Pflegeleicht 60 °C wäre mit 73 % als
Pflegeleicht 30 °C benannt worden; die beiden unterscheiden sich nur in der Heizdauer). Diese Läufe
zählen bei „falsch gezeigt“ und „Benennung falsch“ mit. Die Empfehlung für die Selbstbenennung muss
ohne falsche Benennung auskommen und trotzdem mindestens einen Lauf richtig benennen. Gibt es keine
solche Schwelle, empfiehlt die Auswertung nichts (`note: no_safe_threshold`); zu wenige Läufe
bekannter Programme ergeben `need_more`.

Läufe ohne festgehaltene Erkennung (vor 0.2.4) bekommen einmalig das nachgespielte Ergebnis. Die
Auswertung läuft 30 s nach dem Start und nach jeder Änderung (beendeter Lauf, Benennung) in einem
eigenen Thread auf einer Kopie der Zyklen; eine volle Historie braucht etwa eine Sekunde. Das
Ergebnis steht im Panel und im Diagnose-Download. Die Schwellen für Anzeige und Selbstbenennung sind
je Gerät einstellbar (Optionen oder „Übernehmen“ im Panel), Standard 25 und 60 %.

**Speicher.** Die letzten 50 Zyklen bleiben erhalten, dazu je Programm die letzten 10 benannten,
auch wenn sie älter sind.

## Daten und Persistenz

Je Gerät speichert ein Home Assistant `Store` (`.storage/washrdata.<entry_id>`):

- den vollständigen Zustand der Engine, auch einen laufenden Zyklus. Ein Neustart oder Reload mitten
  im Programm setzt den Zyklus fort.
- die gelernten Werte,
- die Programme und die Zyklen (siehe oben) mit komprimiertem Leistungsverlauf und Programmzuordnung.

Speicherstände von 0.1 werden ohne Migration gelesen; Programme sind dort einfach leer.

**Diagnose.** Der Diagnose-Download des Geräts enthält Konfiguration, Engine-Zustand, Programme,
Referenzen, aktuelle Erkennung und alle gespeicherten Kurven. Er ist das Format für Fehlerberichte;
ein gemeldeter Fehler wird zuerst als Test mit der Kurve aus der Datei nachgestellt.

Gespeichert wird verzögert (30 s) und sofort am Zyklusende.

## Entitäten, Dienste und Events

| Entität | Inhalt |
| :--- | :--- |
| `sensor.<name>_state` | Zustand (siehe oben) |
| `binary_sensor.<name>_running` | An während des ganzen Zyklus |
| `sensor.<name>_program` | Erkanntes oder gewähltes Programm, Attribute `confidence`, `manual`, `candidates` |
| `select.<name>_program` | Programm des laufenden, sonst des letzten Zyklus; Auswahl benennt ihn |
| `sensor.<name>_recognition_confidence` | Sicherheit der Erkennung im laufenden Zyklus (%), Attribut `program` |
| `sensor.<name>_recognition_accuracy` | Trefferquote der Erkennung (%), Attribute `cycles`, `hits`, `programs` |
| `sensor.<name>_cycle_cost`, `_last_cycle_cost` | Kosten des laufenden bzw. letzten Zyklus, nur mit Strompreis-Entität |
| `sensor.<name>_time_remaining` | Restzeit (min) |
| `sensor.<name>_expected_end` | Voraussichtliches Ende (Zeitstempel) |
| `sensor.<name>_progress` | Fortschritt (%) |
| `sensor.<name>_cycle_duration` | Laufzeit des aktuellen Zyklus (min) |
| `sensor.<name>_cycle_energy` | Energie des aktuellen Zyklus (kWh) |
| `sensor.<name>_last_cycle_duration` | Dauer des letzten Zyklus, mit Details als Attribute |
| `sensor.<name>_last_cycle_energy` | Energie des letzten Zyklus (kWh) |
| `sensor.<name>_cycle_count` | Anzahl Zyklen |
| `sensor.<name>_total_energy` | Gesamtenergie (kWh), für das Energie-Dashboard |
| `sensor.<name>_end_delay` | Diagnose: aktuell wirksame, gelernte Endverzögerung |

Dienste: `washrdata.label_cycle`, `create_program`, `rename_program`, `delete_program`.

**Panel.** Die Integration bringt eine eigene Seite in der Seitenleiste mit („WashRData“, für alle
Benutzer). Sie ist eine Web-Komponente ohne Build-Schritt (`frontend/washrdata-panel.js`), die
`panel.py` ausliefert und registriert. Daten kommen über WebSocket-Befehle (`websocket.py`):
`washrdata/overview` liefert je Gerät Zustand, laufenden Zyklus mit Erkennung, Programme,
Genauigkeit, Auswertung, Schwellen und alle Zyklen mit Kurve (Mittelwert pro Minute) und eine
`revision`. `washrdata/subscribe` meldet Änderungen; das Panel holt dann höchstens alle 5 s nur
`washrdata/live` (laufender Zyklus und `revision`) und die ganze Übersicht erst, wenn sich die
`revision` geändert hat (gespeicherte Zyklen, Programme, Auswertung). `washrdata/label`,
`rename_program`, `delete_program` und `set_thresholds` ändern. Ein Browser-Test
(`tests/frontend/panel_smoke.mjs`) lädt das Panel in Chromium mit Daten, die ein Integrationstest aus
einem echten Home Assistant schreibt, und prüft Anzeige und Bedienung, in Chromium und in WebKit
(iPhone). Er lädt das Modul wie Home Assistant per Script-Tag und legt das Element erst beim
Load-Event an oder schon vorher (Upgrade mit bereits gesetztem `hass`). Deshalb kein top-level
`await` im Panel-Modul: Das Load-Event kommt, bevor ein solches `await` fertig ist. Das Panel zeigt:
den laufenden Zyklus mit Live-Sicherheit und Kurve über den Referenzen, die Trefferquote gesamt und
je Programm, alle Zyklen mit Kurve, Programm (direkt änderbar), Erkennung und Treffer/Fehler, beim
Aufklappen die große Kurve im Vergleich mit den Referenzen eines wählbaren Programms, und die
Programmliste zum Umbenennen und Löschen. Texte stehen in den Übersetzungen unter
`selector.panel`.

Events: `washrdata_cycle_started`, `washrdata_program_detected`, `washrdata_cycle_finished`,
`washrdata_cycle_resumed`, `washrdata_anti_crease_started`, `washrdata_anti_crease_ended`. Jedes
Event enthält `entry_id`, `name`, `device_type` und eine Zusammenfassung des Zyklus (Start, Ende,
Dauer, Energie, Spitzenleistung, `interrupted`, `resumed`, `anti_crease_bursts`, `program`,
`program_source`, `match_confidence`, `recognised`, `cost`).

**Kosten.** Mit einer Strompreis-Entität in den Optionen (`input_number`, `number` oder `sensor`, Preis
pro kWh, ct/kWh wird umgerechnet) rechnet der Detector jedes Energie-Stück mit dem gerade gültigen
Preis ab (`CycleRecord.cost`). Ein Preiswechsel während des Laufs gilt ab dann; solange der Preis
unbekannt ist, kommt nichts dazu.

**Blueprints.** `blueprints/automation/washrdata/`: `notify_finished.yaml` (Event
`washrdata_cycle_finished`, gefiltert auf das gewählte Gerät, Meldung an die Companion-App) und
`notify_ending_soon.yaml` (Restzeit-Sensor fällt unter N Minuten). Getestet in einem echten Home
Assistant, die Meldung durch ein Event ersetzt. `program_detected` enthält zusätzlich `program`, `confidence`
und `remaining_min`.

## Roadmap

| Version | Inhalt |
| :--- | :--- |
| **0.1 Grundgerüst** (erledigt) | Engine mit Zustandsautomat, lernender Endverzögerung, Fortsetzen und Knitterschutz; Integration mit Config- und Options-Flow, Sensoren, Events, Persistenz; Waschmaschine und Trockner |
| **0.2 Programme und Restzeit** (dieser Stand) | Programme anlegen und Zyklen benennen (Auswahl, Dienste); Erkennung per Open-End-DTW ohne numpy, mit Sicherheit; Restzeit, Fortschritt, Ende-Uhrzeit; programmabhängiges Ende; Selbstbenennung sicher erkannter Läufe; Diagnose-Download |
| **0.2.1** | Programm-Erkennung robuster bei nur einer Referenz (begrenzte Dehnung, „unbekannt“ als Gegenkandidat), getestet an einer echten Kurve; Textfeld „Neues Programm“ am Gerät |
| **0.2.2** | Repository zu RookieIVG umgezogen: Links und Codeowner im Manifest angepasst |
| **0.2.3** | Optionen-Dialog „Zyklen bearbeiten“: gespeicherte Zyklen mit Start, Dauer, Energie, Programm, Herkunft und Kurve als Textgrafik; Programm prüfen, korrigieren oder neu anlegen |
| **0.2.4** | WashRData-App in der Seitenleiste: laufender Zyklus, Zyklen mit Kurve und Kurvenvergleich mit Referenzen, Programm direkt korrigieren, Programme umbenennen und löschen; Erkennung wird pro Lauf festgehalten; Sensoren Erkennungssicherheit (live) und Trefferquote, gesamt und je Programm |
| **0.2.5** | Auswertung der gespeicherten Läufe (leave-one-out): Schwellen-Tabelle mit Empfehlung zum Übernehmen, Genauigkeit der Restzeit, ältere Läufe nachträglich bewertet; Schwellen für Anzeige und Selbstbenennung einstellbar; Kosten pro Lauf aus einer Strompreis-Entität; Statistik je Programm; Blueprints für „fertig“ und Vorwarnung; Panel lädt nur noch den laufenden Zyklus nach; Browser-Test des Panels in der CI |
| **0.2.6** | Fehlerbehebung: Die App blieb seit 0.2.5 leer (Styles per top-level await geladen, das Element war beim Anlegen durch Home Assistant noch nicht definiert). Browser-Test lädt das Panel jetzt wie Home Assistant, auch mit früh angelegtem Element, in Chromium und WebKit (iPhone) |
| **0.2.7** | Läufe teilen: In der App einen Lauf mit zwei Wäschen an der Pause dazwischen (oder an einer angetippten Stelle) in zwei Läufe teilen; Energie und Kosten werden aufgeteilt, die Lücke aus der gelernten Endverzögerung entfernt. Zeitfenster zum Fortsetzen und maximale Endverzögerung in den Erkennungseinstellungen. Getestet an einer echten Kurve mit zwei zusammengelegten Wäschen |
| **0.2.8** | Trockner: Knitterschutz-Drehungen am Ende (alle ~30 s für wenige Sekunden) beenden den Zyklus dort, wo sie beginnen; „fertig“ kommt 20 bis 30 min früher, gespeicherte Läufe werden einmalig gekürzt. Auswertung: Der erste Lauf eines Programms wird mitgeprüft, damit keine Schwelle empfohlen wird, bei der ein neues Programm falsch benannt würde. Beides getestet an echten Kurven |
| **0.2.9** | App: Erkennung je Lauf mit drei Zeichen: ✓ richtig und sicher genug zum Selbstbenennen, ○ richtig getippt, aber zu unsicher, ✗ falsch, auch beim ersten Lauf eines neuen Programms, der einen fremden Namen bekommen hätte. Trefferquote weist „richtig, aber unsicher“ getrennt aus (auch als Attribut `unsure`) |
| **0.2.x** | Kalibrierung der Schwellen an echten Kurven aus Diagnose-Dateien (Testkorpus, Leave-one-out-Messung); bei Bedarf Import alter Zyklen aus dem Recorder |
| **0.3 Zusatzsensoren und Benachrichtigungen** | Türkontakt (Ende bestätigen, Erinnerung „Wäsche noch drin“), Vibrationssensor (Pause oder Ende unterscheiden, Leistung allein reicht dafür nicht immer), Wasserzähler; Blueprints für Benachrichtigungen, Vorwarnung vor dem Ende |
| **0.4 Kosten und Tarife** | Kosten pro Zyklus mit fester oder dynamischer Preis-Entität (Tibber, Nordpool, EPEX); Empfehlung für die günstigste Startzeit: Programmprofil mal Preiskurve; Startzeit nach PV-Prognose (Forecast.Solar, Solcast) |
| **0.5 Wartung und Wasser** | Erinnerungen nach Zyklen, kWh oder Zeit (Flusensieb, Entkalken, Trommelreinigung); Verschleiß erkennen, wenn die Kurve eines Programms über Monate abweicht (z. B. längere Heizphase durch Kalk); Wasserverbrauch pro Zyklus: gemessen mit Wasserzähler oder aus dem Programmprofil geschätzt |
| **später** | Spülmaschine, generischer Gerätetyp, Lovelace-Karte, Diagnose-Export, weitere Sprachen |

## Qualität

- Engine-Tests auf simulierten Kurven: Normalzyklus, Spitzen, Hysterese, Einweichen, Fortsetzen und
  Lernen, Knitterschutz, Ausfall, Neustart, Persistenz, Steckdosen, die nur Änderungen melden.
- Integrationstests mit echtem Home Assistant über `pytest-homeassistant-custom-component`:
  Config-Flow, Options-Flow, ein Zyklus von der Steckdose bis zu Entität, Event und Reload.
- Erkennungstests auf drei synthetischen Programmen mit zufälliger Streuung in Dauer (±10 %) und
  Leistung (±15 %): richtiges Programm, Restzeit, Selbstbenennung, Zurückhalten des Endes,
  unbekanntes Programm, Vorrang der Hand-Auswahl.
- Echte Zyklen aus Diagnose-Dateien liegen anonymisiert (Startzeit 0, ohne Sensornamen) unter
  `tests/fixtures/` und werden in `tests/engine/test_real_curves.py` geprüft: Wiederholungen mit
  ±15–20 % Heizdauer werden erkannt (Restzeit bei Minute 80 auf 3 min genau), fremde Programme nicht
  benannt.
- Als Nächstes: mehr echte Zyklen und eine Messung der Erkennungsgenauigkeit (Leave-one-out), damit
  Verbesserungen belegbar sind.
- CI: Ruff, Tests, `hassfest`, HACS-Validierung.

## Offene Punkte

- Lizenz des Projekts festlegen (z. B. MIT oder Apache-2.0).
- Sammeln echter Leistungskurven der eigenen Geräte für den Testkorpus (per Diagnose-Download).
- Die Schwellen der Erkennung (25 / 50 / 60 %, Distanz 0,8) sind an synthetischen Kurven gewählt
  und müssen an echten Kurven geprüft werden. Erster Befund an echten Waschmaschinen-Läufen (0.2.8):
  Läufe desselben Programms liegen 0,11 bis 0,2 auseinander, „unbekannt“ tritt mit 0,25 an, deshalb
  bleibt die Sicherheit richtiger Erkennungen bei 33 bis 56 %.
- **Vorgemerkt (C): Energie als zweites Merkmal der Erkennung.** Programme, die sich nur in der
  Heizdauer unterscheiden (Pflegeleicht 30 / 60 °C: 0,5–0,7 gegen 1,26 kWh), liegen in der Kurven-
  Distanz eng beieinander. Ein Aufschlag `0,1 · |ln(E_Lauf / E_Referenz)|` senkte im Versuch den
  falschen Treffer von 73 auf 22 %, kostete aber Intensiv 60 °C Sicherheit (33 → 22 %). Erst mit 2 bis 3
  benannten Läufen je Programm einstellen und an echten Kurven testen.
