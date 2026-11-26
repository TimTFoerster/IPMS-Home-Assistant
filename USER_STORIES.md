# User Stories — Projekt C: ISYGLT IPMS Home Assistant Integration

Diese User Stories definieren die schrittweise Umsetzung der Home Assistant Custom Integration `custom_components/isyglt_ipms`. Jede Story ist in sich abgeschlossen, testbar und baut auf den vorherigen Stories auf.

## Epic 1: Fundament, HTTP-Client & Coordinator

### US-01: Integrations-Grundgerüst & Manifest-Loader

**Als** Entwickler/Integrator
**möchte ich** ein sauberes Grundgerüst unter `custom_components/isyglt_ipms` mit Schema-Validierung für `anlage.yaml` / `anlage.json`,
**damit** Konfigurationsfehler beim Laden oder Neuladen der Integration sofort klar gemeldet werden.

**Akzeptanzkriterien:**

* `manifest.json` definiert `domain: "isyglt_ipms"`, `iot_class: "local_polling"`, `config_flow: true` und `requirements: ["pyyaml"]`.

* `const.py` enthält alle Konstanten für Plattformen (`light`, `switch`, `cover`, `button`, `number`, `climate`, `sensor`, `binary_sensor`), `light`-Modes (`bit`, `dimmer`, `dimmer_tw`, `rgb`, `rgbw`), numerische Modes (`byte`, `signed_byte`, `word`, `signed_word`) sowie Standardwerte (`scan_interval: 1.0`, `pulse_ms: 150`).

* Ein Loader-Modul (`manifest_loader.py`) lädt YAML- oder JSON-Manifeste asynchron (`hass.async_add_executor_job`), prüft Pflichtfelder (`schema_version`, `master`, `entities`) und wirft bei ungültigen `platform`/`mode`-Kombinationen aussagekräftige `ConfigEntryError`-Meldungen.

### US-02: Asynchroner IPMS HTTP-Client (`api.py`)

**Als** Integration
**möchte ich** über `aiohttp` asynchron gegen `/ipms/jsonpooldata` und `/ipms/ajaxcmdproxy?$MCRQC$=1` kommunizieren,
**damit** Zustände gebündelt gelesen und Schalt-/Dimm-Befehle verzögerungsfrei gesendet werden können.

**Akzeptanzkriterien:**

* `async_read_pool(markers: list[tuple[str, str]]) -> dict` sendet `POST /ipms/jsonpooldata` mit `reqDataSet` (`masterMarker`, `valueType` `BIT`|`BYTE`, `length: 1`), parst `dataItems` in ein Dictionary `{marker: raw_value}` und liefert zusätzlich `poolStatus` und `masterCycleSet` zurück.

* `async_send_command(cmd: str, marker: str, value: int)` sendet `POST /ipms/ajaxcmdproxy` mit Query-Parameter `{"$MCRQC$": "1"}` und form-encoded Body (`CMD`, `ID`, `MARKER`, `VALUE`).

* Prüft im Response-Header `$PROXY_RESPONSE$ == "OK"` und löst bei HTTP-Fehlern, Timeouts oder `COMM_ERROR` eine spezifische Exception (`IsygltConnectionError` / `IsygltCommandError`) aus.

* `async_pulse_bit(marker: str, pulse_ms: int)` setzt ein Bit (`SET_BIT_SWITCH` mit `VALUE=1`), wartet `pulse_ms / 1000` Sekunden und löscht es wieder (`CLR_BIT_SWITCH` mit `VALUE=1`).

### US-03: Zentraler `DataUpdateCoordinator` mit Sofort-Refresh & Zyklus-Watchdog (`coordinator.py`)

**Als** Home-Assistant-Nutzer
**möchte ich**, dass alle Merker in einem einzigen HTTP-Request gepollt werden und nach jedem Schalt-/Dimm-Befehl sofort aktualisiert werden,
**damit** die UI ohne spürbare Verzögerung reagiert und der Server nicht durch Einzelabfragen belastet wird.

**Akzeptanzkriterien:**

* Sammelt beim Initialisieren alle benötigten Marker aus `entities` (inkl. optionaler `enable_marker` und `fault_marker`) dedupliziert ein.

