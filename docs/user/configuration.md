---
revision_date: 2026-10-29
---

# Konfiguration

Alle Einstellungen werden über den Tab **Einstellungen** im miniEMS-Dashboard verwaltet.
Die Werte werden in `/data/config.json` gespeichert und überleben Neustarts, Updates und Supervisor-Neuladen.

!!! info "Keine HA Add-on-Konfigurations-UI"
    Ab v1.4.0 ist der HA Add-on-Tab „Configuration" absichtlich leer.
    Die gesamte Konfiguration erfolgt über die miniEMS-Einstellungsseite.

!!! info "Wie Werte geladen werden"
    Bei jedem Start liest miniEMS drei Quellen mit dieser Priorität (höchste zuerst):

    1. **`/data/options.json`** — vom Supervisor verwaltete Werte, die vom Standard abweichen (du hast sie über die HA-UI geändert)
    2. **`/data/config.json`** — zuletzt gespeicherte Werte (überlebt einen Reset von `options.json` durch Supervisor-Neuladen/Add-on-Updates)
    3. **Eingebaute Standardwerte**

    Das Ergebnis wird bei jedem Start nach `/data/config.json` zurückgeschrieben, sodass deine Einstellungen nie verloren gehen. Alte Feldnamen (z. B. aus Versionen vor v2.0) werden automatisch auf die aktuellen Namen migriert.

    Werte, die **nicht** im Formular auf der Einstellungsseite auftauchen, lassen sich über die Tabs **config.json** bzw. **options.json** im Dashboard direkt als JSON bearbeiten (siehe Abschnitt „Rohdaten-Editoren" ganz unten auf dieser Seite).

---

## Wechselrichter-Entitäten

Diese findest du unter **HA → Entwicklerwerkzeuge → Zustände** — filtere nach `deye`, um die genauen Entitäts-IDs zu finden.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `pv_power_entity` | `sensor.deye_pv_total_power` | Gesamte PV-Leistung (W) |
| `battery_soc_entity` | `sensor.deye_battery_soc` | Batterie-Ladezustand (%) |
| `battery_power_entity` | `sensor.deye_battery_power` | Batterieleistung (W) — **negativ** = Laden, **positiv** = Entladen beim Deye 8K (live verifiziert; eine frühere Version dieser Zeile hatte das Vorzeichen vertauscht) |
| `grid_power_entity` | `sensor.deye_grid_power` | Netzleistung (W) — positiv = Bezug, negativ = Einspeisung |
| `load_power_entity` | `sensor.deye_load_power` | Hauslast (W) |
| `grid_import_energy_entity` | `sensor.deye8k_today_energy_import` | Tages-Netzbezug direkt vom Wechselrichter (kWh, setzt um Mitternacht zurück). Wenn gesetzt, ersetzt sie den aus `grid_power_entity` berechneten Wert; die Netzkosten werden weiterhin pro Tick akkumuliert. Leer lassen, um auf die Berechnung zurückzufallen. |
| `feed_in_energy_entity` | `sensor.deye8k_today_energy_export` | Tages-Einspeisung direkt vom Wechselrichter (kWh, setzt um Mitternacht zurück). Wenn gesetzt, ersetzt sie den berechneten Einspeisewert. Leer lassen für die Berechnung aus `grid_power_entity`. |
| `load_consumption_entity` | `sensor.deye8k_today_load_consumption` | Tages-Hausverbrauch direkt vom Wechselrichter (kWh, setzt um Mitternacht zurück). Wenn gesetzt, ersetzt sie den aus `load_power_entity` hochgerechneten Wert — dadurch unempfindlich gegenüber Add-on-Neustarts. Die Lastkosten (€) werden weiterhin pro Tick akkumuliert, da dafür der Preis zu jedem Zeitpunkt gebraucht wird. Leer lassen, um auf die Berechnung zurückzufallen. Seit v2.0.3. |
| `grid_import_total_entity` | `sensor.deye8k_total_energy_import` | Lebenszeit-Zähler des Netzbezugs (kWh, setzt **nie** zurück). Seit v2.0.4 bevorzugt gegenüber der Tages-Entity darüber: Der Wechselrichter setzt seine Tageszähler auf seiner **eigenen** Uhr zurück — gemessen 4 min 54 s nach lokaler Mitternacht — und meldet in diesem Fenster noch den Vortagesstand. miniEMS bildet das Tagesdelta stattdessen selbst und schneidet den Tag dort, wo es auch die Kosten schneidet. Die Tages-Entity bleibt als Startwert nach einem Neustart mitten am Tag in Gebrauch. Leer lassen, um auf die Tages-Entity zurückzufallen. |
| `feed_in_total_entity` | `sensor.deye8k_total_energy_export` | Lebenszeit-Zähler der Einspeisung. Verhalten wie `grid_import_total_entity`. Seit v2.0.4. |
| `load_consumption_total_entity` | `sensor.deye8k_total_load_consumption` | Lebenszeit-Zähler des Hausverbrauchs. Verhalten wie `grid_import_total_entity`. Seit v2.0.4. |
| `solcast_last_fetch_entity` | `sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf` | Zeitpunkt des letzten **erfolgreichen** Solcast-Abrufs. Ausgewertet wird der **Wert** dieser Entity, nicht ihr Zeitstempel — er ist genau das Alter der Prognosedaten. Nötig, weil Solcast bei unerreichbarer API aus dem Plattencache weiterliefert: Die Sensoren bleiben verfügbar, ihre Zeitstempel rücken vor, und nichts verrät, dass die Zahlen Tage alt sind. Ab 30 h Datenalter wird die Prognose für Entscheidungen verworfen und im Banner gemeldet. Leer lassen, um die Prüfung abzuschalten. Seit v2.0.4. |
| `grid_charge_min_margin_eur_kwh` | `0.02` | Mindest-Wirtschaftlichkeitsmarge fürs Netzladen (€/kWh). Geladen wird nur, wenn `Wirkungsgrad × Entladetarif − Bezugspreis` diese Marge übersteigt. Der Entladetarif wird aus der Historie hergeleitet (entladungsgewichteter Preis der letzten 7 Tage); `avg_discharge_tariff_eur_kwh` überschreibt ihn, wenn gesetzt. Solange Wirkungsgrad oder Entladetarif fehlen, entfällt die Prüfung. Seit v2.0.4. |

Alle fünf oberen Pflicht-Entitäten werden auf **Veraltung** geprüft (`sensor_max_age_sec`, Standard 300 s): Liefert eine Entität länger keinen neuen Wert, behandelt miniEMS sie als „nicht verfügbar" und fällt in den sicheren Zustand zurück (siehe Abschnitt „Netzfreundliche PV-Strategie" weiter unten).

---

## Batterieeinstellungen

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `battery_capacity_kwh` | `10.0` | Nutzbare Batteriekapazität in kWh. Wird durch `battery_capacity_entity` (falls gesetzt) ersetzt. |
| `battery_min_soc` | `15` | Mindest-SoC (%). Unterhalb wechselt miniEMS in `Battery Protection` und blockiert Entladen. |
| `battery_max_soc` | `95` | Maximaler SoC (%). Laden stoppt (Modus `Idle`), wenn erreicht. |

