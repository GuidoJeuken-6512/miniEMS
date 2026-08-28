---
revision_date: 2026-08-27
---

# Sensor-Staleness

Wie miniEMS erkennt, dass ein Sensorwert zu alt ist, um noch als Entscheidungsgrundlage
zu dienen — und warum dafür **kein** einzelner Alters-Schwellwert reicht.

## Mechanik

```
HAStateClient._parse_ts(state)
  → bevorzugt state["last_updated"], sonst state["last_changed"]
  → gespeichert in _state_ts[entity_id]

get_state_age_sec(entity_id)
  → now(UTC) − _state_ts[entity_id]   (None, wenn nie empfangen)

is_stale(entity_id, max_age_sec)
  → age is None  ODER  age > max_age_sec
```

Drei Konstanten decken die drei kontinuierlichen/regelmäßigen Update-Klassen ab (`const.py`):

| Konstante | Wert | Gedacht für |
|---|---|---|
| `SENSOR_MAX_AGE_SEC` | 300 s (5 min) | Leistungssensoren, SoC-Lebenszeichen |
| `FORECAST_MAX_AGE_SEC` | 28 800 s (8 h) | Solcast-Restprognose (`solcast_remaining_today_entity`) |
| `PRICE_MAX_AGE_SEC` | 21 720 s (6 h + 2 min) | Strompreis |

Zwei weitere Konstanten decken Sonderfälle ab, die mit „Alter" allein nicht lösbar sind
(siehe unten):

| Konstante | Wert | Gedacht für |
|---|---|---|
| `DAILY_VALUE_GRACE_SEC` | 900 s (15 min) | Karenz nach lokaler Mitternacht für einmal-täglich geschriebene Werte |
| `SOLCAST_DATA_MAX_AGE_SEC` | 108 000 s (30 h) | Alter der Solcast-**Daten** selbst, nicht der Sensor-Zeitstempel |

`get_state_value(entity_id)` liefert `None` für `unavailable`/`unknown`/`""` und macht
sonst `float(raw)` — für Zeitstempel-Sensoren (ISO-Datum als Zustand) liefert das immer
`None`, selbst wenn der Sensor gesund ist. Dafür existiert `get_state_datetime()`
(`ha_state_client.py:73`), das `raw` als ISO-Zeitstempel parst statt als Zahl.

## Zwei Signale, die *nicht* als Alters-/Lebenszeichen taugen

Bevor man auf „Zeit seit letztem Update" als generische Lösung kommt, lohnt sich der
Blick auf zwei naheliegende Alternativen — beide wurden gegen den echten
Integrationscode geprüft.

### `last_reported` statt `last_updated`

Home Assistant führt drei Zeitstempel pro Entity:

| Feld | rückt vor, wenn … |
|---|---|
| `last_changed` | der **Wert** sich ändert |
| `last_updated` | Wert **oder** Attribute geschrieben werden ← miniEMS nutzt das |
| `last_reported` | die Entity **überhaupt schreibt**, auch wertgleich |

Alle drei rücken nur vor, wenn die Integration `async_write_ha_state()` aufruft. Die
Solcast-Integration tut das für ihre Tagestotale („heute"/„morgen") bewusst **nicht** bei
jedem Zyklus (`custom_components/solcast_solar/sensor.py`, `_handle_coordinator_update`):

```python
if self._update_policy == SensorUpdatePolicy.DEFAULT and not (
        self._coordinator.date_changed or self._coordinator.data_updated):
    return          # ← kein async_write_ha_state()
```

`get_sensor_update_policy()` (gleiche Datei) vergibt `EVERY_TIME_INTERVAL` nur an eine
Handvoll Schlüssel (u. a. `ENTITY_FORECAST_REMAINING_TODAY`, `ENTITY_POWER_NOW`). Alle
Tagestotale und Zeitpunkt-Sensoren („heute"/„morgen", Spitzenleistungs-Zeitpunkte)
bekommen `SensorUpdatePolicy.DEFAULT` — geschrieben nur bei Prognose-Abruf oder
Datumswechsel. Für diese Sensoren sind `last_changed`, `last_updated` und `last_reported`
zu jedem Zeitpunkt identisch; `last_reported` wäre also keine bessere Datenquelle als
`last_updated`.