* Pollt im konfigurierbaren `scan_interval` (Standard `1.0s`) alle Marker in einem einzigen Batch-Request.

* Stellt Hilfsmethoden `get_bit(marker) -> bool | None` und `get_byte(marker) -> int | None` bereit.

* Nach jedem Schreibbefehl (`async_write_bit`, `async_write_byte`, `async_write_word`, `async_pulse`) wird sofort `await self.async_request_refresh()` ausgeführt.

* Überwacht `masterCycleSet` für den konfigurierten Master-Alias (`IPM1` → `IPM001`); bleibt der Zykluszähler über mehrere Polls hinweg unverändert oder schlägt der Request fehl, wird `master_online = False` gesetzt.

## Epic 2: UI-Einrichtung, Mehr-Master-Betrieb & Areas

### US-04: Config Flow, Reload & Mehr-Master-Unterstützung (`config_flow.py`, `__init__.py`)

**Als** Installateur/Nutzer
**möchte ich** die Integration über das Home-Assistant-UI einrichten, mehrere IP-Master anlegen und Änderungen an der Manifest-Datei per Klick auf „Neu laden“ übernehmen können,
**damit** kein manueller Eintrag in `configuration.yaml` erforderlich ist.

**Akzeptanzkriterien:**

* Config Flow fragt Protokoll (Default `http://`), Host/IP, Port (Default `8080`), API-Pfad (Default `/ipms`), `master_alias`, `manifest_path` (Default `isyglt/`, relativ oder absolut innerhalb von `hass.config.config_dir`) und optional `scan_interval` / `pulse_ms` ab.

* Validiert beim Einrichten die Erreichbarkeit von `/ipms/jsonpooldata` sowie die Lesbarkeit der Manifest-Datei.

* Mehrere Config Entries (z. B. `IPM1`, `IPM2`) können dieselbe IPMS-Server-Adresse verwenden und laufen über `unique_id` auf Basis der normalisierten `ipms_url` + `master_alias` kollisionsfrei. Jeder Eintrag pollt nur Marker seines Masters; Areas mit gleichem Namen werden gemeinsam genutzt.

* Verbindungsdaten und Manifestpfad sind nachträglich über den Options Flow änderbar; Änderungen validieren Verbindung und Manifest und laden den Config Entry neu.

* Unterstützt `async_reload_entry`, sodass nach einer Änderung durch Projekt A ein Klick auf **Neu laden** alle Plattformen sauber aktualisiert.

### US-05: Automatische Home Assistant Areas & Basis-Entität (`entity.py`)

**Als** Nutzer
**möchte ich**, dass Räume aus `locations` automatisch als Bereiche (Areas) in Home Assistant angelegt und alle Entitäten sowie `EnableMarker`/`FaultMarker` einheitlich behandelt werden,
**damit** die Anlage sofort nach Räumen strukturiert ist und Sperr-/Störzustände konsistent greifen.

**Akzeptanzkriterien:**

* Beim Setup werden alle Einträge aus `locations` über `area_registry` geprüft und falls nicht vorhanden angelegt; Entitäten werden über `device_registry` / `entity_registry` ihrem `location_id`-Bereich zugeordnet.

* Eine gemeinsame Basisklasse `IsygltEntity(CoordinatorEntity)` kapselt `unique_id`, `DeviceInfo` pro Raum/Master und die Auswertung von `enable_marker` und `fault_marker`.

* Ist `coordinator.last_update_success == False`, `enable_marker == False` (`0`) oder `fault_marker == True` (`1`), liefert `available` `False` (`unavailable` in HA) und `extra_state_attributes` zeigt `lock_reason` (`"disabled"` | `"fault"` | `"offline"`).

## Epic 3: Entitäts-Plattformen

### US-06: Licht-Plattform (`light.py`) mit allen 5 Modes

**Als** Nutzer
**möchte ich** Schaltlichter, Byte-Dimmer, Tunable-White-Leuchten sowie RGB- und RGBW-Stripes steuern können,
**damit** alle Beleuchtungskreise des PD-Programms nativ in Home Assistant (inkl. Lichtgruppen und HA-Szenen) funktionieren.