!!! warning "Automatische Absicherung"
    Ist `battery_min_soc ≥ battery_max_soc`, deaktiviert miniEMS beim Start automatisch `battery_control_enabled` und schreibt eine Warnung ins Log — eine unsinnige Konfiguration kann so nicht zu Fehlverhalten am Wechselrichter führen.

---

## Authentifizierung

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `long_lived_token` | *(leer)* | Langlebiger HA-Token — wird als Fallback verwendet, wenn der Supervisor-Token mit 401 abgelehnt wird. Normalerweise nicht erforderlich. |

Zum Erstellen: **HA → Profil → Langlebige Zugriffstoken → Token erstellen**.

---

## Octopus Energy / Strompreis

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `electricity_price_entity` | `sensor.octopus_energy_electricity_current_rate` | Aktueller Spotpreis-Sensor (€/kWh) |
| `cheap_rate_threshold_eur` | `0.10` | Netzladen wird ausgelöst, wenn der Preis **unter** diesem Wert liegt — auch die Obergrenze für den **Niedrig**-Tarif |
| `medium_rate_threshold_eur` | `0.20` | Tarifgrenze: **Niedrig** unter `cheap_rate_threshold_eur`, **Mittel** zwischen beiden, **Hoch** ab diesem Wert |
| `feed_in_tariff_eur_kwh` | `0.08` | Vergütung pro kWh ins Netz eingespeister Energie |
| `fix_price` | `0.30` | Festtarif für den Vergleichssensor „Kosten zum Festpreis" |

