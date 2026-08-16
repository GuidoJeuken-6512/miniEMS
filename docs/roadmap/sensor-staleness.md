---
revision_date: 2026-08-15
---

# Sensor-Staleness — Ist-Zustand und Verbesserungsvorschläge

!!! info "Status: Bestandsaufnahme + Vorschläge, teilweise umgesetzt"
    Diese Seite beschreibt den Ist-Zustand der Staleness-Erkennung und fünf Vorschläge.
    Alle Messwerte stammen vom 13.–16.08.2026 aus der Produktivanlage. Verwandt:
    [Tageswechsel & Energiezählung](tageswechsel-energiezaehlung.md) — siehe
    [Bezug](#bezug-zur-tageswechsel-roadmap) am Ende dieser Seite.

    | Vorschlag | Stand |
    |---|---|
    | 1 — Datums-Prüfung für Tagesprognosen | **umgesetzt in v2.0.4** |
    | 2 — `unavailable` für Hardware | bereits vorhanden, nichts zu tun |
    | 3 — Preis-Marge 6 h + 2 min | **umgesetzt in v2.0.4** |
    | 4 — Datenfrische von Solcast | **umgesetzt in v2.0.4** |
    | 5 — Plausibilitätsprüfung des Kurvenverlaufs | **bewusst verworfen** (Begründung bei Vorschlag 5) |

!!! info "Anlass"
    Live beobachteter Fehlalarm: Das Dashboard meldet `solcast_pv_forecast_prognose_heute`
    als „stale", unabhängig davon, wie lange der Wert wirklich alt ist. Live nachgemessen
    (Produktivanlage, 18:14 Uhr UTC): `last_updated = 08:31:25` → Alter **9 h 43 min**,
    während die geprüfte Schwelle (`forecast_max_age_sec`, 8 h) das bereits als veraltet
    einstuft. Die Ursache ist inzwischen **im Quellcode der Solcast-Integration belegt**
    (siehe unten) — es ist kein Konfigurationsproblem, sondern ein Denkfehler in der
    Prüfmethode.

Das hier ist der **zweite** Fund dieser Fehlerklasse in diesem Projekt. Der erste
(`battery_soc_entity`, behoben in v2.0.2) ist der Präzedenzfall für die
Verbesserungsvorschläge unten.

## Mechanik (kurz)

```
HAWebSocketClient._parse_ts(state)
  → bevorzugt state["last_updated"], sonst state["last_changed"]
  → gespeichert in _state_ts[entity_id]

get_state_age_sec(entity_id)
  → now(UTC) − _state_ts[entity_id]   (None, wenn nie empfangen)

is_stale(entity_id, max_age_sec)
  → age is None  ODER  age > max_age_sec
```

Drei Konstanten decken **alle** Staleness-Prüfungen im Projekt ab (`const.py`):

| Konstante | Wert (aktuell) | Gedacht für |
|---|---|---|
| `SENSOR_MAX_AGE_SEC` | 300 s (5 min) | Leistungssensoren, SoC-Lebenszeichen |
| `FORECAST_MAX_AGE_SEC` | 28 800 s (8 h, seit v2.0.1) | Solcast-Sensoren |
| `PRICE_MAX_AGE_SEC` | 21 600 s (6 h, seit v2.0.1) | Strompreis |

**Totes Feature:** `get_state_value(entity_id, max_age_sec=None)` hat einen optionalen
Staleness-Parameter, der im gesamten Code an **keiner einzigen** Aufrufstelle mit einem
Wert übergeben wird (geprüft: `grep -n "get_state_value(" *.py`). Jede tatsächliche
Staleness-Prüfung läuft über die separaten, expliziten `is_stale()`-Aufrufe unten.

## Zwei naheliegende Abkürzungen — und warum nur eine trägt

Bevor es an die Zahlen geht: Zwei Ideen liegen nahe, wenn man das Problem sieht. Beide
wurden gegen den echten Integrationscode auf der Produktivanlage geprüft. Eine davon
trägt nicht, die andere nur zur Hälfte.

### Idee A: „Nimm doch `last_reported` statt `last_updated`"

Home Assistant führt **drei** Zeitstempel pro Entity:

| Feld | rückt vor, wenn … |
|---|---|
| `last_changed` | der **Wert** sich ändert |
| `last_updated` | Wert **oder Attribute** geschrieben werden ← miniEMS nutzt das |
| `last_reported` | die Entity **überhaupt schreibt**, auch wertgleich |

`last_reported` ist im REST-JSON tatsächlich enthalten (live verifiziert), der Wechsel
wäre also trivial. **Er bringt hier trotzdem nichts.** Alle drei Felder rücken nur vor,
wenn die Integration `async_write_ha_state()` *aufruft*. Sie messen „wann hat die
Integration zuletzt geschrieben" — nicht „wann hatte sie zuletzt Kontakt zur Quelle".

Und die Solcast-Integration schreibt bei den Tagesprognosen bewusst **nicht**
(`custom_components/solcast_solar/sensor.py`, `_handle_coordinator_update`):

```python
if self._update_policy == SensorUpdatePolicy.DEFAULT and not (
        self._coordinator.date_changed or self._coordinator.data_updated):
    return          # ← kein async_write_ha_state()
```

`get_sensor_update_policy()` (gleiche Datei) vergibt `EVERY_TIME_INTERVAL` nur an neun
Schlüssel — darunter `ENTITY_FORECAST_REMAINING_TODAY` und `ENTITY_POWER_NOW`. Alles
übrige, inklusive der Tagestotale „heute" und „morgen", bekommt `DEFAULT`.

Messung auf der Produktivanlage (18:14 UTC), alle Sensoren aus **derselben Integration
und demselben Coordinator**:

| Sensor | `last_changed` = `last_updated` = `last_reported` | Alter | Policy |
|---|---|---|---|
| `…_aktuelle_leistung` | 18:10:00 | 4 min | `EVERY_TIME_INTERVAL` |
| `…_prognose_verbleibende_leistung_heute` | 18:10:00 | 4 min | `EVERY_TIME_INTERVAL` |
| `…_prognose_heute` | **08:31:25** | **9 h 43 min** | `DEFAULT` |
| `…_prognose_morgen` | 08:31:25 | 9 h 43 min | `DEFAULT` |

Die Integration ist beweisbar quicklebendig — sie schreibt alle fünf Minuten. Nur der
Tagesgesamtwert wird nicht neu geschrieben, weil sich fachlich nichts geändert hat. Bei
`prognose_heute` sind alle drei Zeitstempel **identisch**: `last_reported` würde den
Fehlalarm eins zu eins reproduzieren. Dasselbe gilt für den Preissensor
(`sensor.deye8k_current_electricity_price`: `last_changed` = `last_reported` = 16:00:00).

### Idee B: „Wird der Sensor nicht einfach `unavailable`, wenn die Quelle fehlt?"

`available` ist in HA eine reine Opt-in-Property jeder Integration. Die beiden
Integrationen, von denen miniEMS abhängt, entscheiden sie **gegensätzlich**:

**Solarman/Deye** (`custom_components/solarman/entity.py:38`) — der Wechselrichter:

```python
def available(self) -> bool:
    return self.coordinator.last_update_success and self.coordinator.device.state.value > -1
```

→ **Ja.** Modbus-Verbindung weg ⇒ `unavailable`. Ein verlässliches, sofortiges Signal.

**Solcast** (`custom_components/solcast_solar/sensor.py:690`):

```python
def available(self) -> bool:
    return self._attr_available      # = (self._sensor_data is not None)
```

`_sensor_data` stammt aus dem **auf Platte persistierten Prognose-Cache**, nicht aus
einem API-Call. API tot, Kontingent aufgebraucht, Internet weg — der Cache bleibt
gefüllt, der Sensor bleibt `available` und liefert unbegrenzt lange die Prognose von
gestern. → **Nein, wird nie `unavailable`.**

Der `unavailable`-Teil ist in miniEMS bereits umgesetzt: `get_state_value()` liefert
`None` für `unavailable`/`unknown`/`""` (`ha_ws_client.py:67`), und
`_build_sensor_warnings` macht daraus „Sensor unavailable". Für die Deye-Sensoren ist
das damit **das** tragfähige Signal — die Altersprüfung dort ist im Wesentlichen
Redundanz.

### Fazit der beiden Ideen

Es sind **zwei orthogonale Fehlerfälle**, die die heutige Altersprüfung zusammenwirft:

1. **Verbindung tot** → `unavailable`. Bereits gelöst, verlässlich für alle Hardware-Sensoren.
2. **Verbindung lebt, Daten fachlich veraltet** (Solcast-Prognose von gestern) → weder
   `unavailable` noch irgendein Zeitstempel greift. Hier hilft nur eine **semantische**
   Prüfung.

## Ist-Zustand — jede Aufrufstelle im Projekt

| # | Ort | Geprüfte Entity | Konstante | Reale Update-Kadenz (live + im Integrationscode belegt) | Urteil | Lösung vorhanden |
|---|---|---|---|---|---|---|
| 1 | `ems_controller.py:358` (`_decide`, SoC-Lebenszeichen) | `battery_power_entity` | `sensor_max_age_sec` (300 s) | kontinuierlich, ~15-s-Takt | ✅ passend (v2.0.2-Fix — Referenzfall) | ✅ |
| 2 | `ems_controller.py:376-377` (`_decide`, PV-Überschuss-Vorbedingung) | `pv_power_entity`, `load_power_entity` | `sensor_max_age_sec` (300 s) | kontinuierlich, ~15-s-Takt | ✅ passend | ✅ |
| 3 | `ems_controller.py:446` + Warnbanner | `electricity_price_entity` | `price_max_age_sec` (**21 720 s / 6 h + 2 min, seit v2.0.4**) | zeitgesteuerter Fahrplan, ändert sich nur an Tarifstufengrenzen; längstes Fenster **exakt 6 h** (06–12 Uhr, `activation_rules`) | ✅ behoben — vorher Punktlandung (Schwelle == längstes Fenster) | ✅ umgesetzt |
| 4 | `ems_controller.py:482` (`_forecast_remaining_kwh`) | `solcast_remaining_today_entity` | `forecast_max_age_sec` (28 800 s / 8 h) | Policy `EVERY_TIME_INTERVAL` (5-min-Schreibtakt), aber **Wertänderung** nur, solange die Kurve läuft: nachts legitim **5 h 50 min** still (zwei Nächte in Folge gemessen) | ❌ blind für den realen Fehlerfall: Der Wert wird aus Cache + Uhrzeit fortgeschrieben und bleibt auch bei tagelang toter API vollständig plausibel | ⚠️ Vorschlag 5 erkennt den Integrationsausfall in Minuten statt Stunden — Datenfrische bleibt offen |
| 5 | `ems_controller.py:460` (`_should_grid_charge`, Tomorrow-Fallback) | `solcast_tomorrow_entity` | `forecast_max_age_sec` (28 800 s / 8 h) | **nur bei Prognose-Abruf oder Datumswechsel** — Policy `DEFAULT` | ✅ behoben — Datums-Prüfung statt `forecast_max_age_sec` (v2.0.4); der Netzlade-Fix aus v2.0.1 greift damit auch nachts wieder | ⚠️ Integrationsausfall gelöst, Datenfrische bleibt offen (Vorschlag 4) |
| 6 | `ems_controller.py:564` (Warnbanner) | `solcast_today_entity` | `forecast_max_age_sec` (28 800 s / 8 h) | **nur bei Prognose-Abruf oder Datumswechsel** — Policy `DEFAULT`; live als Ursache des Fehlalarms bestätigt (9 h 43 min) | ✅ behoben — Datums-Prüfung statt `forecast_max_age_sec` (v2.0.4) | ⚠️ Integrationsausfall gelöst, Datenfrische bleibt offen (Vorschlag 4) |
| 7 | Generische `required`-Liste (Warnbanner) | `pv_power`, `battery_power`, `grid_power`, `load_power` | `sensor_max_age_sec` (300 s) | kontinuierlich | ✅ passend | ✅ |

Legende der letzten Spalte: ✅ = beide Fehlerarten (Verbindung tot / Daten veraltet)
abgedeckt · ⚠️ = nur eine der beiden · ❌ = keine wirksame Erkennung.

`battery_soc_entity` selbst hat seit v2.0.2 **keine** Alters-Prüfung mehr — nur noch einen
Presence-Check (`get_state_value(...) is None`). Das ist der bereits gelöste Fall dieser
Fehlerklasse.

Bemerkenswert: #4 und #6 stammen aus **derselben Integration** und teilen sich dieselbe
Konstante, obwohl ihre Kadenzen nichts miteinander zu tun haben — für #4 passt sie
(5 h 50 min gemessene Stillstandsphase gegen 8 h Schwelle), für #6 ist sie zu knapp.
Dass ein und derselbe Wert für zwei Sensoren derselben Integration einmal passt und
einmal Fehlalarm erzeugt, zeigt deutlich genug, dass „Alter" hier nicht die tragende
Messgröße sein kann.

!!! danger "Zwei Korrekturen gegenüber früheren Fassungen dieser Seite"
    **(1)** #4 war zunächst als „✅ passend, mit großer Marge" eingestuft. Das verdeckt,
    dass die Prüfung den eigentlichen Fehlerfall nicht sehen kann. #4 ist der einzige
    Solcast-Wert, der direkt in `_should_grid_charge` einfließt.

    **(2)** Danach stand hier, die Prüfung könne „strukturell nie auslösen, weil der
    Sensor alle 5 Minuten neu geschrieben wird". Auch das war falsch. Der 5-Minuten-Takt
    ist der *Schreib*takt; `last_updated` rückt aber nur bei einer **Wertänderung** vor.
    Gemessen über zwei Nächte steht der Wert legitim **5 h 50 min** still
    (22:00→03:50 UTC, beide Nächte identisch) — die 8-h-Konstante hat also rund 2 h
    Marge und ist für den Integrationsausfall durchaus wirksam, nur sehr langsam. Blind
    ist sie ausschließlich für veraltete *Daten*.

!!! note "Wie schwer wiegt #5 wirklich? — Erreichbarkeit des Tomorrow-Zweigs"
    Der betroffene Zweig in `_should_grid_charge` wird nur erreicht, wenn **gleichzeitig**
    gilt: Billigtarif **und** (`remaining` fehlt **oder** `remaining ≤ 1,0 kWh`). Beides
    zusammen ist seltener, als es zunächst wirkt:

    - **Sommer, gesunder Prognosesensor:** `remaining ≤ 1,0 kWh` tritt erst ab ca. 20:15
      Uhr lokal ein (gemessen), die Billigfenster liegen bei 02–06 und 12–16 Uhr. Die
      Bedingungen überlappen sich **nie** — der Zweig ist unerreichbar, der Fehler
      folgenlos.
    - **Winter:** Um 02–06 Uhr steht `remaining` auf der *Tages*prognose des neuen Tages
      (Reset um lokale Mitternacht). An einem trüben Dezembertag kann die unter 1,0 kWh
      liegen ⇒ Zweig erreicht, und 02–06 liegt im Dunkelfenster (21–06) ⇒ es wird
      geladen. `prognose_morgen` ist dann garantiert stale (letzter Abruf am Vortag,
      ~17,5 h), die Prüfung „morgen füllt den Akku ohnehin" entfällt. **Hier kostet der
      Fehler Geld — also genau dann, wenn Netzladen überhaupt relevant ist.**
    - **Ausfall des Prognosesensors:** `remaining is None` erfüllt die Bedingung ebenfalls,
      und zwar zu **jeder** Tageszeit. Fällt Solcast während eines Billigfensters aus, ist
      der Zweig auch im Sommer erreichbar.

    Im Billigfenster 12–16 Uhr greift der Dunkelfenster-Test korrekt und verhindert das
    Laden, unabhängig vom Prognosezustand.

!!! success "Verwandter Befund außerhalb der Staleness-Logik — behoben in v2.0.4"
    `_should_grid_charge` besitzt **keine Hysterese** — die Rückgabe in
    `ems_controller.py:475` ist ein nackter Schwellwertvergleich
    (`bat_kwh_free > remaining × 1,2 + 1,0`). Einziger Dämpfer ist die Verweildauer
    `mode_dwell_sec` (300 s) in `_commit()`. Der PV-Pfad hat mit
    `pv_charge_hysteresis_frac` dagegen eine asymmetrische Hysterese, die die gemessenen
    Prognosesprünge (+0,35 / +0,13 kWh, siehe Vorschlag 5) vollständig schluckt. Da sich
    `remaining` tagsüber um bis zu 0,47 kWh je 5-Minuten-Intervall bewegt, kann ein Akku
    nahe der Schwelle im Netzlade-Pfad pendeln. Kein Staleness-Problem, aber dieselbe
    Ursache — sprunghafte Prognosedaten — und deshalb hier vermerkt.

## Root Cause

Drei Konstanten decken faktisch **fünf** unterschiedliche Update-Kadenz-Klassen ab:

| Klasse | Beispiel | Reale Kadenz | Passende Konstante? |
|---|---|---|---|
| a) kontinuierlich | Leistungssensoren | Sekunden | `SENSOR_MAX_AGE_SEC` — ja |
| b) fester Schreibtakt, aber wertgetrieben | Solcast „remaining", „aktuelle Leistung" | 5-min-Takt tagsüber, nachts bis 5 h 50 min ohne Wertänderung | `FORECAST_MAX_AGE_SEC` — ja, ~2 h Marge |
| c) mehrmals/Tag, feste Zeitpunkte | Strompreis (ToU-Tarif) | exakt bis 6 h am Stück, Fahrplan fest | `PRICE_MAX_AGE_SEC` — richtige Größenordnung, nur ohne Marge |
| d) ereignisgesteuert, ~1×/Tag | Solcast „heute"/„morgen" (Totale) | nur bei Abruf/Datumswechsel | **keine eigene Konstante** — teilt sich (b), beide falsch |
| e) praktisch nie | SoC, Batteriekapazität | Stunden bis Tage, legitim | keine Alters-Prüfung nötig (bereits korrigiert) |

