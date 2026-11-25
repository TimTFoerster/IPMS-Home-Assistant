# ISYGLT IPMS Home Assistant Integration (`custom_components/isyglt_ipms`)

Diese Home-Assistant-Integration (**Projekt C**) bindet ISYGLT-Gebäudeautomationsanlagen direkt über die lokale HTTP-API des ISYGLT IPMS-Servers (Jetty, Standard-Port `8080`) an – **ohne Modbus TCP**. Sie ist vollständig wiederverwendbar, mehr-master-fähig und wird primär über ein deklaratives YAML/JSON-Anlagenmanifest (erzeugt durch **Projekt A**) konfiguriert.

---
## Installation & Einrichtung

### 1. Integration installieren
* **Über HACS (empfohlen):**
  1. Öffne in Home Assistant **HACS → Integrationen → Drei-Punkte-Menü (oben rechts) → Benutzerdefinierte Repositories**.
  2. Füge die URL dieses GitHub-Repositories mit der Kategorie **Integration** hinzu und installiere **ISYGLT IPMS**.
  3. Starte Home Assistant neu.
* **Manuell:**
  1. Kopiere den Ordner `custom_components/isyglt_ipms` in das Verzeichnis `config/custom_components/` deiner Home-Assistant-Installation.
  2. Starte Home Assistant neu.

### 2. Anlagen-Manifest ablegen
1. Erstelle oder generiere die Manifest-Datei (z. B. `anlage.yaml` oder `anlage.json`) mit allen Räumen (`locations`) und Entitäten (`entities`).
2. Lege die Datei im Home-Assistant-Konfigurationsverzeichnis ab, z. B. unter `/config/isyglt/anlage.yaml`.

### 3. Integration in Home Assistant hinzufügen
1. Gehe zu **Einstellungen → Geräte & Dienste → Integration hinzufügen** und suche nach **ISYGLT IPMS**.
2. Gib folgende Daten ein:
   * **IPMS-URL:** z. B. `http://192.168.178.71:8080/ipms`
   * **Master-Alias:** z. B. `IPM1`
   * **Pfad zur Manifest-Datei:** z. B. `/config/isyglt/anlage.yaml`
3. Nach dem Speichern werden alle Bereiche (**Areas**) aus `locations` sowie sämtliche Entitäten automatisch angelegt.

*(Hinweis: Bei mehreren IP-Mastern auf einem oder mehreren IPMS-Servern kannst du die Integration einfach mehrfach über **Integration hinzufügen** anlegen.)*

---

## Nutzung & Bedienung

* **Änderungen am Manifest übernehmen:** Wenn du `anlage.yaml` angepasst oder neu generiert hast, klicke unter **Einstellungen → Geräte & Dienste → ISYGLT IPMS** auf das **Drei-Punkte-Menü → Neu laden**. Ein Neustart von Home Assistant ist nicht erforderlich.
* **Lichter & Dimmer (`light`):**
  * **Schaltlichter (`bit`):** Ein- und Ausschalten über Bit-Merker.
  * **Dimmer (`dimmer`, `dimmer_tw`, `rgb`, `rgbw`):** Helligkeit (`0–255`) und Farben werden direkt als Byte-Wert geschrieben; beim Ausschalten (`0`) merkt sich Home Assistant die vorherige Helligkeit für das nächste Einschalten.
  * **Gemeinsames Dimmen („Gesamt Heller/Dunkler“):** Fasse mehrere Dimmer über **Einstellungen → Geräte & Dienste → Helfer → Gruppe → Lichtgruppe** zusammen, um sie gemeinsam über einen Slider zu regeln.
* **Rollladen, Markisen & Leinwände (`cover`):**
  * **Auf / Ab / Stopp:** Sendet einen kurzen Tastimpuls (`pulse_ms`), die Selbsthaltung übernimmt das ISYGLT-Programm (`ROLLO`). Ein erneutes Drücken in dieselbe Richtung stoppt den Antrieb.
  * **Lamellenverstellung (Jalousien):** Ist `tilt_step_ms` konfiguriert, stehen in Home Assistant zusätzliche Tilt-Buttons bereit, die einen Start- und nach `tilt_step_ms` einen Stopp-Impuls senden.