### `unavailable` als Lebenszeichen

`available` ist eine reine Opt-in-Property jeder Integration — die beiden, von denen
miniEMS abhängt, entscheiden sie gegensätzlich:

**Solarman/Deye** (`custom_components/solarman/entity.py:38`):

```python
def available(self) -> bool:
    return self.coordinator.last_update_success and self.coordinator.device.state.value > -1
```

→ Modbus-Verbindung weg ⇒ `unavailable`. Ein verlässliches, sofortiges Signal — deshalb
reicht für die Wechselrichter-Sensoren eine reine Presence-/Alters-Prüfung.

**Solcast** (`custom_components/solcast_solar/sensor.py:690`):

```python
def available(self) -> bool:
    return self._attr_available      # = (self._sensor_data is not None)
```

`_sensor_data` stammt aus dem auf Platte persistierten Prognose-Cache, nicht aus einem
API-Call. API tot, Kontingent aufgebraucht, Internet weg — der Cache bleibt gefüllt, der
Sensor bleibt `available` und liefert unbegrenzt lange die Prognose von gestern. Für
Solcast ist `unavailable` deshalb **kein** verlässliches Lebenszeichen; das übernimmt die
Datenfrische-Prüfung weiter unten.

Daraus folgen zwei orthogonale Fehlerfälle:

1. **Verbindung tot** → `unavailable`. Für alle Hardware-Sensoren durch
   `get_state_value() is None` abgedeckt.
2. **Verbindung lebt, Daten fachlich veraltet** (Solcast-Prognose von gestern, aus dem
   Cache bedient) → weder `unavailable` noch irgendein Zeitstempel greift hier. Das braucht
   eine semantische Prüfung (Datenfrische, siehe unten).

## Sensor-Klassen nach Update-Verhalten

| Klasse | Beispiel | Reale Kadenz | Prüfung |
|---|---|---|---|
| a) kontinuierlich | Leistungssensoren, Preis-Feeder | Sekunden | `is_stale()` mit `SENSOR_MAX_AGE_SEC` |
| b) fester Schreibtakt, aber wertgetrieben | Solcast „Restprognose heute", „aktuelle Leistung" | 5-min-Takt tagsüber, nachts bis ~6 h ohne Wertänderung | `is_stale()` mit `FORECAST_MAX_AGE_SEC` |
| c) mehrmals/Tag, feste Zeitpunkte | Strompreis (ToU-Tarif) | bis zu 6 h am Stück, Fahrplan fest | `is_stale()` mit `PRICE_MAX_AGE_SEC` |
| d) ereignisgesteuert, ~1×/Tag | Solcast-Tagestotale und -Zeitpunkte („heute"/„morgen", Spitzenleistungs-Zeitpunkte) | nur bei Abruf/Datumswechsel | `is_stale_daily()` — Datums-, keine Alters-Prüfung |
| e) praktisch nie | SoC, Batteriekapazität | Stunden bis Tage, legitim | keine Alters-Prüfung, nur Presence-Check |

Für Klasse (d) gibt es strukturell **kein** Alters-Limit, das richtig sein könnte: Derselbe
Prognosewert ist um 09:00 Uhr taufrisch und um 23:00 Uhr immer noch korrekt — das Alter
des Zeitstempels sagt in beiden Fällen nichts über die Gültigkeit aus. Ein höherer
Schwellwert verschiebt das Problem nur, er löst es nicht.

## Implementierung je Klasse

### a–c: Alters-Prüfung (`is_stale`)

`EMSController._is_stale(entity_id, max_age_sec)` (`ems_controller.py:587`) delegiert an
`HAStateClient.is_stale()`. Verwendet für Leistungssensoren, die Solcast-Restprognose
und den Strompreis.

`PRICE_MAX_AGE_SEC = 21 720` statt eines runden Werts: Der Tarif ist rein zeitgesteuert,
der Fahrplan liegt fest, und das längste Fenster ist exakt 6 h (06–12 Uhr,
`activation_rules`). Bei 21 600 s (exakt 6 h) läge die Schwelle auf einer Punktlandung mit
dem längsten legitimen Fenster; 120 s Marge reichen, um das zu vermeiden, ohne die
Erkennungszeit unnötig zu verschlechtern.