!!! info "Tariflogik"
    Drei Tarife klassifizieren den Verbrauch jedes Ticks für den Abschnitt [Tarifnutzung](dashboard.md#tarifnutzung) im Dashboard und die sechs `sensor.miniems_*kwh*rate`-HA-Sensoren:

    | Tarif | Bedingung |
    |---|---|
    | **Niedrig** | `price < cheap_rate_threshold_eur` |
    | **Mittel** | `cheap_rate_threshold_eur ≤ price < medium_rate_threshold_eur` |
    | **Hoch** | `price ≥ medium_rate_threshold_eur` |

    Der aktuelle Tarif wird neben dem Preis im Dashboard angezeigt (grün / gelb / rot). Ist der Preis länger als `price_max_age_sec` (Standard 21 600 s = 6 h) nicht aktualisiert worden, gilt er als veraltet und löst **kein** Netzladen aus.

---

## EMS-Parameter

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `pv_surplus_threshold_w` | `200` | Mindest-PV-Überschuss (W, `pv_power - load_power`) zum Auslösen des PV-Lademodus |
| `update_interval_sec` | `30` | Wie oft die EMS-Schleife läuft (Sekunden, 10–300) |
| `event_log_retention_days` | `30` | Wie viele Tage Ereignislog-Einträge in der Datenbank aufbewahrt werden |

---

## Batteriesteuerung

!!! warning "Zuerst den Simulationsmodus aktivieren"
    Bevor du die Live-Steuerung aktivierst, betreibe miniEMS mit aktiviertem `Simulationsmodus`. Überprüfe im Log, ob die richtigen Befehle für deinen Wechselrichter angezeigt werden.

!!! tip "Schnell umschalten: Abschnitt „Control Mode“"
    Ganz oben im Einstellungs-Tab stehen vier Schalter, die **sofort und ohne Neustart** wirken:
    **Simulation** (gilt für Akku und Wärmepumpe gemeinsam), **Batteriesteuerung an/aus**,
    **WP-Steuerung an/aus** und **Historie nachfüllen** (Import aus der HA-Statistik, siehe
    [Vorhersage & Prognose](#vorhersage-prognose); Einschalten startet den Import sofort). Beim Verlassen von LIVE werden Wechselrichter bzw. Boiler-Sollwert
    zuerst in den sicheren Zustand gebracht (wie beim Beenden des Add-ons).
    Die Schalter werden in `config.json` gespeichert und überstehen jeden Neustart.
    Werte dieser Schlüssel in `options.json` werden ignoriert.

!!! info "Ampere statt Watt"
    Die Deye-Ladungslimits sind Entitäten in **Ampere**, nicht in Watt — anders als in miniEMS-Versionen vor v2.0. Die alten `*_power_w`-Feldnamen werden beim Start automatisch auf die neuen `*_current_a`-Felder migriert; bereits gespeicherte Watt-Werte werden dabei **nicht** umgerechnet, da 1:1 keine gültige Umrechnung existiert. Prüfe die migrierten Werte einmal in den Einstellungen nach einem Update von einer Vorversion.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `battery_control_enabled` | `false` | Hauptschalter für die Wechselrichtersteuerung |
| `battery_control_simulation` | `true` | Befehle protokollieren, aber nicht senden |
| `inverter_charge_current_entity` | `number.deye8k_battery_max_charging_current` | Entität zum Setzen des Ladestrom-Limits (A) |
| `inverter_grid_charge_current_entity` | `number.deye8k_battery_grid_charging_current` | Eigener Grenzwert des Deye für das Laden aus dem Netz (A). Beim Netzladen gilt der kleinere von diesem und dem Ladestrom, deshalb schreibt miniEMS im Modus *Grid Charging* denselben Wert in beide (höchstens `battery_max_grid_charge_current_a`) und bestätigt ihn. Leer = nicht schreiben. |
| `battery_max_grid_charge_current_a` | `185` | Wert des Netzlade-Grenzwerts in allen anderen Modi, beim Beenden und in der Notabschaltung, und Obergrenze beim Netzladen. So bleibt der Grenzwert nie auf 0 A stehen. |
| `grid_charge_switch_entity` | `switch.deye8k_battery_grid_charging` | Schalter-Entität zum Aktivieren/Deaktivieren des Netzladens |
| `battery_discharging_current_entity` | `number.deye8k_battery_max_discharging_current` | Entität zum Setzen des Entladestrom-Limits (A) |
| `battery_max_charge_current_a` | `185` | Maximaler Ladestrom (A). Automatisch auf 0–350 A begrenzt. |
| `battery_max_discharge_current_a` | `185` | Maximaler Entladestrom (A). Automatisch auf 0–350 A begrenzt. |
| `inverter_write_stuck_threshold_sec` | `1800` | Ab dieser Dauer ununterbrochener Unbestätigtheit meldet `sensor.miniems_inverter_write_status` „error" statt „warning" (seit v2.0.7). **Vorläufiger Wert** — siehe Hinweis unten. |

### Wie die Wechselrichtersteuerung funktioniert

Jeder EMS-Modus setzt **alle drei** Wechselrichter-Größen explizit, damit der resultierende Zustand nie vom vorherigen Modus abhängt:

| EMS-Modus | Netzlade-Schalter | Ladestrom | Entladestrom |
|---|---|---|---|
| Grid Charging | `switch.turn_on` | `battery_max_charge_current_a` | `0` A (verhindert sofortiges Wiederentladen) |
| PV Charging | `switch.turn_off` | `battery_max_charge_current_a` | `battery_max_discharge_current_a` |
| Export Surplus *(netzfreundliche Haltephase)* | `switch.turn_off` | `export_hold_charge_current_a` | `battery_max_discharge_current_a` |
| Battery Protection | `switch.turn_off` | `battery_max_charge_current_a` | `0` A |
| Idle | `switch.turn_off` | `battery_max_charge_current_a` | `battery_max_discharge_current_a` |

Befehle sind idempotent — miniEMS sendet einen Service-Call nur, wenn sich der Zielwert ändert. `Export Surplus` erscheint nur, wenn `pv_export_priority_enabled` aktiv ist (siehe unten).

!!! info "Schreibbestätigung (seit v2.0.1)"
    Ein von Home Assistant angenommener Service-Call (HTTP 200) ist noch kein Beweis, dass der Wechselrichter den Wert übernommen hat — manche Deye/Solarman-Anbindungen bestätigen einen geschriebenen Wert erst bei ihrem nächsten Poll, was mehrere Minuten dauern kann. miniEMS gleicht deshalb bei **jedem** Tick den echten HA-Zustand mit dem Zielwert ab und sendet den Befehl automatisch erneut, solange beides nicht übereinstimmt — unabhängig für Ladestrom, Entladestrom und Netzlade-Schalter. Bleibt ein Wert dauerhaft unbestätigt, erscheint eine Warnung im Dashboard-Banner ("Inverter control: N unconfirmed write(s)").

    Seit v2.0.7 ist dieser Status auch als `sensor.miniems_inverter_write_status` abrufbar (`ok`/`warning`/`error`, Rohzahlen als Attribute) — siehe [Berechnungen](../technical/calculations.md). `inverter_write_stuck_threshold_sec` bestimmt, ab wann aus „warning" ein „error" wird; der Standardwert ist ausdrücklich vorläufig, bis genug reale Bestätigungsdauern gesammelt sind.

---

## Netzfreundliche PV-Strategie (Phase 7)

Optionale Strategie, die PV-Überschuss so lange **ins Netz exportiert**, statt ihn in die Batterie zu laden, bis die Solcast-Restprognose für den Tag ungefähr auf den noch benötigten Batterie-Bedarf gefallen ist. Ziel: die Batterie erst spät am Tag füllen, wenn ohnehin noch genug Sonne kommt — schont das Netz vor unnötiger Mittags-Einspeisespitze und lässt trotzdem genug Reserve für den Abend.

!!! warning "Standardmäßig deaktiviert"
    Bei `pv_export_priority_enabled = false` verhält sich miniEMS exakt wie zuvor: jeder PV-Überschuss lädt sofort die Batterie. Die Strategie hat außerdem **keine Wirkung**, solange `battery_control_enabled = false` ist — miniEMS schreibt in dem Fall eine Warnung ins Log.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `pv_export_priority_enabled` | `false` | Hauptschalter für die Strategie |
| `pv_charge_margin_factor` | `1.2` | Sicherheitsfaktor auf den Batteriebedarf beim Vergleich mit der Restprognose. `>1` lädt früher (Puffer gegen eine zu optimistische Solcast-Prognose). Automatisch auf 0,5–3,0 begrenzt. |
| `pv_charge_hysteresis_frac` | `0.10` | Totband um den Umschaltpunkt (0,10 = ±10 %), damit der Modus nicht flattert. Automatisch auf 0,0–0,5 begrenzt. |
| `pv_export_min_soc_pct` | `30` | Unterhalb dieses SoC wird der Export-Halt **nie** angewendet — die Batterie lädt immer. Muss über `battery_min_soc` liegen; sonst wird er automatisch auf `battery_min_soc + 10` angehoben. |
| `pv_charge_backstop_hour` | `14` | Lokale Stunde (0–23), ab der die Batterie unabhängig von der Prognose immer lädt — verhindert, dass ein zu optimistischer Nachmittag die Batterie leer lässt. |
| `export_hold_charge_current_a` | `0` | Ladestrom (A) während der Export-Haltephase. `0` blockiert das Laden vollständig. Automatisch auf 0–350 A begrenzt. |
| `mode_dwell_sec` | `300` | Ein neuer Modus muss diese Zeit lang durchgehend angefordert werden, bevor er wirklich angewendet wird (Anti-Flattern). Dringende Wechsel (SoC-Schutz, Sensorausfall) umgehen dieses Delay. Automatisch auf 0–3600 s begrenzt. |
| `battery_soc_hysteresis_pct` | `2` | `Battery Protection` wird erst verlassen, wenn der SoC über `battery_min_soc + battery_soc_hysteresis_pct` gestiegen ist — verhindert Flattern genau am Grenzwert. |
| `grid_charge_min_free_kwh` | `1.0` | Mindest-freie Batteriekapazität (kWh), unterhalb derer sich Netzladen nicht mehr lohnt. |
| `grid_charge_dark_start_hour` / `grid_charge_dark_end_hour` | `21` / `6` | Zeitfenster (lokale Stunden), in dem Netzladen erlaubt ist, wenn weder die heutige noch die morgige Solcast-Prognose eine Entscheidung tragen ("es kann heute keine Sonne mehr kommen, und über morgen ist nichts Verlässliches bekannt"). Beide automatisch auf 0–23 begrenzt. |
| `sensor_max_age_sec` | `300` | Nach dieser Zeit ohne Aktualisierung gelten Leistungs-/SoC-Sensoren als veraltet — 5 Minuten Stillstand bei einem Live-Wert bedeutet defekte Anbindung. |
| `forecast_max_age_sec` | `28800` | Veraltungsgrenze für die Solcast-Prognose (aktualisiert typischerweise alle 30 min bei Tageslicht, kann nachts aber je nach Solcast-Plan 6 h und mehr ohne neuen Wert bleiben — 8 h Marge verhindert Fehlalarme). |
| `price_max_age_sec` | `21600` | Veraltungsgrenze für den dynamischen Tarifpreis (manche Anbieter halten denselben Wert mehrere Stunden). |

!!! tip "Wie die Entscheidung funktioniert"
    - Die Haltephase (`Export Surplus`) startet nur, wenn **alle** Bedingungen erfüllt sind: Strategie aktiv, vor der Backstop-Stunde, SoC über `pv_export_min_soc_pct`, freie Kapazität vorhanden, und die Solcast-Restprognose (`solcast_remaining_today_entity`) über dem mit `pv_charge_margin_factor`/`pv_charge_hysteresis_frac` berechneten Schwellwert.
    - **Jeder** fehlende oder veraltete Eingabewert (SoC, Prognose, Zeitfenster) lässt die Haltephase sofort abbrechen und die Batterie normal laden — die Logik schlägt also immer in Richtung „Batterie voll machen" fehl, nie in Richtung „Batterie leer lassen".
    - Netzladen bei fehlender oder erschöpfter Tagesprognose (z. B. abends, wenn für heute keine Sonne mehr kommt) prüft seit v2.0.1 zuerst die **morgige** Solcast-Prognose (`solcast_tomorrow_entity`): Reicht sie rechnerisch aus, um die Batterie zu füllen, wird **nicht** aus dem Netz geladen. Erst wenn auch dafür kein verlässlicher Wert vorliegt, greift wie bisher das reine Dunkelfenster (`grid_charge_dark_start_hour`–`grid_charge_dark_end_hour`).

---

## Vorhersage & Prognose

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `weather_entity` | `weather.openweathermap` | HA-Wetter-Entität für temperaturbasierte Lastvorhersage |

Die Verbrauchsprognose für morgen sucht Tage der letzten 60 Tage, deren
Außentemperatur-**Tagesmittel** höchstens ±4 °C vom vorhergesagten Tagesmittel
für morgen abweicht, und nimmt deren mittleren Verbrauch (mindestens 3 Tage).
Gibt es weniger, gilt der Median der letzten 14 Tage. Nur ganz ohne gemessene
Tage greifen feste Regeln nach der Tagesprognose:

| Bedingung | Vorhergesagte Last |
|---|---|
| Tiefstwert < 0 °C und Höchstwert < 0 °C | 30 kWh |
| Tiefstwert < 0 °C und Höchstwert < 10 °C | 20 kWh |
| Tiefstwert > 0 °C und Höchstwert < 15 °C | 10 kWh |

Für den Temperaturabgleich sollte `outdoor_temp_entity` (Abschnitt unten)
gesetzt sein – sonst wird das Tagesmittel aus der Stundenprognose gebildet.

!!! tip "Leere Historie nach Neuinstallation"
    Mit dem Schalter **Historie nachfüllen** (oben unter *Control Mode*,
    `history_backfill_enabled`) übernimmt miniEMS fehlende Tage der letzten
    60 Tage aus den Langzeitstatistiken von Home Assistant – Tagesverbrauch aus
    dem Lebenszeitzähler, Außentemperatur-Tagesmittel. Gemessene Tage werden
    nie überschrieben. Details: [Berechnungen](../technical/calculations.md).

---

## Stündliche Bedarfsprognose & Überbrückungsplanung (seit v3.3.0)

Ersetzt beim Netzladen die Faustregel „PV-Restprognose gegen freien Speicher“
durch eine Rechnung in 30-Minuten-Schritten: **Wie viel muss im aktuellen
günstigen Tarif-Fenster geladen werden, um das nächste teure Fenster zu
überbrücken – und lohnt sich das?** Berücksichtigt werden der stündliche
Hausverbrauch (inkl. Wärmepumpe, abhängig von der Außentemperatur), die
Solcast-Prognose je 30 Minuten und der Tarif. Ablauf und Diagramm:
[Netzladen-Entscheidung](../technical/netzladen-entscheidung.md).

Der Stundenverlauf wird **immer** aufgezeichnet, auch wenn der Schalter aus
ist – so ist beim Einschalten schon Historie da.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `load_forecast_enabled` | `false` | Hauptschalter. Aus = Netzladen entscheidet wie bisher. |
| `load_forecast_min_days` | `14` | Tage mit Stundenhistorie, bevor die Planung entscheidet. Vorher wird sie berechnet und angezeigt, die alte Logik entscheidet. 1–60. |
| `heat_pump_power_entity` | leer | Leistung der Wärmepumpe in W, optional. Mehrere Entities kommagetrennt werden addiert (z. B. die drei Phasen eines Shelly 3EM). Leer = Temperaturabhängigkeit wird aus dem Gesamtverbrauch gelernt. |
| `outdoor_temp_entity` | leer | Außentemperatur in °C, optional. Leer = Wert der laufenden Stunde aus der Stundenprognose von `weather_entity`. |
| `history_backfill_enabled` | `false` | Schalter oben in den Einstellungen („Control Mode“), wirkt ohne Neustart: Einschalten startet den Import sofort. Beim Start fehlende Tage der letzten 60 Tage (Tagesverbrauch aus `load_consumption_total_entity`, Außentemperatur-Tagesmittel aus `outdoor_temp_entity`) aus den HA-Langzeitstatistiken nachfüllen. Nur Lücken – gemessene Tage und der laufende Tag werden nie überschrieben, ein erneuter Lauf ändert nichts. |
| `heating_limit_temp_c` | `15.0` | Heizgrenze: darüber heizt die Wärmepumpe nicht. Warmwasser wird pauschal als fester Anteil gelernt. 5–25. |
| `pv_forecast_scenario` | `blend` | Solcast-Szenario: `estimate` (Mittel), `estimate10` (pessimistisch) oder `blend`. |
| `pv_forecast_blend_weight` | `0.7` | Bei `blend`: Anteil von `estimate`, Rest `estimate10`. Zu wenig laden kostet die volle Tarifspanne je kWh, zu viel nur die Umwandlungsverluste – deshalb leicht pessimistisch. |
| `tariff_source` | `auto` | Tarifkalender: `auto` (datierte Preise `rates[]` falls vorhanden, sonst feste Zeitfenster `timeslots[]`), `timeslots` oder `rates`. |

!!! note "Was sich mit dem Schalter ändert"
    - Die absolute Billig-Schwelle `cheap_rate_threshold_eur` spielt fürs Netzladen keine Rolle mehr – entscheidend ist, ob sich eine gespeicherte kWh gegenüber den teuren Stunden lohnt (`grid_charge_min_margin_eur_kwh`).
    - Geladen wird nur die geplante Menge, nicht mehr bis `battery_max_soc`. Start ab `grid_charge_min_free_kwh`, Stopp wenn der Restbedarf unter 0,2 kWh fällt.
    - Ein kleiner PV-Überschuss verhindert ein nötiges Netzladen nicht mehr.
    - Fehlt eine Eingabe (Solcast-Detailprognose, Tarifkalender, Preis, SoC), entscheidet die bisherige Logik.

---

## Entscheidungslogik v4 (seit v4.0.0)

Phase A des [Gesamtplans](../roadmap/gesamtplan.md). **Jeder Baustein hat einen
eigenen Schalter, alle stehen auf aus**: Mit den Standardwerten entscheidet miniEMS
genau wie v3.7.2. Einzeln einschalten, erst mit `battery_control_simulation`
beobachten, dann live. Was ein Baustein entscheidet, steht im Protokoll und in
`/api/status` (`price_classes`, `energy_value`, `battery_plan`, `learning`).

### Preise und Akku

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `price_classes_enabled` | `false` | **A1** – „günstig“ relativ zum Tag statt fester Schwelle. Gilt für Netzladen, `price_tier` und das Niedrigpreisfenster des Warmwasser-Planers. Die Kostenstatistik nach Tarifstufe bleibt bei den festen Schwellen. |
| `price_cheap_hours` | `6` | So viele günstigste Stunden des Kalendertags gelten als „low“. Bei Octopus (8 h NIEDRIG) ergibt 6 genau NIEDRIG. 1–20. |
| `price_hysteresis_min_eur` / `_max_eur` | `0.005` / `0.02` | Grenzen der Preis-Hysterese (15 % der Tagesspanne). Wird für das Band bei Day-Ahead-Preisen genutzt. |
| `price_flat_spread_eur` | `0.03` | Liegen alle Preise des Tages enger beieinander, ist nichts „low“ – keine Optimierung. |
| `load_profile_enabled` | `false` | **A2** – Restverbrauch heute und morgen aus dem Stundenmodell der Bedarfsprognose statt aus dem Tagesmedian. V2 verschiebt eine Ladung nur, wenn der Akku bis zum späteren Fenster nicht gebraucht wird. Braucht Stundenhistorie. |
| `soc_aware_gate_enabled` | `false` | **A3** – Netzladen nur, wenn die gekaufte kWh eine teurere Stunde ersetzt, die der Akku sonst nicht mehr decken kann; geladen wird nur diese Menge. Ersetzt den historischen Entladetarif. Braucht das Stundenmodell. |
| `battery_wear_cost_eur_kwh` | `0.0` | Verschleiß je entladener kWh: Akkupreis ÷ (Zyklen × nutzbare kWh), z. B. 4000 € ÷ (6000 × 9 kWh) ≈ 0,074 €. Senkt die Marge im Gate, im Überbrückungsplan und im Tagesplan. |
| `discharge_hold_enabled` | `false` | **A4** – Modus *Hold for Peak*: kein Entladen, wenn der Akku für spätere, teurere Stunden gebraucht wird. PV lädt weiter. Greift nie anstelle einer Netzladung. |
| `discharge_hold_min_spread_eur` | `0.03` | Gehalten wird nur für Stunden, die mindestens so viel teurer sind als jetzt. |
| `battery_plan_enabled` | `false` | **A6** – Der Akku-Tagesplan entscheidet (Slot 0). Aus = er wird trotzdem berechnet und angezeigt (Schattenbetrieb); Abweichungen zur tatsächlichen Entscheidung stehen im Log. Braucht Stundenmodell und Solcast-Detailprognose. |
| `battery_plan_style` | `careful` | `careful` (PV × 0,85, Last × 1,1), `balanced`, `bold`. |
| `battery_plan_switch_cost_eur` | `0.01` | Kosten je Aktionswechsel im Plan – hält ihn ruhig. |
| `battery_learned_values_enabled` | `false` | **A7a** – Gelernte Akku-Wirkungsgrade statt der Wechselrichter-Effizienz im Gate, in der Sperre und im Plan. Gelernt wird immer (`learning.battery`). |
| `pv_correction_enabled` | `false` | **A7b** – PV-Prognose je Tagesstunde mit Messung ÷ Prognose korrigieren (0,3–2,0). |
| `plan_feedback_enabled` | `false` | **A7c** – Last (0,8–1,25) und PV (0,7–1,2) um die systematische Abweichung korrigieren. |
| `day_ahead_enabled` | `false` | **A9** – für Börsenpreise: „gleich günstig“ als Band, kurze günstige Ausreißer beenden die Überbrückung nicht, V1 über den ganzen günstigen Lauf, Mittelpreis je Slot. Für Zeitfenster-Tarife ohne Wirkung. |
| `price_unit` | `auto` | Einheit einer datierten Preisliste: `auto` (einmal für die ganze Liste bestimmt), `eur`, `ct`, `eur_mwh`. Ein expliziter Wert gilt auch für den Preissensor. |
| `battery_full_charge_days` | `0` | **A11** – spätestens alle n Tage auf `battery_max_soc` laden (Zellausgleich, SoC-Kalibrierung): PV wird nicht zurückgehalten, im günstigen Fenster wird aus dem Netz geladen, wenn die PV es nicht schafft. 0 = aus, Vorschlag 14. |

### Negative Preise und Einspeisung (A5)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `negative_price_enabled` | `false` | Bei Preis ≤ 0 oder unvergüteter Einspeisung: PV in den Akku statt einspeisen, Netzladen ohne Gate (mit Platz für die restliche PV), bei vollem Akku Warmwasser bis `hw_max_temp_c`, optional Einspeisung auf 0 W. |
| `feed_in_price_entity` | leer | Sensor mit dem aktuellen Einspeisepreis; ≤ 0 heißt „Einspeisen bringt nichts“. |
| `feed_in_limit_w` | `0` | Einspeisegrenze am Netzanschluss in W (z. B. 60 % der Modulleistung). Darüber gibt es keinen PV-Halt, der Akku nimmt den Überschuss; der Tagesplan hält dafür Platz frei. 0 = keine Grenze. |
| `grid_export_limit_entity` | leer | `number`-Entity der Einspeisebegrenzung am Wechselrichter (W). Nur zusammen mit `grid_export_limit_normal_w`. |
| `grid_export_limit_normal_w` | `0` | Wert, auf den die Begrenzung zurückgesetzt wird (auch beim Beenden und von der Notabschaltung). |
| `price_is_spot` | `false` | Der Preissensor liefert reine Börsenpreise: wirksamer Preis = (Börsenpreis + `price_fee_eur_kwh`) × (1 + `price_vat_pct` %). |
| `price_fee_eur_kwh` | `0.0` | Netzentgelt, Abgaben, Umlagen in €/kWh. |
| `price_vat_pct` | `0.0` | Mehrwertsteuer in %. |

### Sicherheit (A10)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `failsafe_timeout_min` | `15` | Meldet sich das Add-on so lange nicht, setzt die **HA-Integration** Netzladen aus, Lade- und Entladestrom auf Maximum, den Boiler-Sollwert auf den Ruhewert und die Einspeisebegrenzung zurück, feuert `miniems_alert` und zeigt einen Reparaturhinweis. Nur wenn eine Steuerung live ist. 0 = aus. |
| `write_budget_per_hour` | `0` | Höchstens so viele Änderungen des gestreckten Ladestroms (V1) pro Stunde, danach bleibt der Wert stehen. Moduswechsel sind nicht begrenzt. 0 = unbegrenzt. Die Zahl der Sollwertwechsel je Kanal steht in `/api/status` (`write_changes_today`). |

### Warmwasser (A8, A11)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `hw_start_cost_enabled` | `false` | **A8** – den gelernten Wärmeverlust je Start jeder geplanten Ladung aufschlagen (getrennt für Start aus Standby und Umschalten aus dem Heizbetrieb). Kleine Nachladungen lohnen sich dann von selbst nicht; `hw_min_charge_k` bleibt als Untergrenze. |
| `hw_thermal_energy_entity` | leer | Wärmezähler Warmwasser der Wärmepumpe in kWh (Lebenszeit), z. B. `sensor.eu08l_hp1_hot_water_thermal_energy_total`. Ohne ihn wird nichts gelernt. |
| `hw_hygiene_enabled` | `false` | **A11** – Hygieneladung, im günstigsten Slot der nächsten 24 h. Für ein Einfamilienhaus keine Pflicht; prüfen, ob die Wärmepumpe ein eigenes Programm fährt. |
| `hw_hygiene_temp_c` | `60.0` | Zieltemperatur der Hygieneladung. 50–70. |
| `hw_hygiene_interval_days` | `7` | Fällig, wenn der Boiler so lange nicht auf der Temperatur war. |

---

## Mehrere Verbraucher (seit v4.1.0)

Phase B des [Gesamtplans](../roadmap/gesamtplan.md): Sicherung, E-Auto, Heizung.
**Alle Schalter stehen auf aus**: Mit den Standardwerten entscheidet miniEMS genau
wie v4.0.0. E-Auto und Heizung haben, wie Akku und Warmwasser, je einen Schalter
für die Steuerung und einen für die Simulation. Der Schalter „Simulation“ oben auf
der Einstellungsseite setzt alle vier. Was ein Baustein entscheidet, steht im
Protokoll und in `/api/status` (`fuse`, `ev`, `surplus_share`, `heating`,
`best_start`) sowie in den Sensoren `sensor.miniems_fuse_headroom`,
`sensor.miniems_ev_mode`, `sensor.miniems_ev_plan_kwh`,
`sensor.miniems_heating_offset` und `sensor.miniems_best_start_<gerät>`.

!!! warning "Reihenfolge der Live-Schaltung"
    Ohne Sicherungs-Wächter (B1) mit gemessenem Nennstrom kein E-Auto live. Die
    Phasenzuordnung der Stromsensoren vorher per Messung prüfen: einen bekannten
    einphasigen Verbraucher einschalten und nachsehen, welche Phase steigt.

### Hauptsicherung und §14a (B1)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `fuse_guard_enabled` | `false` | Drosselt Netzladen und Wallbox, wenn eine Phase der Sicherung nahekommt. Der Wächter reduziert nur, er startet nie etwas. |
| `fuse_rated_a` | `0` | Nennstrom der Hauptsicherung in A. 0 = unbekannt, der Wächter bleibt aus (Warnband). |
| `fuse_work_limit_pct` | `90` | Arbeitsgrenze in % des Nennstroms. Darüber wird gedrosselt, sobald der thermisch gefilterte Strom (I², τ = 60 s) sie überschreitet. Ein einzelner Messwert über 1,2 × Nennstrom drosselt sofort. Nach dem Drosseln 20 s keine Erhöhung. |
| `phase_current_entities` | Deye-Netz-CTs | Drei Stromsensoren L1, L2, L3 in A, durch Komma getrennt. Vorbelegt mit `sensor.deye8k_external_ct1..3_current`. |
| `phase_power_entities` | Deye-Netzleistung je Phase | Drei Leistungssensoren in W (+ = Bezug). Erkennt einspeisende Phasen, die nie der Grund einer Drosselung sind. Optional. |
| `phase_voltage_v` | `230` | Phasenspannung für die Umrechnung A ↔ W. |
| `fuse_stale_sec` | `60` | Ältere Phasenwerte gelten als fehlend. Dann rechnet der Wächter mit der gelernten Grundlast je Phase plus 6 A, nie mit 0 A. |
| `fuse_poll_sec` | `10` | Prüfabstand, solange Netzladen oder Wallbox läuft. |
| `grid_dimming_entity` | leer | `binary_sensor` der Steuerbox nach §14a EnWG (an = gedimmt). |
| `grid_dimming_limit_w` | `4200` | Zugesicherte Mindestleistung für alle steuerbaren Verbraucher zusammen, solange gedimmt wird. Bei mehreren Geräten den Wert aus der Festlegung der Bundesnetzagentur bzw. vom Netzbetreiber eintragen. |

Konkurrieren Netzladen und E-Auto um die Reserve, bekommt die Aufgabe mit der
knappsten Frist den Vorrang. Fehlt Leistung, wird zuerst beim Verbraucher mit der
spätesten Frist gekürzt.

### Mehrere Verbraucher (B2)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `charge_priority` | `battery` | Wer den PV-Überschuss zuerst bekommt: `battery` (Akku bis `battery_priority_soc_pct`, dann E-Auto, Warmwasser, Rest-Akku, Einspeisung) oder `ev`. |
| `battery_priority_soc_pct` | `50` | Unter diesem SoC lädt der Akku vor dem E-Auto. Darüber gibt er seine PV-Ladung an das Auto ab. |
| `ev_battery_mode` | `block` | Während das Auto lädt: `block` = der Akku entlädt nicht (er würde sonst das Auto speisen, denn der Deye deckt jede Last hinter seinem Netz-Messwandler). Modus *Hold for Peak*, Grund „ev charging“. `allow` = keine Kopplung. Nur mit `ev_control_enabled`. |

Die geplante Ladeleistung des Autos geht als Last in den Akku-Tagesplan (A6) und
in den Überbrückungsplan ein.

### E-Auto und Wallbox (B3–B5)

Die Wallbox wird über HA-Entities angesprochen, nicht über einen festen
Hersteller. Lesend arbeitet miniEMS, sobald `ev_charge_power_entity` oder
`ev_plugged_entity` gesetzt ist. Mit `ev_control_enabled` wird entschieden, in der
Simulation nur protokolliert.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `ev_control_enabled` | `false` | E-Auto steuern. Aus = nur lesen und planen. |
| `ev_control_simulation` | `true` | Nur protokollieren (`[SIM]`), nichts schreiben. |
| `ev_charge_power_entity` | leer | Ladeleistung (W oder kW). |
| `ev_plugged_entity` | leer | Gesteckt (`binary_sensor` oder Text wie `connected`, `charging`). Ohne ihn gilt „gesteckt“, solange Leistung fließt. |
| `ev_status_entity` | leer | Status der Wallbox. Steht er trotz Freigabe länger als 2 min auf einem Wert aus `ev_status_waiting_states`, versucht miniEMS einmal je 10 min `switch.turn_on` („Ladung hängt“). |
| `ev_status_waiting_states` | `waiting,waiting_for_vehicle,ready` | Texte für „wartet auf das Auto“. |
| `ev_enable_entity` | leer | Schalter für die Ladefreigabe. |
| `ev_current_entity` | leer | Ladestromgrenze (`number`, A). |
| `ev_phases_entity` | leer | Phasenumschaltung (`select` mit Optionen `1`/`3` oder `number`). Leer = keine Umschaltung. |
| `ev_soc_entity` | leer | SoC des Autos. Ohne ihn rechnet die Planung mit 20 % und zieht die in dieser Sitzung geladene Energie ab. |
| `ev_mode_entity` | leer | Übersteuerung als `input_select` mit den Optionen **Auto / Jetzt / Nur PV / Stopp**. Leer = Auto. |
| `ev_target_soc_entity` | leer | Ziel-SoC als `input_number`. |
| `ev_departure_entity` | leer | Abfahrt als `input_datetime` (Datum und Uhrzeit oder nur Uhrzeit). |
| `ev_target_soc_pct` | `80` | Ziel-SoC ohne Helfer. |
| `ev_departure_time` | `07:00` | Abfahrt ohne Helfer. Bei 100 % Ziel plant miniEMS eine Stunde früher. |
| `ev_morning_soc_pct` | `50` | Mindest-SoC zur Morgenzeit (Teilziel vor dem Rest). 0 = keins. |
| `ev_morning_time` | `06:00` | Uhrzeit des Teilziels. |
| `ev_capacity_kwh` | `50` | Akkukapazität des Autos. Bedarf = (Ziel − Ist) × Kapazität × 1,12. |
| `ev_max_charge_kw` | `11` | Ladeleistung des Autos bei vollem Strom. Daraus wird W je A bestimmt, damit ein Auto mit zweiphasigem Bordlader richtig geplant wird. |
| `ev_max_current_a` / `ev_min_current_a` | `16` / `6` | Strombereich der Wallbox. |
| `ev_phases_max` | `3` | 1 oder 3. |
| `ev_pause_mode` | `zero_current` | Pause mit 0 A (Sitzung bleibt bestehen) oder `switch` (Freigabe aus), falls die Wallbox 0 A nicht annimmt. |

**Modi:**

- **Auto:** Die Planung (B4) legt den Bedarf in die günstigsten Slots vor der
  Frist, innerhalb der Leistung, die die Sicherung nach der vorhergesagten
  Grundlast übrig lässt. Niedrige Ströme bekommen einen Aufschlag
  (0,10 € × (P_max ÷ P − 1)), weil sie schlechter laden. Reicht die Zeit nicht,
  steht „target not reachable“ im Warnband. Reicht der PV-Überschuss für mehr,
  lädt das Auto mit PV.
- **Jetzt:** voller Strom, nur durch Sicherung und §14a begrenzt.
- **Nur PV:** PV-Überschuss in Echtzeit (B5). Start einphasig ab dem
  Mindeststrom, dreiphasig ab rechnerisch 18 A, zurück unter 5,5 A, danach
  15 min Sperre. Nachregeln alle 5 min, jede Minute, wenn das Auto teuren
  Netzstrom kauft. Mindestens 30 min Laufzeit, danach Pause.
- **Stopp:** Freigabe aus.

Beim Beenden, beim Verlassen von LIVE und über die Notabschaltung (A10) wird die
Wallbox freigegeben und auf vollen Strom gesetzt: Das kostet im Zweifel Geld,
lässt das Auto aber nie ungeladen.

### Heizung (B6, B7)

Gesteuert wird nur der **Vorlauf-Offset** der Wärmepumpe (Lambda: HC-Register 50,
−10 … +10 K). miniEMS setzt ihn beim Start, beim Beenden und bei der Übergabe auf
0 K.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `heating_control_enabled` | `false` | Heizung steuern. |
| `heating_control_simulation` | `true` | Nur protokollieren. |
| `heating_offset_entity` | Lambda HC1 | `number` des Vorlauf-Offsets. |
| `heating_offset_up_k` / `heating_offset_down_k` | `2` / `2` | Anheben in günstigen, Absenken in teuren Stunden (Preisklassen aus A1, mit Hysterese). Vor einer teuren Stunde in den nächsten 6 h mindestens +1 K (Vorladen). |
| `heating_cop_drop_pct_per_k` | `2.5` | COP-Verlust je K höherer Vorlauftemperatur. Angehoben wird nur, wenn `Preis jetzt ÷ COP_erhöht < Preis Spitze ÷ COP_normal`. |
| `heating_sun_kwh` | `10` | Ab so viel PV-Prognose in den nächsten 12 h wird das Vorladen halbiert. |
| `heating_compressor_max_w` | `0` | Elektrische Leistung des Verdichters allein. Liegt die Wärmepumpe 10 min lang über dem 1,3-fachen, läuft vermutlich der Heizstab: Warnung, Absenkung für den Tag halbiert. Braucht `heat_pump_power_entity`. 0 = aus. |
| `heating_surplus_entity` | leer | Optional: PV-Überschuss selbst an die Wärmepumpe melden (`number`, W; Lambda Register 102). **Nur bei ausgeschalteter Lambda-Option `pv_surplus`**, sonst regeln zwei Regler dieselbe Wärmepumpe. |
| `heating_room_temp_entity` | Lambda HC1 Raumtemperatur | Innentemperatur. |
| `heating_comfort_min_c` | `0` | Untergrenze der Raumtemperatur. Nahe daran (0,3 K) keine Absenkung; 30 min am Stück darunter: Anheben. 0 = keine Raumregeln. |
| `heating_room_setpoint_c` | `21` | Raumtemperatur, die die Heizkurve hält (für den Planer). |
| `heating_room_control_enabled` | `false` | **B7** – Der Vorheizplaner entscheidet statt der Preisklasse. Nur mit einem Raummodell, das den Rückblick besteht (siehe unten). |

**Schutzregeln, immer aktiv:** Über der Heizgrenze (`heating_limit_temp_c`) kein
Offset. Höchstens 3 h am Stück abgesenkt, danach 1 h normal. Unter −5 °C außen
höchstens 1 K Absenkung, unter −15 °C keine. Nach jeder Änderung 60 min (Anheben)
bzw. 45 min (Absenken) Sperre; die Sperre übersteht einen Neustart.

**Raummodell (B7):** Aufgezeichnet werden je Stunde Raumtemperatur, Heizanteil und
Offset (`hourly_stats`). Gelernt werden Auskühlrate `a · (T_innen − T_außen) − b · PV_3h`
und Aufheizrate `c + d · T_außen`, ab 48 Auskühl- und 24 Heizstunden. Vor dem
Einsatz rechnet miniEMS jede zweite Stunde nach (Rückblick); erst unter 0,3 K/h
mittlerem Fehler ist das Modell bereit. Stand und Fehler stehen in
`/api/status` unter `heating.model`.

### Startempfehlung (B8)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `flex_devices` | leer | Geräte als `Name:kWh:Stunden`, durch Komma getrennt, z. B. `Waschmaschine:1.2:2, Spülmaschine:1.0:3`. Dezimalpunkt, kein Komma. |

Je Gerät gibt es `sensor.miniems_best_start_<name>` (Zeitstempel) mit Ende,
Kosten, Kosten bei Start jetzt und Ersparnis als Attribute. PV-Überschuss zählt mit
der Einspeisevergütung. Der Sensor steuert nichts.

### Warmwasser-Sollwert als Entity (D1)

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `hw_setpoint_entity` | leer | Boiler-Sollwert als `number`-Entity statt über `hw_write_service`/`hw_write_data`. Für Wärmepumpen, deren Integration den Sollwert direkt anbietet. Leer = Dienst-Vorlage wie bisher (Lambda). |

---

## Wärmepumpe: Warmwasser (seit v3.5.0)

Datensammlung und Steuerung der Warmwasserbereitung haben eine eigene Seite:
[Wärmepumpe: Warmwasser-Steuerung](warmwasser.md). Dort stehen alle Einstellungen der
Abschnitte *Heat Pump / Hot Water* und *Hot Water Control* sowie die Einrichtung für die
Lambda-Wärmepumpe.

---

## Solcast PV-Prognose

**Erforderlich.** [Solcast](https://solcast.com/) liefert Dachanlagen-PV-Prognosen. Installiere die Solcast-HA-Integration, schalte in ihren Optionen die **detaillierte Prognose** ein (Attribut `detailedForecast` mit 30-Minuten-Werten an den Sensoren „Prognose heute/morgen“) und konfiguriere die Entitäten hier.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `solcast_remaining_today_entity` | `sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute` | Erwartete verbleibende PV für heute (kWh) — wird für Netzlade- und Export-Entscheidung verwendet |
| `solcast_today_entity` | `sensor.solcast_pv_forecast_prognose_heute` | Gesamte erwartete PV für heute (kWh) — Dashboard-Anzeige |
| `solcast_tomorrow_entity` | `sensor.solcast_pv_forecast_prognose_morgen` | Erwartete PV für morgen (kWh) — Dashboard-Anzeige **und** seit v2.0.1 Fallback für die Netzlade-Entscheidung (siehe unten) |

!!! tip "Warum der Solcast-Restwert wichtig ist"
    Sowohl die Netzladeentscheidung als auch die netzfreundliche PV-Strategie vergleichen die freie Batteriekapazität mit `solcast_remaining_today_kwh`.
    Ist Solcast nicht konfiguriert, länger als `forecast_max_age_sec` veraltet, oder ist die heutige Sonne schlicht schon vorbei (Wert legitim ~0, z. B. abends), gilt die Tagesprognose als „nicht verfügbar". Für die Netzlade-Entscheidung prüft miniEMS in diesem Fall zusätzlich `solcast_tomorrow_kwh`, bevor es auf die konservativeren Dunkelfenster-Regeln zurückfällt (siehe oben) — die netzfreundliche PV-Strategie (Export-Halt) nutzt weiterhin ausschließlich die Tagesprognose.

---

## Erweiterte Sensoren für bilanzbasierte Kostenberechnung

Diese Felder sind **optional** und in der Einstellungsseite noch nicht als Formularfelder verfügbar. Setze sie über den Tab **config.json** (siehe Abschnitt „Rohdaten-Editoren" ganz unten). Sind sie gesetzt, verwendet miniEMS die tatsächlichen Wechselrichter-Tageswerte statt der aus Momentanleistungen hochgerechneten Werte — Details zur Berechnung siehe [Sensoren](sensors.md).

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `battery_charge_entity` | `sensor.deye8k_today_battery_charge` | Tägliche Batterieladung (kWh) laut Wechselrichter |
| `battery_discharge_entity` | `sensor.deye8k_today_battery_discharge` | Tägliche Batterieentladung (kWh) laut Wechselrichter |
| `battery_capacity_entity` | `sensor.deye8k_battery_capacity` | Batteriekapazität direkt vom Wechselrichter — ersetzt `battery_capacity_kwh`, wenn gesetzt |
| `battery_state_entity` | `sensor.deye8k_battery_state` | Batteriezustand als Enum (charging / discharging / idle) |
| `today_production_entity` | `sensor.deye8k_today_production` | Tägliche PV-Bruttoproduktion (kWh) — für die Wechselrichter-Wirkungsgrad-Berechnung |
| `today_losses_entity` | `sensor.deye8k_today_losses` | Tägliche Wechselrichterverluste (kWh) |
| `power_losses_entity` | `sensor.deye8k_power_losses` | Momentane Verlustleistung (W) — für die Live-Wirkungsgrad-Anzeige |

---

## Erweiterte Kostenparameter

Ebenfalls nur über **config.json** editierbar:

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `daily_base_price_eur` | `0.0` | Fixer Grundpreis (€/Tag), der zusätzlich zu den Energiekosten aufgeschlagen wird — z. B. der monatliche Grundpreis deines Stromvertrags, umgelegt auf den Tag |
| `avg_discharge_tariff_eur_kwh` | `0.0` | Durchschnittlicher Bezugstarif für die ROI-Berechnung der Batterieentladung (€/kWh). `0` = automatisch aus den drei Preis-Tarifstufen abgeleitet |

---

## Geräteerkennung (Vorschau)

Der Tab **Devices** im Dashboard zeigt, welche Geräte und Entitäten Home Assistant kennt
(Geräte-Registry und Energie-Dashboard) und wie miniEMS die einzelnen Rollen (PV-Leistung,
Batterie-SoC, Netzladeschalter, …) daraus binden *würde*. Diese Auflösung speist heute noch
nicht die tatsächliche Steuerung — die 30 Entitäts-Felder oben bleiben die einzige Quelle,
die wirklich verwendet wird. Details zur geplanten vollen Auflösung:
[Roadmap „Geräteprofile"](../technical/geraeteprofile.md).

Zwei Felder bereiten das vor, ebenfalls nur über **config.json** editierbar:

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `entity_overrides` | `{}` | Bindet einzelne Rollen manuell an eine Entität, `"<klasse>.<rolle>": "entity_id"` (z. B. `{"inverter.pv_power": "sensor.meine_pv"}`). Schlägt in der Devices-Auflösungsvorschau jede automatisch erkannte Bindung — auch die aus den 30 Entitäts-Feldern oben abgeleitete. |
| `device_detection_enabled` | `false` bei einem Update, `true` bei einer echten Neuinstallation | Schaltet noch nichts in der Steuerung — Vorbereitung für eine spätere Version, in der die Auflösung die Laufzeitkonfiguration tatsächlich speist. |

---

## Rohdaten-Editoren (config.json / options.json)

Über die Tabs **config.json** und **options.json** im Dashboard lässt sich die komplette Konfiguration direkt als JSON bearbeiten und speichern — nützlich für Felder, die (noch) kein eigenes Formularfeld in den **Einstellungen** haben (siehe die beiden Abschnitte oben).

| Tab | Datei | Zweck |
|---|---|---|
| **config.json** | `/data/config.json` | Von miniEMS selbst geschriebene, persistente Werte. Hat Vorrang vor `options.json`, wenn ein Wert vom eingebauten Standard abweicht. Für dauerhafte Änderungen empfohlen. |
| **options.json** | `/data/options.json` | Vom HA-Supervisor verwaltet. Kann bei einer Add-on-Neukonfiguration oder einem Schema-Update überschrieben werden — hier gemachte Änderungen sind also nicht dauerhaft sicher. |

Beide Editoren validieren das JSON vor dem Speichern (Button „Reformat JSON" zum Prüfen/Formatieren) und starten das Add-on nach dem Speichern automatisch neu, damit die neue Konfiguration geladen wird.