Die tiefere Ursache liegt aber nicht bei den Konstanten. Für Klasse (d) existiert
**kein** Alters-Limit, das strukturell richtig sein könnte: Derselbe Prognosewert ist um
09:00 Uhr taufrisch und um 23:00 Uhr immer noch korrekt — das Alter des Zeitstempels
sagt in beiden Fällen nichts über die Gültigkeit aus. Ein höherer Schwellwert verschiebt
das Problem nur.

## Verbesserungsvorschlag

### 1. Tagesprognosen: Datums-Prüfung statt Alters-Prüfung ✅

!!! success "Umgesetzt in v2.0.4"
    `HAWebSocketClient.is_stale_daily(entity_id, grace_sec)` fragt „wurde heute
    geschrieben?" statt „wie alt?". Verwendet an beiden Stellen: dem
    Tomorrow-Fallback in `_should_grid_charge` (#5) und dem Warnbanner (#6, jetzt
    zusätzlich auch für `solcast_tomorrow_entity`). Karenz
    `DAILY_VALUE_GRACE_SEC = 900` deckt die Minuten nach Mitternacht ab, in denen
    Solcast noch nicht neu geschrieben hat.

    Der Vergleich läuft bewusst in **lokaler** Zeit: Die Quelle rollt den Tag in
    der Zeitzone der Installation um, `_parse_ts` speichert aber UTC. Unter CEST
    wäre ein UTC-Vergleich jede Nacht zwischen 22:00 und 00:00 UTC falsch.