### d: Datums-Prüfung (`is_stale_daily`) statt Alters-Prüfung

`HAStateClient.is_stale_daily(entity_id, grace_sec)` (`ha_state_client.py:122`) fragt
„wurde heute geschrieben?" statt „wie alt ist der Wert?". Das ist für Klasse (d) exakt
beantwortbar, weil die Solcast-Integration bei jedem Datumswechsel garantiert schreibt
(`coordinator.py`):

```python
self.tasks[TASK_LISTENERS] = async_track_utc_time_change(
    self.hass, self._update_integration_listeners, minute=range(0, 60, 5), second=0
)
```

`_update_integration_listeners` läuft alle fünf Minuten und setzt dort
`self._date_changed = current_day != self._last_day` (lokale Zeitzone). Bei
`date_changed` fällt die Early-Return-Bedingung aus dem vorigen Abschnitt weg, und
**alle** `DEFAULT`-Sensoren werden neu geschrieben. Daraus folgt eine harte Zusage:
Solange die Solcast-Integration läuft, trägt jeder Klasse-(d)-Sensor spätestens fünf
Minuten nach lokaler Mitternacht einen Zeitstempel des laufenden Tages. Ein Zeitstempel
von gestern bedeutet zwingend, dass die Integration steht.

Zwei Details der Umsetzung:

- **Lokale Zeitzone.** Solcast rechnet den Datumswechsel in lokaler Zeit
  (`dt.now(self.solcast.options.tz)`), `_parse_ts()` speichert aber UTC. Der Vergleich in
  `is_stale_daily()` läuft deshalb bewusst in lokaler Zeit — unter CEST (UTC+2) läge
  lokale Mitternacht sonst zwei Stunden lang auf dem falschen UTC-Tag.
- **Karenz `DAILY_VALUE_GRACE_SEC` (900 s).** Deckt die Minuten zwischen 00:00 und 00:05
  lokal ab, in denen Solcast noch nicht neu geschrieben hat.

`EMSController._is_stale_daily(entity_id)` (`ems_controller.py:669`) kapselt den Aufruf
mit der Konstante. Verwendet an drei Stellen:

- `_should_grid_charge()` (`ems_controller.py:543`) — Tomorrow-Fallback: Wenn die
  Restprognose für heute erschöpft/veraltet ist, prüft der Controller, ob morgens
  Prognose allein den Akku füllen würde, bevor er in den Dunkelfenster-Fallback geht.
  Stale wird hier als „nicht verwendbar" behandelt, nicht als Fehler — der Pfad fällt
  einfach auf das Dunkelfenster zurück.
- Warnbanner (`_build_sensor_warnings()`, `ems_controller.py:768`) — für
  `solcast_today_entity` und `solcast_tomorrow_entity`.
- Warnbanner, zweiter Block (`ems_controller.py:784`) — für
  `solcast_peak_time_today_entity` und `solcast_peak_time_tomorrow_entity`. Diese beiden
  Sensoren tragen einen Zeitstempel als Zustand (Uhrzeit der erwarteten
  Spitzenleistung), nicht eine Zahl — die Presence-Prüfung läuft deshalb über
  `get_state_datetime()`, nicht über `get_state_value()` (das würde `float(raw)` auf
  einem ISO-String versuchen und immer `None` liefern).

### Solcast-Datenfrische — unabhängig von jedem HA-Zeitstempel

Weder die Alters- noch die Datums-Prüfung erkennt den Fall, dass die Solcast-**API**
unerreichbar ist (Kontingent aufgebraucht, Internet weg, Dienst gestört), während der
Plattencache unverändert weiterliefert:

| Signal | Verhalten in diesem Fall | erkennt den Fehler? |
|---|---|---|
| `unavailable` | Cache gefüllt → Sensor bleibt `available` | ❌ |
| `last_updated` / `last_reported` | rücken vor (Werte werden aus Cache + Uhrzeit fortgeschrieben) | ❌ |
| Datums-Prüfung (`is_stale_daily`) | Datumswechsel schreibt garantiert → Zeitstempel ist von *heute* | ❌ |
| Alters-Prüfung auf der Restprognose | Kurve läuft aus Cache + Uhr weiter → Alter bleibt im Normalbereich | ❌ |