* **Zentral-Aus & Taster-Impulse (`button`):** Löst beim Klick einen kurzen Tastimpuls (`pulse_ms`) auf den konfigurierten Merker aus.
* **Szenen:** Verwende die nativen **Home Assistant Szenen** (*Einstellungen → Automationen & Szenen → Szenen*). Um Szenen über physische ISYGLT-Wandtaster auszulösen, binde den Taster-Merker als `binary_sensor` ein und starte die Szene per Automation.
* **Einstellungsmerker (`number`) & Heizung (`climate`):** Nachlaufzeiten, Schwellwerte oder Raumregler-Sollwerte lassen sich direkt über Slider bzw. das Thermostat-Widget anpassen.
* **Sperre, Störung & Verbindung (`enable_marker`, `fault_marker`, `NetworkError`):**
  * Ist ein `enable_marker` auf `0` (gesperrt) oder ein `fault_marker` auf `1` (Störung), wird die betroffene Entität in Home Assistant automatisch als `nicht verfügbar` markiert und der Grund in den Entitäts-Attributen angezeigt.
  * Der globale Diagnose-Sensor `binary_sensor.<master>_verbindung` meldet Verbindungs- oder Zyklusprobleme des IP-Masters.

---


## 1. Architektur & Systemkontext

* **Projekt A (Generator / Parser):** Liest Exporte aus dem ISYGLT ProgrammDesigner (`_Adressen.txt`, `_Aktoren.txt`, `_Sensoren.txt` und Programm-TXT), vergibt freie Merker (`M`, `SM`, `NE`, `NA`) und erzeugt das Anlagenmanifest (`anlage.yaml` / `anlage.json`).
* **Projekt B (Web-App):** Separates Web-Frontend für gewerbliche/öffentliche Bauten.
* **Projekt C (Dieses Repository):** Home Assistant Custom Integration (`local_polling`), ideal für Wohnhäuser und private Anlagen.

```text
[Projekt A: anlage.yaml]
          │
          ▼
[custom_components/isyglt_ipms]
  ├── Config Flow (Host, Port, Master-Alias, Pfad zur anlage.yaml)
  ├── Area Sync (legt Gebäude/Stockwerk/Raum als HA-Areas an)
  ├── DataUpdateCoordinator (1x Batch-Poll /ipms/jsonpooldata + Sofort-Refresh)
  └── Plattformen: light, switch, cover, button, number, climate, sensor, binary_sensor
          │
          ▼
[ISYGLT IPMS HTTP API (Port 8080)]
```

---

## 2. IPMS HTTP-API Kurzreferenz

### A. Zustände gebündelt auslesen: `POST /ipms/jsonpooldata`
* **Header:** `Content-Type: application/json`
* **Request-Body:**
  ```json
  {
    "reqDataSet": [
      { "masterMarker": "IPM1:NA64.1", "valueType": "BIT", "length": 1 },
      { "masterMarker": "IPM1:M56", "valueType": "BYTE", "length": 1 }
    ]
  }
  ```
* **Response-Body:**
  ```json
  {
    "poolStatus": "OK",
    "id": "0",
    "dataItems": [
      { "m": "IPM1:NA64.1", "v": "1", "av": null },
      { "m": "IPM1:M56", "v": "180", "av": null }
    ],
    "masterCycleSet": { "IPM001": 31766 }
  }
  ```

### B. Befehle senden: `POST /ipms/ajaxcmdproxy?$MCRQC$=1`
* **Wichtig:** Der Query-Parameter `?$MCRQC$=1` in der URL ist zwingend. Fehlt er, antwortet Jetty still mit HTTP `200` und `Content-Length: 0`.
* **Header:** `Content-Type: application/x-www-form-urlencoded`
* **Body-Parameter:** `CMD`, `ID` (laufende Ganzzahl), `MARKER` (z. B. `IPM1:NA64.1` oder `IPM1:M56`), `VALUE` (Pflicht-Ganzzahl, z. B. `1` bei Bit-Befehlen oder `0..255` bei `WRITE_BYTE`).
* **Unterstützte Kommandos:**
  * `SET_BIT_SWITCH` (`VALUE=1`) — Bit setzen / einschalten
  * `CLR_BIT_SWITCH` (`VALUE=1`) — Bit löschen / ausschalten
  * `SET_BIT_PUSHBUTTON` (`VALUE=1`) / `CLR_BIT_PUSHBUTTON` (`VALUE=1`) — Taster-Impuls
  * `WRITE_BYTE` (`VALUE=0..255`) — Byte-Wert schreiben (Dimmer, TW, RGB/RGBW, Parameter/Einstellungen)
  * `WRITE_WORD` (`VALUE=0..65535`) — 16-Bit-Wert schreiben (für `word` / `signed_word` Einstellungen)