Statt zu fragen „*wie alt* ist der Zeitstempel?" die fachlich richtige Frage stellen:
**„Stammt der Zeitstempel vom heutigen Tag?"**

Das ist bei Klasse (d) exakt beantwortbar, weil die Solcast-Integration bei jedem
Datumswechsel garantiert schreibt (`coordinator.py`):

```python
self.tasks[TASK_LISTENERS] = async_track_utc_time_change(
    self.hass, self._update_integration_listeners, minute=range(0, 60, 5), second=0
)
```

`_update_integration_listeners` läuft also **alle fünf Minuten** und setzt dort

```python
current_day = dt.now(self.solcast.options.tz).day
self._date_changed = current_day != self._last_day
```

Bei `date_changed` fällt die Early-Return-Bedingung in `_handle_coordinator_update` weg,
und **alle** `DEFAULT`-Sensoren werden neu geschrieben. Daraus folgt eine harte Zusage:

> Solange die Solcast-Integration läuft, tragen `prognose_heute` und `prognose_morgen`
> spätestens fünf Minuten nach lokaler Mitternacht einen Zeitstempel des **laufenden
> Tages**. Ein Zeitstempel von gestern bedeutet zwingend, dass die Integration steht.

Damit wird die Prüfung selbstkalibrierend: keine neue Konstante, kein neues Config-Feld,
keine zusätzliche Entity, und sie bleibt über Jahres- und Sommerzeitwechsel korrekt.