Der Ausweg: nicht den Schreibzeitstempel eines abgeleiteten Sensors prüfen, sondern den
**Wert** von `solcast_last_fetch_entity`
(`sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf`) — explizit sein Inhalt, nicht
sein eigener `last_updated`. Der Wert stammt aus dem persistierten Datensatz der
Integration und ist damit exakt das Alter der Prognosedaten, unabhängig davon, wie oft HA
die Sensoren neu schreibt (`solcastapi.py`, `SolcastApi.last_updated`).

```python
def _solcast_data_age_sec(self) -> float | None:      # ems_controller.py:644
    fetched = self._ws.get_state_datetime(cfg.solcast_last_fetch_entity)
    return (now_utc - fetched).total_seconds() if fetched else None

def _solcast_data_stale(self) -> bool:                  # ems_controller.py:658
    age = self._solcast_data_age_sec()
    return age is not None and age > SOLCAST_DATA_MAX_AGE_SEC
```

`SOLCAST_DATA_MAX_AGE_SEC = 30 h` ist gemessen, nicht geraten: Auf der Produktivanlage an
fünf Tagen in Folge 6/6/6/5/4 erfolgreiche Abrufe pro Tag, längste legitime Nachtlücke
15,5 h (12:44 UTC → 04:11 UTC). Kürzere Wintertage schätzungsweise ~19 h; 30 h lässt Marge
und erkennt eine stehende API dennoch binnen gut eines Tages.

Ohne konfigurierte `solcast_last_fetch_entity` liefert die Prüfung `None`/`False` — sie
entfällt, statt Fehlalarm zu schlagen.

Zwei Wirkstellen:

- `_forecast_remaining_kwh()` (`ems_controller.py:571`) liefert `None`, sobald die
  Solcast-Daten veraltet sind — damit beruht keine Regelentscheidung
  (`_should_hold_pv_charge`, `_should_grid_charge`) auf tagealten Prognosedaten.
- Warnbanner: meldet das Alter in Stunden, wenn die Schwelle überschritten ist.

!!! note "Warum nicht der eigene Zeitstempel von `solcast_last_fetch_entity`?"
    Naheliegend, aber falsch: Der `last_changed` dieser Entity selbst ist genauso alt wie
    die Prognosesensoren, die sie eigentlich bewachen soll — live geprüft identisch mit
    dem Zeitstempel von `prognose_heute` zum gleichen Zeitpunkt. Als Lebenszeichen taugt
    also nur ihr **Wert**, nicht ihr eigener Schreibzeitpunkt.

### e: nur Presence, keine Alters-Prüfung

`battery_soc_entity` hat seit v2.0.2 keine Alters-Prüfung mehr, nur einen
Presence-Check (`get_state_value(...) is None`). Ein Alters-Timeout wäre für eine grobe
Prozentzahl, die legitim stundenlang — im Winter bei ausgeschaltetem Netzladen sogar
tagelang — exakt gleich bleiben kann, irgendwann für jeden denkbaren Wert falsch. Die
Alters-Prüfung auf `battery_power_entity` (derselbe BMS-Anschluss, schwankt kontinuierlich,
solange die Verbindung lebt) übernimmt implizit die Lebenszeichen-Funktion, siehe
[Berechnungen](calculations.md).

## Jede Aufrufstelle im Projekt