**Akzeptanzkriterien:**

* **`mode: "bit"`:** `ColorMode.ONOFF`; liest `state_marker` (`BIT`), schaltet über `SET_BIT_SWITCH` / `CLR_BIT_SWITCH` auf `command_marker`.

* **`mode: "dimmer"`:** `ColorMode.BRIGHTNESS`; liest `brightness_marker` (`BYTE` `0..255`). `is_on` ist `value > 0`. `turn_off` schreibt `0` per `WRITE_BYTE` und merkt sich die letzte Helligkeit `> 0`; `turn_on` ohne `brightness` stellt den gemerkten Wert (bzw. `default_brightness`, Standard `255`) wieder her.

* **`mode: "dimmer_tw"`:** `ColorMode.COLOR_TEMP`; liest/schreibt `brightness_marker` (`0..255`) und `color_temp_marker` (`0..255`, linear skaliert zwischen `min_kelvin` und `max_kelvin`).

* **`mode: "rgb"`:** `ColorMode.RGB`; liest/schreibt `red_marker`, `green_marker`, `blue_marker` (`0..255`). Wenn `brightness_marker` gesetzt ist, wird dieser separat geschrieben; andernfalls werden R/G/B proportional zur Helligkeit skaliert.

* **`mode: "rgbw"`:** `ColorMode.RGBW`; liest/schreibt `red_marker`, `green_marker`, `blue_marker`, `white_marker` (`0..255`) sowie optional `brightness_marker`.

### US-07: Schaltbare Verbraucher (`switch.py`), Impuls-Buttons (`button.py`) & Einstellungsmerker (`number.py`)

**Als** Nutzer
**möchte ich** Steckdosen schalten, globale Impulsbefehle wie „Zentral Aus“ auslösen und Parameter-/Einstellungsmerker (z. B. Nachlaufzeiten wie `M101` oder `SM`-Werte) anpassen können,
**damit** Verbraucher, Zentralfunktionen und Anlagenparameter direkt über Home Assistant bedienbar sind.

**Akzeptanzkriterien:**

* `switch.py` unterstützt `device_class` (z. B. `outlet`, `switch`), liest `state_marker` (`BIT`) und sendet `SET_BIT_SWITCH` / `CLR_BIT_SWITCH` auf `command_marker`.

* `button.py` erzeugt native HA-Buttons ohne langen Tastendruck; `async_press` löst `coordinator.async_pulse(command_marker, pulse_ms)` aus (Entity-`pulse_ms` überschreibt optional globales `master.pulse_ms`).

* `number.py` erzeugt `NumberEntity`-Entitäten (Standard `EntityCategory.CONFIG`, `NumberMode.BOX` oder `SLIDER`), liest den aktuellen Wert (`mode`: `byte` [Default] | `signed_byte` | `word` | `signed_word`, optional mit `scale`/`offset`) und schreibt Änderungen per `WRITE_BYTE` bzw. `WRITE_WORD` innerhalb von `min_value`..`max_value` (mit `step` und `unit_of_measurement`).

### US-08: Rollladen, Markisen, Leinwände & Raffstores (`cover.py`)

**Als** Nutzer
**möchte ich** Rollladen, Markisen und Leinwände über kurze Impulse für die PD-Selbsthaltung fahren und stoppen sowie bei Raffstores die Lamellen schrittweise verstellen können,
**damit** die Verriegelungs- und Laufzeitlogik im PD-Programm unverändert genutzt wird und Raffstore-Lamellen intuitiv kippbar sind.

**Akzeptanzkriterien:**

* Nutzt ausschließlich die englischen Manifest-Felder `up_marker`, `down_marker` sowie optional `moving_up_marker`, `moving_down_marker`, `tilt_step_ms` und `device_class` (`shutter`, `blind`, `awning`, `shade`).

* `async_open_cover` sendet einen Impuls (`pulse_ms`) auf `up_marker` und merkt sich `"up"` als letzte Fahrtrichtung.

* `async_close_cover` sendet einen Impuls (`pulse_ms`) auf `down_marker` und merkt sich `"down"` als letzte Fahrtrichtung.