Zwei Punkte für die Umsetzung:

- **Lokale Zeitzone verwenden.** Solcast rechnet den Datumswechsel in lokaler Zeit
  (`dt.now(self.solcast.options.tz)`), `_parse_ts()` liefert dagegen UTC. Bei CEST
  (UTC+2) liegt lokale Mitternacht um 22:00 UTC des Vortages — ein Datumsvergleich in
  UTC wäre zwei Stunden lang falsch. Der Vergleich muss in der lokalen Zeitzone der
  HA-Installation stattfinden.
- **Kleine Karenz.** Zwischen 00:00 und 00:05 lokal steht der Zeitstempel legitim noch
  auf gestern. Eine Toleranz von ~15 Minuten nach Mitternacht vermeidet einen täglichen
  Fehlalarm im Fünf-Minuten-Fenster.

Betrifft #5 und #6. Löst den gemeldeten Fehlalarm und schließt die Lücke im
Netzlade-Fallback aus v2.0.1.

!!! warning "Reichweite dieses Vorschlags"
    Die Datums-Prüfung beantwortet „**läuft die Integration heute?**" — nicht „**wie alt
    sind die Prognosedaten?**". Den zweiten Fall deckt sie nicht ab; siehe Punkt 4.

### 2. Hardware-Sensoren: `unavailable` ist bereits das richtige Signal

