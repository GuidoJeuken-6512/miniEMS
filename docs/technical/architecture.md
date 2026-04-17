---
revision_date: 2026-04-07
---

# Architektur

## Komponentenübersicht

```
┌───────────────────────────────────────────────────────────────────┐
│                         miniEMS Add-on                            │
│                                                                   │
│  ┌─────────────┐    ┌───────────────────────────────────────────┐ │
│  │HAWebSocket  │    │         EMS Loop (30 s tick)              │ │
│  │Client       │───▶│  EMSController                            │ │
│  │(REST poll)  │    │  + CostOptimizer  + SensorValidator       │ │
│  └─────────────┘    │  + ConsumptionModel + BatteryModel        │ │
│                     │  + SolcastClient  + EventLog              │ │
│  ┌──────────────┐   └──────────────┬────────────────────────────┘ │
│  │WeatherClient │──────────────────┘                              │
│  │(HA forecast  │   weather.get_forecasts (täglich, 30 min Cache) │
│  │ action API)  │                                                 │
│  └──────────────┘                                                 │
│                          status_store {}                          │
│              ┌────────────────────────────────┐                  │
│              ▼                                ▼                  │
│  ┌───────────────────┐       ┌──────────────────────────────┐   │
│  │MQTTPublisher      │       │  FastAPI / Uvicorn           │   │
│  │(Discovery + data) │       │  Ingress Dashboard           │   │
│  │                   │       │  /  /settings  /log          │   │
│  │HASensorPublisher  │       │  /config-json /options-json  │   │
│  │(REST fallback)    │       │  /database                   │   │
│  │                   │       │  (de/en i18n via YAML)       │   │
│  └────────┬──────────┘       └──────────────────────────────┘   │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
   HA Core API  (http://hassio/homeassistant/api)
   sensor.miniems_* entities
```

## Asyncio-Task-Graph

Drei lang laufende Tasks werden nebenläufig ausgeführt:

```
asyncio.gather(
  ws_client.run()      # ruft HA-Zustände alle 15 s ab
  _ems_task()          # wartet auf ready → führt EMS-Loop alle 30 s aus
  uvi_server.serve()   # FastAPI / Uvicorn HTTP-Server auf Port 8080
)
```

`_ems_task` wartet auf `ws_client.wait_ready()` (ein `asyncio.Event`), bevor
er startet. Dadurch wird verhindert, dass der EMS mit veralteten oder leeren
Zustandsdaten läuft.

## Subsystem-Verdrahtung (pro Tick)

```
EMSController.update()
  │
  ├─ SensorValidator.validate()     # Leistungs-Spikes ablehnen
  ├─ BatteryModel.free_to_charge()  # kWh-Headroom-Berechnung
  ├─ BatteryModel.useable()         # kWh Entladekapazität
  ├─ SolcastClient.remaining_today  # verbleibende Solcast-kWh (oder None)
  ├─ ConsumptionModel.predict()     # predicted_load_kwh + Quellbezeichnung
  ├─ _determine_mode()              # EMS-Moduslogik
  ├─ InverterController.apply_mode()# Befehle senden (oder [SIM] loggen)
  ├─ CostOptimizer.record_tick()    # Energie-/Kosten-Akkumulatoren
  ├─ EventLog.append()              # bei Moduswechsel ODER Preisänderung
  └─ return status_store {}         # an web_server + Publisher weitergegeben
```

## Authentifizierungsablauf

```
SUPERVISOR_TOKEN  ──▶  http://hassio/homeassistant/api
       │ 401?
       ▼
long_lived_token  ──▶  http://hassio/homeassistant/api
       │ 401?
       ▼
    Fehler loggen, in 10 s erneut versuchen
```

Sowohl `HAWebSocketClient` (Lesezugriffe) als auch `HASensorPublisher`
(Schreibzugriffe) implementieren diesen Fallback unabhängig voneinander,
sodass jeder zur Laufzeit den Token wechseln kann.

`SUPERVISOR_TOKEN` wird außerdem verwendet von:

- `WeatherClient` — um `weather.get_forecasts` aufzurufen und den HA-Breitengrad abzurufen
- `web_server.py` — um `http://supervisor/core/api/config` nach der HA-Sprache
  (de/en Auto-Detection) abzufragen

## Sensor-Veröffentlichung: MQTT vs. REST

miniEMS veröffentlicht Sensoren auf zwei Wegen mit automatischem Fallback:

| Methode | Wann aktiv | Sensoren |
|---|---|---|
| **MQTT Discovery** | Wenn ein MQTT-Broker erreichbar ist | Alle 28 nativen Sensoren unter dem *miniEMS*-Gerät |
| **HA REST API** | Immer (Fallback wenn MQTT nicht verfügbar) | Dieselben Sensoren per `POST /api/states/sensor.miniems_*` |

MQTT-Sensoren unterstützen Langzeit-Statistiken in HA (`state_class: total_increasing` / `measurement`).

## Sensor-Validierung (SensorValidator)

Leistungswerte werden bei jedem Tick validiert, um unplausible Spikes abzulehnen:

```
ablehnen wenn: |aktuell − letzter_akzeptierter_Wert| > 500 W  AND  |Δ| / letzter_akzeptierter_Wert > 50%
```

Abgelehnte Werte geben `None` zurück; der EMS überspringt den Tick für diesen Sensor.
Jede Entity wird unabhängig verfolgt.

## Ausfallzeiten-Erkennung

Beim Start liest `CostOptimizer` `last_flush_ts` aus der SQLite-Tabelle `daily_stats`.
Wenn die Lücke zwischen `last_flush_ts` und `datetime.now(timezone.utc)` mehr als zwei
Update-Intervalle überschreitet, wird eine Datenlücken-Warnung ausgelöst und im
Warnungsbanner des Dashboards angezeigt.

## Internationalisierung (i18n)

Bei jeder Seitenanfrage fragt `web_server.py` `http://supervisor/core/api/config`
ab, um die HA-Sprache (`language`-Feld) zu ermitteln. Die entsprechende YAML-Datei
(`translations/de.yaml` oder `translations/en.yaml`) wird geladen und sowohl in die
Jinja2-Templates als auch in das JavaScript-Objekt `const T` injiziert, sodass auch
dynamisch gerenderte Karten übersetzt werden.

Fallback-Reihenfolge: HA Supervisor API → `Accept-Language`-Header → Englisch.
