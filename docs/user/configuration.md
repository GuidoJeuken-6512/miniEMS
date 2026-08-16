---
revision_date: 2026-08-15
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

!!! info "Ampere statt Watt"
    Die Deye-Ladungslimits sind Entitäten in **Ampere**, nicht in Watt — anders als in miniEMS-Versionen vor v2.0. Die alten `*_power_w`-Feldnamen werden beim Start automatisch auf die neuen `*_current_a`-Felder migriert; bereits gespeicherte Watt-Werte werden dabei **nicht** umgerechnet, da 1:1 keine gültige Umrechnung existiert. Prüfe die migrierten Werte einmal in den Einstellungen nach einem Update von einer Vorversion.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `battery_control_enabled` | `false` | Hauptschalter für die Wechselrichtersteuerung |
| `battery_control_simulation` | `true` | Befehle protokollieren, aber nicht senden |
| `inverter_charge_current_entity` | `number.deye8k_battery_max_charging_current` | Entität zum Setzen des Ladestrom-Limits (A) |
| `grid_charge_switch_entity` | `switch.deye8k_battery_grid_charging` | Schalter-Entität zum Aktivieren/Deaktivieren des Netzladens |
| `battery_discharging_current_entity` | `number.deye8k_battery_max_discharging_current` | Entität zum Setzen des Entladestrom-Limits (A) |
| `battery_max_charge_current_a` | `185` | Maximaler Ladestrom (A). Automatisch auf 0–350 A begrenzt. |
| `battery_max_discharge_current_a` | `185` | Maximaler Entladestrom (A). Automatisch auf 0–350 A begrenzt. |

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

Das Vorhersagemodell verwendet historische Verbrauchsdaten von Tagen mit ähnlicher Temperatur. Wenn noch keine Historie vorhanden ist, gelten temperaturbasierte Fallback-Regeln:

| Bedingung | Vorhergesagte Last |
|---|---|
| Nachttemperatur < 0 °C und Tagestemperatur < 0 °C | 30 kWh |
| Nachttemperatur < 0 °C und Tagestemperatur < 10 °C | 20 kWh |
| Nachttemperatur > 0 °C und Tagestemperatur < 15 °C | 10 kWh |

---

## Solcast PV-Prognose

[Solcast](https://solcast.com/) liefert hochgenaue Dachanlagen-PV-Prognosen. Installiere die Solcast HA-Integration und konfiguriere die Entitäten hier.

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

## Rohdaten-Editoren (config.json / options.json)

Über die Tabs **config.json** und **options.json** im Dashboard lässt sich die komplette Konfiguration direkt als JSON bearbeiten und speichern — nützlich für Felder, die (noch) kein eigenes Formularfeld in den **Einstellungen** haben (siehe die beiden Abschnitte oben).

| Tab | Datei | Zweck |
|---|---|---|
| **config.json** | `/data/config.json` | Von miniEMS selbst geschriebene, persistente Werte. Hat Vorrang vor `options.json`, wenn ein Wert vom eingebauten Standard abweicht. Für dauerhafte Änderungen empfohlen. |
| **options.json** | `/data/options.json` | Vom HA-Supervisor verwaltet. Kann bei einer Add-on-Neukonfiguration oder einem Schema-Update überschrieben werden — hier gemachte Änderungen sind also nicht dauerhaft sicher. |

Beide Editoren validieren das JSON vor dem Speichern (Button „Reformat JSON" zum Prüfen/Formatieren) und starten das Add-on nach dem Speichern automatisch neu, damit die neue Konfiguration geladen wird.