Für #1, #2 und #7 liefert die Solarman-Integration bei Verbindungsverlust
`unavailable` — das greift schneller und zuverlässiger als jede Altersprüfung und ist in
`get_state_value()` schon ausgewertet. Die 300-Sekunden-Prüfung dort ist kein Fehler,
aber weitgehend redundant; sie kann als zweite Verteidigungslinie bleiben.

### 3. Strompreis: kein semantisches Äquivalent — kleine Marge genügt ✅

!!! success "Umgesetzt in v2.0.4"
    `PRICE_MAX_AGE_SEC = 21720` in `const.py`, plus Migration `_v13_to_v14()`, die
    Bestandskonfigurationen mit **exakt** dem alten Default 21600 anhebt. Ein
    abweichender, also bewusst gesetzter Wert bleibt unangetastet. Auf dem
    Devcontainer live verifiziert:
    `Migration v13→v14: price_max_age_sec was exactly the longest tariff window
    (21600s/6h) – raised to 21720s for clearance`.


Für #3 gibt es weder einen Datumsbezug noch ein Lebenszeichen: Der Preissensor wird
ausschließlich bei Stufenwechsel geschrieben (`last_changed` = `last_reported`, live
bestätigt).

Hier ist die Obergrenze aber **hart bekannt**: Der Tarif ist rein zeitgesteuert, der
Fahrplan liegt fest, und das längste Fenster ist exakt 6 h (06–12 Uhr). Es gibt keine
Marktdynamik, die daran etwas ändert. Damit braucht es keinen großzügigen Puffer,
sondern nur genug Abstand, um die Punktlandung „Schwelle == längster Block" zu
vermeiden:

```python
PRICE_MAX_AGE_SEC = 21720   # 6 h + 2 min – längstes Tarifsfenster ist exakt 6 h
```

Ein höherer Wert würde nur die Erkennungszeit verschlechtern, ohne einen realen Fall
abzudecken.

Die sauberere Lösung wäre eine Plausibilitätsprüfung gegen den `activation_rules`-Fahrplan
der Entity — dieser Fahrplan ist dort vorhanden und beschreibt den Tarifverlauf
vollständig. Das setzt Zugriff auf Entity-Attribute voraus, den miniEMS heute nirgends
hat; Berührungspunkt zur
[Netzdienliches-Laden-Roadmap](netzdienliches-laden.md), die genau diesen
Zugriff als ersten Schritt vorschlägt.