* `async_stop_cover` prüft `moving_up_marker` / `moving_down_marker` (falls vorhanden) oder die zuletzt ausgelöste Fahrtrichtung und sendet erneut einen Impuls auf den entsprechenden Richtungs-Merker (falls keine Richtung bekannt ist, auf `up_marker`).

* Ist `tilt_step_ms` konfiguriert, registriert das `cover` die Tilt-Features `CoverEntityFeature.OPEN_TILT` und `CoverEntityFeature.CLOSE_TILT` (ohne `SET_TILT_POSITION` / ohne Slider): `async_open_cover_tilt` bzw. `async_close_cover_tilt` sendet einen Start-Impuls auf `up_marker` bzw. `down_marker`, wartet `tilt_step_ms` und sendet sofort einen zweiten Stopp-Impuls auf denselben Merker. Ein Lock pro Entität verhindert überlappende Tipp-Sequenzen bei schnellem Mehrfachklick.

* `is_opening` und `is_closing` werden aus `moving_up_marker` / `moving_down_marker` abgeleitet, falls konfiguriert.

### US-09: Raumregler (`climate.py`), Sensoren (`sensor.py`) & Binärsensoren (`binary_sensor.py`)

**Als** Nutzer
**möchte ich** Raumtemperaturen regeln, Wetter-/Lux-/Messwerte anzeigen und Bewegungsmelder, Rauchmelder sowie den Master-Verbindungsstatus überwachen können,
**damit** alle Informationen der ISYGLT-Anlage in Home Assistant vorliegen.

**Akzeptanzkriterien:**

* **`climate.py`:** Liest `current_temp_marker` und `target_temp_marker` (konfigurierbarer `temperature_scale`, Standard `1.0` °C pro Byte-Schritt ohne Offset), schreibt Solltemperatur per `WRITE_BYTE` auf `target_temp_marker`, bildet `comfort_marker` als Komfort-/Normal-Preset und `valve_marker` als HVAC-Aktion ab.

* **`sensor.py`:** Unterstützt `mode: "byte"` (`0..255`), `mode: "signed_byte"` (`-128..127`, Zweierkomplement für Außentemperatur), `mode: "word"` (`0..65535`, z. B. Luxwerte) und `mode: "signed_word"` (`-32768..32767`, 16-Bit-Zweierkomplement), jeweils als einzelner 16-Bit-Merker oder kombiniert aus `high_marker` + `low_marker`, inklusive `device_class`, `unit_of_measurement` und optionalem `scale` / `offset`.

* **`binary_sensor.py`:** Liest `state_marker` (`BIT`) mit `device_class` (`motion`, `smoke`, `door`, `window`, `garage_door`, `problem`).

* **Globaler Master-Diagnosesensor:** Pro Config Entry wird automatisch genau ein `binary_sensor` (`EntityCategory.DIAGNOSTIC`, `device_class: BinarySensorDeviceClass.CONNECTIVITY`) erzeugt, der den Verbindungs- und Zyklusstatus des IP-Masters (`coordinator.master_online`) anzeigt.

## Epic 4: Qualitätssicherung & Beispielkonfiguration

### US-10: Automatisierte Tests & Beispiel-Manifest

**Als** Entwickler
**möchte ich** Unit-Tests für API-Client, Coordinator, Byte-Dimmer-Logik, Cover-Stopp- & Lamellen-Tipp-Logik, `number`-Parameter sowie Sensor-Dekodierung (`signed_byte`/`word`/`signed_word`) haben,
**damit** Änderungen an der Integration regressionsfrei geprüft werden können.

**Akzeptanzkriterien:**

* `pytest`-Tests decken `ajaxcmdproxy?$MCRQC$=1`-URL-Aufbau, `jsonpooldata`-Parsing, `dimmer`-Last-Brightness-Memory, `enable_marker`/`fault_marker` → `unavailable`, `cover`-Stopp-Impuls, `cover`-Lamellen-Doppelimpuls (`tilt_step_ms`), `number`-Lesen/Schreiben sowie `signed_byte`/`word`/`signed_word`-Umrechnung ab.

* Ein validiertes Beispiel-Manifest (`examples/anlage.example.yaml`) liegt im Repository bereit.