# Bug-Analyse miniEMS

- **Stand:** 19.10.2026
- **Geprüfter Code:** `main` @ `aca939f` (Add-on und eingebettete Integration 2.0.4)
- **Anlass:** Kompatibilität mit Home Assistant Core 2026.10 und den für 2026.11 angekündigten Änderungen, inkl. Suche nach veralteten (deprecated) Funktionen
- **Hinweis:** Die Analyse hat keinen Code geändert. Pfade beziehen sich auf `miniems/rootfs/usr/bin/`.

## Ergebnis

**HA 2026.10 macht miniEMS nicht kaputt.** Die eingebettete Integration (`integration/`) wurde in einer Testumgebung mit `homeassistant==2026.10.2` geladen. Die Add-on-API (`/api/status`) wurde dabei durch einen lokalen Server ersetzt. Getestet wurden:

- Einrichtung über den Config-Flow
- alle 34 Sensoren (verfügbar)
- Options-Flow mit anschließendem Neuladen
- Entladen

Alle Befunde unten treten **mit HA 2026.8.1 genauso auf**, sind also nicht durch 2026.10 entstanden. Sie bestehen schon länger und stehen hier, weil der Test sie sichtbar gemacht hat.

### Abgleich mit dem Changelog 2026.10 / Dev-Blog

| Thema | Betroffen? |
|---|---|
| Breaking Changes 2026.10 (Authentifizierung, State-Conditions, einzelne Integrationen) | nein |
| REST-API, die das Add-on nutzt (`/api/states`, `/api/services/…`, `/api/template`, `weather.get_forecasts`, `homeassistant.reload_config_entry`) | nein, keine Änderungen |
| `device_registry.async_get_device` deprecated, `DeviceEntry.config_entries` wird durchgesetzt | nein, nicht genutzt |
| Probatio ersetzt `voluptuous` (seit 2026.9) | nein, Config- und Options-Flow laufen; `import voluptuous as vol` bleibt gültig |
| `RestoreStateData.last_states` entfällt (2026.11) | nein, nicht genutzt |
| Climate / Water Heater `native_…`-Umbenennung (2026.11) | nein, nur Sensoren |
| hassfest prüft `requirements` von Custom-Integrationen | nein, `requirements` ist leer |

## Befunde

| # | Befund | Ort | Kritikalität | Aufwand |
|---|---|---|---|---|
| M1 | HTTP-Session wird doppelt angelegt, die erste bleibt offen („Unclosed client session“ bei jedem Start und Neuladen) | `integration/__init__.py:33`, `integration/coordinator.py:34` | 🟠 mittel | S |
| M2 | Unzulässige Kombination aus Zustandsklasse und Geräteklasse bei 22 Sensoren (44 Warnungen pro Start) | `integration/sensor.py` | 🟠 mittel | S–M (Statistik-Migration beim Anwender) |
| M3 | Coordinator bekommt `config_entry` nicht übergeben | `integration/coordinator.py:24` | 🟢 niedrig | S |
| M4 | Eigene `aiohttp.ClientSession` statt der von HA | `integration/coordinator.py:36`, `:52`, `integration/config_flow.py:27` | 🟢 niedrig | S |

### M1 – Doppelte HTTP-Session (🟠 mittel)

`async_setup_entry` ruft `await coordinator._async_setup()` selbst auf (`__init__.py:33`). `async_config_entry_first_refresh()` ruft `_async_setup()` danach **noch einmal** auf; das macht der `DataUpdateCoordinator` von HA so. Dabei entsteht eine zweite `aiohttp.ClientSession`. Die erste wird nie geschlossen.

- **Folge:** Fehlermeldung „Unclosed client session“ im HA-Log bei jedem Start und jedem Neuladen (z. B. nach Änderung der Optionen); jedes Mal bleibt eine Verbindung offen.
- **Änderung:** Zeile 33 in `__init__.py` streichen. Besser zusammen mit M4: Dann entfällt `_async_setup` ganz.
- **Test:** Integration laden und neu laden, im Log darf kein „Unclosed client session“ stehen.

### M2 – Zustandsklasse passt nicht zur Geräteklasse (🟠 mittel)

HA meldet bei jedem Start pro Sensor: „is using state class '…' which is impossible considering device class '…'“. HA fordert für diese Sensoren eine andere Zustandsklasse; Langzeitstatistiken für sie sind deshalb nicht verlässlich.