### 4. Datenfrische von Solcast (#4, #5, #6) ✅

!!! success "Umgesetzt in v2.0.4"
    `get_state_datetime()` liest Entities, deren Zustand ein Zeitstempel ist —
    `get_state_value()` endet in `float(raw)` und scheitert daran. Ausgewertet wird
    der **Wert** von `solcast_last_fetch_entity`, nicht dessen Zeitstempel.

    `SOLCAST_DATA_MAX_AGE_SEC = 30 h`, aus Messdaten hergeleitet statt geraten: auf
    der Produktivanlage an fünf Tagen in Folge **6/6/6/5/4** erfolgreiche Abrufe pro
    Tag, längste legitime Nachtlücke **15,5 h** (12:44 UTC → 04:11 UTC). Kürzere
    Wintertage schätzungsweise ~19 h; 30 h lässt Marge und erkennt eine stehende API
    dennoch binnen gut eines Tages.

    Greift an zwei Stellen: `_forecast_remaining_kwh()` liefert `None`, damit keine
    Regelentscheidung auf tagealten Daten beruht, und das Warnbanner meldet es. Ohne
    konfigurierte Entity entfällt die Prüfung, statt Fehlalarm zu schlagen.


Für die drei Solcast-Prüfungen ist bisher nur der Fehlerfall „Integration tot" gelöst.
Es gibt einen **zweiten**, den kein einziges der bisherigen Signale erkennt:

> Die Solcast-**API** ist unerreichbar (Kontingent aufgebraucht, Internet weg, Dienst
> gestört), **während der Plattencache unverändert weiterliefert.**

In diesem Zustand sieht alles gesund aus:

| Signal | Verhalten in diesem Fall | erkennt den Fehler? |
|---|---|---|
| `unavailable` | Cache gefüllt → Sensor bleibt `available` | ❌ |
| `last_updated` / `last_reported` | rücken vor | ❌ |
| Datums-Prüfung (Vorschlag 1) | Datumswechsel schreibt garantiert → Zeitstempel von *heute* | ❌ |
| Alters-Prüfung bei #4 | Kurve läuft aus Cache + Uhr weiter → Alter bleibt im Normalbereich | ❌ |
| Plausibilitätsprüfung (Vorschlag 5) | Kurvenform bleibt vollständig plausibel | ❌ |

Am gravierendsten ist das bei **#4**, weil dieser Wert direkt in `_should_grid_charge`
einfließt: Die Netzlade-Entscheidung kann tagelang auf einer veralteten Prognose
beruhen, ohne dass irgendeine Warnung erscheint. Erst wenn der Cache nach etwa sieben
Tagen keine Daten mehr für den laufenden Tag enthält, wird `_sensor_data = None` und der
Sensor kippt auf `unavailable` — dann greift die vorhandene Presence-Prüfung. Die Tage
davor sind blind.

**Kandidat für die Lücke:** der **Wert** von
`sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf` — ausdrücklich sein Inhalt, nicht
sein Zeitstempel (siehe Kasten unten). Im Quellcode belegt (`solcastapi.py:561`):

```python
@property
def last_updated(self) -> dt | None:
    """When the data was last updated.

    Returns:
        dt | None: The last successful forecast fetch.
    """
    return self.data[LAST_UPDATED].astimezone(self.tz) if self.data.get(LAST_UPDATED) is not None else None
```

Der Wert stammt aus dem persistierten Datensatz und ist damit exakt das Alter der
Prognosedaten — unabhängig davon, wie oft HA die Sensoren neu schreibt. Live geprüft:
`2026-08-15T08:31:25` bei einer Messzeit von 18:14 UTC ⇒ Prognosedaten **9 h 43 min**
alt.

Umsetzungshürde: Das ist ein Datums-Sensor. `get_state_value()` macht `float(raw)` und
liefert für einen ISO-Zeitstempel `None` (`ha_ws_client.py:69-72`) — es bräuchte einen
Datums-Pfad im Client plus ein Config-Feld für die Entity, analog zu
`load_consumption_entity` in v2.0.3. Ein sinnvoller Schwellwert wäre am typischen
Abrufrhythmus zu bemessen, nicht geraten; dafür fehlt bisher eine Messung über mehrere
Tage.

### 5. Plausibilitätsprüfung des Kurvenverlaufs (#4) — verworfen