* **Response-Header:** `$PROXY_RESPONSE$: OK` und `$MASTER_RESPONSE$: 1,4,1,0,36589`.

---

## 3. Vollständiges Manifest-Beispiel (`anlage.yaml`)

```yaml
schema_version: 1
master:
  alias: "IPM1"
  ipms_url: "http://192.168.178.71:8080/ipms"
  scan_interval: 1.0
  pulse_ms: 150

locations:
  - id: "wohnhaus_og_bad"
    name: "OG Bad"
    building: "Wohnhaus"
    floor: "OG"
    room: "Bad"
  - id: "wohnhaus_eg_wz"
    name: "EG Wohnzimmer"
    building: "Wohnhaus"
    floor: "EG"
    room: "Wohnzimmer"

entities:
  # 1. Schaltlicht (Bit)
  - id: "og_bad_sk4"
    name: "Bad Schaltkreis 4"
    platform: "light"
    mode: "bit"
    location_id: "wohnhaus_og_bad"
    state_marker: "IPM1:NA64.1"
    command_marker: "IPM1:NA64.1"

  # 2. Byte-Dimmer (reines Byte 0..255, kein Schaltbit nötig)
  - id: "og_bad_spiegel"
    name: "Bad Spiegel"
    platform: "light"
    mode: "dimmer"
    location_id: "wohnhaus_og_bad"
    brightness_marker: "IPM1:M56"
    default_brightness: 255
    enable_marker: "IPM1:NE64.1"       # optional: 0 = unavailable (gesperrt)
    fault_marker: "IPM1:NE64.2"        # optional: 1 = unavailable (Störung)

  # 3. Tunable White (Helligkeit + Farbtemperatur als Byte)
  - id: "eg_wz_tw"
    name: "Wohnzimmer Decke TW"
    platform: "light"
    mode: "dimmer_tw"
    location_id: "wohnhaus_eg_wz"
    brightness_marker: "IPM1:M32"
    color_temp_marker: "IPM1:M33"
    min_kelvin: 2700
    max_kelvin: 6500

  # 4. RGB / RGBW Licht
  - id: "eg_wz_rgbw"
    name: "Wohnzimmer Voute RGBW"
    platform: "light"
    mode: "rgbw"
    location_id: "wohnhaus_eg_wz"
    red_marker: "IPM1:M34"
    green_marker: "IPM1:M35"
    blue_marker: "IPM1:M36"
    white_marker: "IPM1:M37"

  # 5. Steckdose / Verbraucher (Bit, kein mode nötig)
  - id: "og_bad_whirlpool"
    name: "Steckdose Whirlpool"
    platform: "switch"
    device_class: "outlet"
    location_id: "wohnhaus_og_bad"
    state_marker: "IPM1:M49.2"
    command_marker: "IPM1:M49.2"

  # 6. Cover (Rollladen, Markise, Leinwand, Raffstore mit Lamellen-Tippbetrieb)
  - id: "eg_wz_leinwand"
    name: "Leinwand"
    platform: "cover"
    device_class: "shade"
    location_id: "wohnhaus_eg_wz"
    up_marker: "IPM1:NE73.2"
    down_marker: "IPM1:NE73.1"
    moving_up_marker: "IPM1:NA73.2"    # optional
    moving_down_marker: "IPM1:NA73.1"  # optional

  - id: "eg_wz_raffstore"
    name: "Raffstore Süd"
    platform: "cover"
    device_class: "blind"
    location_id: "wohnhaus_eg_wz"
    up_marker: "IPM1:NE74.1"
    down_marker: "IPM1:NE74.2"
    tilt_step_ms: 300                  # optional: aktiviert Lamellen-Tippbetrieb (Start-Impuls -> 300ms -> Stopp-Impuls)

  # 7. Impuls-Button (Zentral-Aus, Tor-Taster)
  - id: "eg_zentral_aus"
    name: "Zentral Aus Wohn/Ess"
    platform: "button"
    location_id: "wohnhaus_eg_wz"
    command_marker: "IPM1:NE95.1"

  # 8. Einstellungsmerker / Parameter (Number: z. B. Nachlaufzeit BWM, Dämmerungsschwelle)
  - id: "og_bad_bwm_nachlauf"
    name: "Nachlaufzeit BWM Bad"
    platform: "number"
    mode: "byte"                       # optional: byte (Default) | word | signed_byte | signed_word
    entity_category: "config"
    location_id: "wohnhaus_og_bad"
    marker: "IPM1:M101"
    min_value: 1
    max_value: 120
    step: 1
    unit_of_measurement: "min"

  # 9. Raumregler (Climate)
  - id: "eg_wz_heizung"
    name: "Heizung Wohnzimmer"
    platform: "climate"
    location_id: "wohnhaus_eg_wz"
    current_temp_marker: "IPM1:M96"
    target_temp_marker: "IPM1:M92"
    comfort_marker: "IPM1:NE1.1"
    valve_marker: "IPM1:NA1.5"

  # 10. Sensor & Binärsensor
  - id: "aussen_temp"
    name: "Außentemperatur"
    platform: "sensor"
    mode: "signed_byte"                # byte (Default) | signed_byte | word | signed_word
    device_class: "temperature"
    unit_of_measurement: "°C"
    location_id: "wohnhaus_eg_wz"
    state_marker: "IPM1:NE31"

  - id: "aussen_lux"
    name: "Helligkeit Nord"
    platform: "sensor"
    mode: "word"
    device_class: "illuminance"
    unit_of_measurement: "lx"
    location_id: "wohnhaus_eg_wz"
    high_marker: "IPM1:NE50"
    low_marker: "IPM1:NE51"

  - id: "eg_wz_bwm"
    name: "Bewegungsmelder Wohnzimmer"
    platform: "binary_sensor"
    device_class: "motion"
    location_id: "wohnhaus_eg_wz"
    state_marker: "IPM1:M198.7"
```