| Sensoren (Key) | Ist | Zulässig | Vorschlag |
|---|---|---|---|
| `today_grid_cost_eur`, `today_pv_savings_eur`, `today_load_cost_eur`, `today_feed_in_revenue_eur`, `today_cost_without_grid_charge`, `today_cost_fix_price_tariff`, `today_grid_charge_cost_bilanz_eur` | `MONETARY` + `TOTAL_INCREASING` | `MONETARY` erlaubt nur `TOTAL` oder keine | `TOTAL` **mit `last_reset`** = Tagesbeginn. Ohne `last_reset` wertet HA den täglichen Rücksprung auf 0 als negativen Betrag |
| `week_*_eur`, `month_*_eur`, `year_*_eur` (Netzkosten, PV-Ersparnis, Lastkosten) | `MONETARY` + `MEASUREMENT` | `TOTAL` oder keine | `TOTAL` mit `last_reset` = Beginn von Woche/Monat/Jahr |
| `today_grid_charge_roi_eur`, `today_base_price_eur` | `MONETARY` + `MEASUREMENT` | `TOTAL` oder keine | keine Zustandsklasse, falls keine Statistik gebraucht wird; sonst `TOTAL` |
| `predicted_load_kwh`, `predicted_pv_kwh` | `ENERGY` + `MEASUREMENT` | `TOTAL`, `TOTAL_INCREASING` oder keine | Zustandsklasse entfernen (Prognose, kein Zähler) |
| `battery_kwh_freetochange`, `battery_kwh_useable`, `battery_capacity_kwh` | `ENERGY` + `MEASUREMENT` | wie oben | Geräteklasse `ENERGY_STORAGE` (erlaubt `MEASUREMENT`), Zustandsklasse bleibt |

- **Achtung beim Umstellen:** Ändert sich die Zustandsklasse eines Sensors mit vorhandener Statistik, zeigt HA unter **Einstellungen → Reparaturen** eine Meldung (Zustandsklasse bzw. Mittelwert-Typ geändert). Das gehört in die Release Notes, mit dem Hinweis, wie die Reparatur zu bestätigen ist.
- **Test:** Integration laden, im Log darf keine „impossible state class“-Warnung mehr stehen.

### M3 – `config_entry` nicht an den Coordinator übergeben (🟢 niedrig)

`MiniEMSCoordinator.__init__` ruft `super().__init__(hass, _LOGGER, name=…, …)` ohne `config_entry=`. HA holt den Eintrag dann aus dem Kontext; für Custom-Integrationen ist das laut HA (Stand 2026.10) nicht zur Abschaffung geplant und erzeugt keine Warnung. Sauberer ist, den Eintrag zu übergeben:

```python
coordinator = MiniEMSCoordinator(hass, entry, base_url, poll_interval)
# im Coordinator:
super().__init__(hass, _LOGGER, config_entry=entry, name="miniEMS", …)
```

### M4 – Eigene HTTP-Session statt der von HA (🟢 niedrig)

Coordinator (`coordinator.py:36`, `:52`) und Config-Flow (`config_flow.py:27`) legen eigene `aiohttp.ClientSession`s an. Empfohlen ist `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)`. Die Session gehört HA und muss nicht geschlossen werden. Damit entfallen `_async_setup`, das Schließen in `async_shutdown` und M1. Das Timeout wird pro Anfrage gesetzt (`session.get(url, timeout=aiohttp.ClientTimeout(connect=5, total=10))`).

## Empfohlene Reihenfolge

1. **M1 + M4 zusammen:** HA-Session verwenden, `_async_setup` und den manuellen Aufruf entfernen.
2. **M2:** Zustands- und Geräteklassen korrigieren, mit Hinweis zu den Statistik-Reparaturen in den Release Notes.
3. **M3:** bei Gelegenheit.

## Prüfumgebung

- Python 3.14, `homeassistant==2026.10.2`, `pytest-homeassistant-custom-component==0.13.373`
- Gegenprobe mit `homeassistant==2026.8.1`: identische Warnungen und Fehler
- Test: Config-Flow → 34 Sensoren verfügbar → Options-Flow (Neuladen) → Entladen; Log ab Stufe WARNING ausgewertet