!!! failure "Bewusst nicht umgesetzt"
    Nach Vorschlag 4 schließt diese Prüfung keine Lücke mehr. Ihr Gewinn wäre reine
    **Erkennungsgeschwindigkeit** für einen eingefrorenen Sensor — Minuten statt
    Stunden — bei einem seltenen Fehlerfall. Datenfrische deckt Vorschlag 4 ab,
    Verbindungsverlust deckt `unavailable` ab.

    Dem gegenüber stehen drei Präzisierungen, die alle korrekt implementiert und
    gepflegt sein müssten (Mitternachts-Reset statt Morgen, Nachtplateau auf dem
    Tagesmaximum, Toleranz für untertägige Prognoserevisionen). Jede davon ist eine
    eigene Fehlalarmquelle. Das Verhältnis stimmt nicht.

    Die Analyse unten bleibt als Beleg stehen — sie ist die Messgrundlage für das
    Verständnis des Sensorverhaltens, auch ohne Umsetzung.


Statt zu fragen, *wann* der Wert zuletzt kam, prüfen, ob er sich **so verhält, wie er
sich verhalten muss**. `remaining_today` hat einen zwingenden Tagesverlauf: einmal
zurücksetzen, dann bis auf null abnehmen.

Gemessen über zwei volle Tage (HA-Recorder, 356 Datenpunkte, 13.–15.08.2026):

| Phase | Beobachtung (UTC / lokal = UTC+2) | Dauer |
|---|---|---|
| Reset | 22:00 / **00:00 lokal**: 0 → 44,37 bzw. 0 → 33,68 | einmal täglich |
| Nachtplateau | 22:00 → 03:50, Wert konstant auf Tagesmaximum | **5 h 50 min** (beide Nächte identisch) |
| Tagesabnahme | 03:50 → 19:00, monoton fallend, glatt | ~15 h |
| Abendnull | 19:00 / 21:00 lokal: exakt 0, danach konstant | 3 h bis zum Reset |
| Aufwärtssprünge | 14.08. 04:19 (+0,3466) und 08:30 (+0,1271) — beide zu Prognose-Abrufzeitpunkten, nicht im 5-Minuten-Raster | 2× an einem Tag, 0× am anderen |

Daraus drei Präzisierungen gegenüber der naheliegenden Formulierung „nachts null, sonst
fallend, morgens ein Anstieg":

1. **Der Anstieg liegt auf lokaler Mitternacht, nicht am Morgen.** Er ist der
   Tageswechsel, nicht der Sonnenaufgang.
2. **Nur das *abendliche* Dunkelfenster ist null.** In den Morgenstunden vor
   Sonnenaufgang steht der Wert auf seinem Tages*maximum* und ist dort 5 h 50 min lang
   konstant — eine Regel „dunkel ⇒ 0" würde jede Nacht zwischen 00:00 und 05:50 lokal
   Fehlalarm schlagen.
3. **Monotonie gilt nur zwischen Prognose-Abrufen.** Solcast revidiert untertägig; die
   gemessenen Sprünge waren mit +0,35 und +0,13 kWh klein, sind aber systematisch und an
   einem wechselhaften Tag deutlich größer zu erwarten. Eine strikte Monotonie-Regel
   erzeugt Fehlalarme; nötig ist eine Toleranz oder eine Ausnahme zum Abrufzeitpunkt
   (der über den Wert aus Punkt 4 bekannt ist).

**Was die Prüfung leistet:** Sie erkennt einen eingefrorenen, unplausiblen oder falsch
verdrahteten Sensor untertags in **Minuten** statt in acht Stunden — im Tagesverlauf
ändert sich der Wert alle 5 Minuten um Größenordnungen über dem Rauschen (bis zu
0,47 kWh je Intervall gemessen). Das ist eine echte Verbesserung der Erkennungszeit
gegenüber der reinen Altersprüfung und deckt Fehler ab, die keine der anderen
Maßnahmen sieht.

**Was sie nicht leistet:** Sie schließt die Lücke aus Punkt 4 **nicht**. Der Kurvenverlauf
entsteht aus dem gecachten Spline plus der Uhrzeit — bei tagelang toter API bleiben
Reset, Nachtplateau, monotone Abnahme und Abendnull sämtlich intakt. Jedes einzelne
Kriterium dieser Prüfung würde bestehen. Für die Datenfrische bleibt der Wert von
`zeitpunkt_letzter_api_abruf` das einzige belastbare Signal.

!!! warning "Korrigierter Vorschlag"
    Eine frühere Fassung dieser Seite empfahl
    `sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf` als Lebenszeichen-Sensor für
    Solcast — analog zum `battery_power_entity`-Trick beim SoC. **Das war falsch.** Live
    geprüft steht diese Entity auf `2026-08-15T08:31:25`, und ihr eigener `last_changed`
    ist ebenfalls `08:31:25` — exakt so alt wie `prognose_heute` selbst. Als Proxy
    wertlos. (Nebenbefund: `verwendete_api_abrufe` sprang zuletzt um 10:36 Uhr, der
    Abruf-Zeitstempel widerspricht also sogar dem eigenen Zähler der Integration.)

    Die Entity ist damit aber nicht wertlos — nur ihre Rolle war falsch bestimmt: Ihr
    **Zeitstempel** taugt nicht als Lebenszeichen, ihr **Wert** ist dagegen genau das
    Datenalter, das in Punkt 4 fehlt. Die Verwechslung von Zeitstempel und Inhalt ist der
    eigentliche Fehler dieser Fehlerklasse — und sie ist mir hier selbst unterlaufen.