| # | Ort | Geprüfte Entity | Konstante/Mechanismus |
|---|---|---|---|
| 1 | `ems_controller.py:399` (`_decide`, SoC-Lebenszeichen) | `battery_power_entity` | `is_stale()`, `sensor_max_age_sec` |
| 2 | `ems_controller.py:399` (`_decide`, PV-Überschuss-Vorbedingung) | `pv_power_entity`, `load_power_entity` | `is_stale()`, `sensor_max_age_sec` |
| 3 | `ems_controller.py:515` (`_should_grid_charge`) + Warnbanner | `electricity_price_entity` | `is_stale()`, `price_max_age_sec` (21 720 s) |
| 4 | `ems_controller.py:571` (`_forecast_remaining_kwh`) | `solcast_remaining_today_entity` | `is_stale()`, `forecast_max_age_sec` **+** `_solcast_data_stale()` |
| 5 | `ems_controller.py:543` (`_should_grid_charge`, Tomorrow-Fallback) | `solcast_tomorrow_entity` | `is_stale_daily()` |
| 6 | `ems_controller.py:768` (Warnbanner) | `solcast_today_entity`, `solcast_tomorrow_entity` | `is_stale_daily()` |
| 7 | Generische `required`-Liste (Warnbanner) | `pv_power`, `battery_power`, `grid_power`, `load_power` | `is_stale()`, `sensor_max_age_sec` |
| 8 | `ems_controller.py:784` (Warnbanner) | `solcast_peak_time_today_entity`, `solcast_peak_time_tomorrow_entity` | `is_stale_daily()`, Presence über `get_state_datetime()` |
| 9 | Warnbanner, global | `solcast_last_fetch_entity` (Wert, nicht Zeitstempel) | `_solcast_data_stale()`, `SOLCAST_DATA_MAX_AGE_SEC` (30 h) |

Zeilen 8/9 fließen aktuell in keine EMS-Entscheidung ein — die beiden
Spitzenleistungs-Zeitpunkte werden gepollt und im Warnbanner auf Aktualität geprüft, sind
aber (Stand dieser Seite) für ein noch nicht umgesetztes Feature vorbereitet.

## Bewusst nicht umgesetzt: Plausibilitätsprüfung des Kurvenverlaufs

Eine Alternative zur Datums-Prüfung wäre gewesen, `solcast_remaining_today_kwh` auf
seinen erwarteten Tagesverlauf zu prüfen (Reset um Mitternacht, Nachtplateau auf dem
Tagesmaximum, danach monoton fallend, abends exakt null) und einen Bruch dieses Musters
als eingefrorenen/fehlerhaften Sensor zu werten. Gemessen über zwei volle Tage
(HA-Recorder, 356 Datenpunkte) bestätigte sich dieses Muster, mit drei Feinheiten
gegenüber der naheliegenden Formulierung „nachts null, sonst fallend": der Reset liegt auf
lokaler Mitternacht (nicht am Morgen), nur das *abendliche* Dunkelfenster ist null (das
Morgen-Fenster steht bis Sonnenaufgang auf dem Tagesmaximum), und Monotonie gilt nur
zwischen Prognose-Abrufen.

Verworfen, weil die Prüfung nach Einführung der Solcast-Datenfrische (`_solcast_data_stale()`
oben) keine Lücke mehr schließt: Der Kurvenverlauf entsteht aus dem gecachten Spline plus
der Uhrzeit — bei tagelang toter API bleiben Reset, Nachtplateau, monotone Abnahme und
Abendnull sämtlich intakt, jedes Kriterium der Prüfung würde bestehen. Ihr einziger Gewinn
wäre reine Erkennungsgeschwindigkeit (Minuten statt Stunden) für einen eingefrorenen
Sensor — bei einem seltenen Fehlerfall, gegen drei zusätzliche Fehlalarmquellen
(Mitternachts-Reset statt Morgen, Nachtplateau-Ausnahme, Toleranz für untertägige
Revisionen). Das Verhältnis trägt nicht.

## Verwandte Seiten

- [Berechnungen](calculations.md) — wo die Staleness-Prüfungen in die
  Modus-Entscheidung einfließen (fail-closed bei fehlendem/veraltetem Sensor).
- [Energiefahrplan — netzdienliches Laden](../roadmap/energiefahrplan.md) — der geplante Zugriff auf
  Entity-Attribute (`activation_rules` des Tarifsensors) würde eine Plausibilitätsprüfung
  gegen den echten Tarif-Fahrplan erlauben, die heute mangels Attribut-Zugriff nicht
  existiert.
- [Tageswechsel & Energiezählung](../roadmap/tageswechsel-energiezaehlung.md) — die
  Datums-Prüfung (`is_stale_daily`) setzt voraus, dass miniEMS' eigener Tagesschnitt
  zuverlässig auf lokaler Mitternacht liegt; dort gemessen und bestätigt
  (`TZ=Europe/Berlin`, Schnitt beobachtet bei `21:59:38 → 22:00:08 UTC`).