---

## 4. Design-Leitlinien für die Implementierung

1. **Kein langer Tastendruck / kein Hold:** Szenen werden über native Home-Assistant-Szenen gelöst, gemeinsames Heller/Dunkler über native HA-Lichtgruppen (`light` Group).
2. **Reine Byte-Dimmer:** Dimmer benötigen im PD-Programm kein zusätzliches Rückmelde- oder Schaltbit. Ausschalten schreibt `0` (`WRITE_BYTE`), Einschalten ohne Helligkeitsangabe stellt den letzten gemerkten Wert > 0 (oder `default_brightness`) wieder her.
3. **Cover-Logik & Lamellen-Tippbetrieb:** `up_marker` und `down_marker` senden nur kurze Impulse (`pulse_ms`), weil das PD-Programm die Selbsthaltung übernimmt. Ein erneuter Impuls auf die aktive Richtung stoppt den Motor. Ist `tilt_step_ms` gesetzt, unterstützen die Tilt-Buttons (`open_cover_tilt` / `close_cover_tilt`) das schrittweise Verstellen der Lamellen über einen Start-Impuls gefolgt von einem Stopp-Impuls nach `tilt_step_ms` (ohne Positionsslider).
4. **Einstellungsmerker (`number`):** Werte wie Nachlaufzeiten (`M101`, `SM...`) oder Schwellwerte werden über `number`-Entitäten lesbar und schreibbar (`WRITE_BYTE` / `WRITE_WORD`), unabhängig davon, ob die Logik auf dem IP-Master oder in Home Assistant läuft.
5. **Enable- & Fault-Marker:** Werden intern ausgewertet (`enable_marker == 0` oder `fault_marker == 1` → Entität wird `unavailable` mit Diagnose-Attribut).
6. **NetworkError:** Wird als globaler Diagnose-`binary_sensor` pro IP-Master abgebildet.

## 5. Beispielmanifest & Tests

Ein vollständiges Beispielmanifest mit allen unterstützten Plattformen liegt unter
[examples/anlage.example.yaml](examples/anlage.example.yaml). Die Integrationstests
können mit `pip install -r requirements-test.txt` und anschließend
`python -m pytest -q` ausgeführt werden.