## Nicht Teil dieser Seite

Diese Seite beschreibt nur den **Ist-Zustand und Vorschläge** — keiner der Punkte oben
ist umgesetzt. Die konkrete Umsetzung (Datums-Prüfung für Tagesprognosen, Preis-Marge)
ist ein separater Schritt.

Ausdrücklich **noch ohne fertige Lösung** ist Punkt 4 (Datenfrische von Solcast, #4/#5/#6):
Der Signalgeber ist identifiziert und im Quellcode belegt, aber Client-Erweiterung,
Config-Feld und ein empirisch begründeter Schwellwert fehlen.

Vorschlag 5 (Plausibilitätsprüfung) und Vorschlag 1 (Datums-Prüfung) verbessern jeweils
die Erkennung des **Integrationsausfalls** — Vorschlag 5 zusätzlich deutlich in der
Geschwindigkeit. Die **Datenfrische** deckt keiner der beiden ab; sie bleibt der einzige
offene Punkt dieser Seite und betrifft mit #4 ausgerechnet den einzigen Solcast-Wert mit
direktem Einfluss auf eine Regelentscheidung.

## Bezug zur Tageswechsel-Roadmap

Bei der Prüfung dieser Seite fiel ein zweiter Defekt auf, der in
[Tageswechsel & Energiezählung](tageswechsel-energiezaehlung.md) behandelt wird: Der
Tagesschnitt der Energiezählung läuft dem Wechselrichter um 4 min 54 s voraus. Beide
Seiten hängen enger zusammen, als die Themen vermuten lassen.

### Was der Tageswechsel-Umbau hier **nicht** löst

Zuerst die Abgrenzung, damit keine falsche Erwartung entsteht: Der Wechsel auf
Total-Zähler mit eigenem Delta behebt **keinen einzigen** Punkt aus der Tabelle oben.
Die Solcast-Sensoren haben kein Total-Pendant, und die Preis- und Leistungssensoren sind
von der Energiezählung nicht betroffen. Die beiden Vorhaben sind unabhängig umsetzbar.

### Was er dennoch beiträgt

**1. Vorschlag 1 stützt sich auf eine dort bewiesene Voraussetzung.** Die Datums-Prüfung
funktioniert nur, wenn miniEMS' eigener Tagesschnitt zuverlässig auf **lokaler**
Mitternacht liegt — sonst vergliche man das Solcast-Datum gegen eine verschobene Grenze.
Genau das ist auf der Tageswechsel-Seite gemessen und bestätigt (`TZ=Europe/Berlin` im
Container, Schnitt live beobachtet bei `21:59:38 → 22:00:08 UTC`). Diese Voraussetzung
muss hier also nicht erneut hergeleitet werden — wohl aber gilt: Ändert sich dort etwas
an der Tagesgrenze, ist Vorschlag 1 mit zu prüfen.

**2. Dieselbe Denkfigur, zweimal.** Beide Seiten kommen unabhängig auf dieselbe Regel:

> Nicht dem abgeleiteten Artefakt vertrauen, sondern eine Ebene näher an die Rohquelle
> gehen und die gewünschte Größe selbst bilden.

Dort: nicht den fremden Tagesschnitt übernehmen, sondern den monotonen Gesamtzähler lesen
und das Tagesdelta selbst rechnen. Hier: nicht den Schreibzeitstempel des abgeleiteten
Sensors prüfen, sondern den Abrufzeitpunkt der Quelle (Vorschlag 4). Wer eine der beiden
Lösungen verstanden hat, hat auch die andere verstanden.

**3. Die Monotonie-Absicherung verallgemeinert sich.** Die Tageswechsel-Seite fordert für
den Gesamtzähler: *Ein monoton steigender Wert darf nie rückwärts laufen — tut er es
doch, ist das ein meldepflichtiges Ereignis, kein Rechenfall.* Dieselbe Disziplin ist auf
`zeitpunkt_letzter_api_abruf` anzuwenden, sobald Vorschlag 4 umgesetzt wird: Auch dessen
Wert ist monoton steigend, und ein Rückwärtssprung wäre ein Defekt der Integration, keine
zu interpretierende Zahl.

**4. Koordination bei der Umsetzung.** Beide Vorhaben brauchen neue Config-Felder und
damit eine Schema-Migration (`migration.py`, `CONFIG_SCHEMA_VERSION`). Landen sie in
derselben Version, sollten sie sich **eine** Schemaversion teilen statt zweier
aufeinanderfolgender Migrationsschritte. Betroffen wären in beiden Fällen zusätzlich
`config_loader.py` und `templates/settings.html`.

## Weitere verwandte Seite

Der Zugriff auf Entity-**Attribute** — Voraussetzung für die `activation_rules`-Prüfung
aus Vorschlag 3 — steht als eigener Umsetzungsschritt in der
[Netzdienliches-Laden-Roadmap](netzdienliches-laden.md).
