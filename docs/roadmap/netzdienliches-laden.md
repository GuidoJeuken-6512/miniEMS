---
revision_date: 2026-08-15
---

# Netzdienliches Laden verbessern — Lehren aus dem evcc-Optimizer

!!! info "Status: teilweise umgesetzt (v2.0.4)"
    Dieses Dokument bewertet, was miniEMS von einem fremden Optimierer lernen kann.

    | Stufe | Stand |
    |---|---|
    | Schritt 1 — Attribut-Zugriff | ✅ umgesetzt in v2.0.4 |
    | V1 — Ladeleistung strecken | offen; braucht die Frist aus V2 |
    | V2 — PriceCurve | Modul gebaut und getestet; Fensterwahl bewusst nicht verdrahtet |
    | V3 — halbstündliche PV-Kurve | offen |
    | V4 — Wirtschaftlichkeits-Gate | ✅ umgesetzt in v2.0.4 |
    | V5 — Zweistufigkeit als Prinzip | offen |

    Nicht Teil der V-Stufen, aber hier entstanden: fehlende Hysterese im
    Netzlade-Pfad (behoben) und der aus der Historie hergeleitete Entladetarif.

## Context

Der [evcc-Optimizer](https://github.com/evcc-io/optimizer) (MIT, aktiv gepflegt) löst dieselbe Aufgabe wie unsere Netzlade-Entscheidung, aber als **Mixed-Integer-Linear-Program über einen Horizont** statt als greedy Schwellwert-Prüfung pro Tick. Bewertet wurde, was davon miniEMS **in der eigenen App** verbessern kann — der Optimizer wird bewusst *nicht* als Dienst eingebunden.

Ergebnis vorweg: Die wertvollsten Verbesserungen brauchen **keinen Solver**. Sie bestehen darin, Daten zu nutzen, die Home Assistant bereits liefert und die miniEMS heute ignoriert.

### Der zentrale Befund

!!! success "Schritt 1 umgesetzt in v2.0.4"
    `HAWebSocketClient.get_state_attribute(entity_id, attribute)` existiert seit v2.0.4.
    Der Befund unten beschreibt den Zustand **davor** und bleibt als Begründung stehen.
    Genutzt wird der Zugriff bisher von keiner Entscheidung — das ist Sache von V2/V3.

    Ebenfalls in v2.0.4 behoben, wenn auch nicht Teil der V-Stufen: Das Netzladen
    hatte **keine Hysterese**. `_should_hold_pv_charge` hat mit
    `pv_charge_hysteresis_frac` eine asymmetrische Schwelle, `_should_grid_charge`
    endete dagegen in einem nackten Vergleich, gedämpft nur durch `mode_dwell_sec`.
    Da sich `remaining` um bis zu 0,47 kWh je 5-Minuten-Intervall bewegt (gemessen),
    konnte ein Akku nahe dem Auslösepunkt pendeln. Jetzt gilt dasselbe Band in
    beiden Pfaden.

**miniEMS liest nirgends Entity-Attribute.** `grep -rn '\["attributes"\]' *.py` findet **null** Treffer. `HAWebSocketClient` cacht in `_state_cache[eid]` das vollständige State-Dict, aber der einzige Zugriffspfad ist `get_state_value()` → `float(state)`. Damit bleibt ungenutzt:

| Daten in HA (live geprüft) | liegt in | heute genutzt |
|---|---|---|
| Vollständiger Tarifkalender (`activation_rules`) | Preis-Entity | ❌ nur der aktuelle Skalar |
| Halbstündliche PV-Kurve (`detailedForecast`, 48 Werte) | Solcast-Entity | ❌ nur `remaining_today` als Skalar |
| `min`/`max`/`step` der Stellglieder | Number-Entities | ❌ hartkodiert als `BATTERY_MAX_CURRENT_A` |

### Der Tarif ist vollständig bekannt — keine Prognose nötig

!!! warning "Korrektur der Datenquelle (16.08.2026)"
    Diese Seite schrieb ursprünglich, der Kalender liege in `activation_rules` der
    **Preis-Entity**. Beim Bau von V2 stellte sich beides als ungenau heraus:

    - Die konfigurierte `electricity_price_entity` ist auf dieser Anlage
      `sensor.deye8k_current_electricity_price` — ein Template-Sensor, der **nur**
      `unit_of_measurement` und `friendly_name` trägt. Dort ist kein Kalender.
    - Der Kalender liegt auf der Entity der Octopus-Germany-Integration
      (`sensor.octopus_a_10fc0646_electricity_price`), und dort **nicht** auf
      oberster Ebene, sondern verschachtelt: `timeslots[].activation_rules[]`.

    Die Entity trägt außerdem `active_timeslot_from` / `active_timeslot_to` — das
    Ende des laufenden Fensters also fertig, ohne Parsing. Genau der Wert, den V1
    braucht.

    `rates[]` und `unit_rate_forecast[]` existieren, sind aber leer
    (`rates_count = 0`) — wie für einen ToU-Vertrag erwartet.

Aus `timeslots[].activation_rules[]` der Octopus-Entity:

| Stufe | Preis | Fenster |
|---|---|---|
| NIEDRIG | 27,44 ct | **02–06** und **12–16** Uhr |
| STANDARD | 34,44 ct | 06–12, 16–18, 21–02 Uhr |
| HOCH | 39,44 ct | 18–21 Uhr |

Das ist ein deterministischer 24-h-Preisverlauf. Die heutige Logik (`cost_optimizer.is_cheap_rate()`, Zeile 350: `price < cheap_rate_threshold_eur`) reduziert ihn auf ein Ja/Nein für *jetzt*.

!!! warning "Konkrete Folge"
    Beide NIEDRIG-Fenster lösen gleichermaßen aus — auch das um **12–16 Uhr, mitten in der PV-Produktion**. Netzbezug zu dieser Zeit ist doppelt schädlich: wirtschaftlich unnötig und netztechnisch gegenläufig (Bezug, während alles einspeist). Der Forecast-Vergleich in `_should_grid_charge` bremst das nur indirekt über `remaining * pv_charge_margin_factor`; bei großem Akku und mäßiger Prognose greift er nicht zuverlässig.

## Was evcc konzeptionell besser macht

1. **Horizont statt Momentaufnahme.** Nicht „ist es jetzt günstig", sondern „welches sind die günstigsten Stunden, in denen ich den Bedarf decken kann".
2. **Strafterm auf das Horizont-Maximum, nicht auf die Leistung** (`optimizer.py:462`). Der Kommentar dort ist die ganze Idee: *„the penalty sits on the horizon maximum instead of on charge power, so the optimizer spreads charging at partial power over several time steps rather than running one step at full power."*
3. **Zweistufige Lösung** (`_solve_preferences`, Zeile 715 ff.): Erst reine Kosten, dann Präferenzen unter der Nebenbedingung, das gefundene Geld zu halten (`COST_BOUND`) — mit explizitem `preference_budget`, wie viel Netzdienlichkeit kosten *darf*. Kosten und Komfort werden nie vermischt.
4. **Wert der gespeicherten Energie** (`p_a`) statt Schwellwert: Laden lohnt sich, wenn die eingelagerte kWh mehr wert ist als der Bezugspreis — nicht, wenn der Preis unter einer Zahl liegt.

## Konkrete Verbesserungen für miniEMS

Nach Nutzen/Aufwand sortiert.

### V1 — Ladeleistung über das Fenster strecken (das eigentliche „netzdienlich")

Heute setzt `GRID_CHARGING` den Ladestrom auf `battery_max_charge_current_a` (185 A ≈ Volllast) und lädt, bis der Akku voll ist oder der Preis steigt. Das erzeugt genau die Lastspitze, die evcc bestraft.

!!! tip "Der entscheidende Punkt"
    **Innerhalb eines Fensters mit konstantem Preis ist Strecken kostenneutral.** 4 kW für 1 h kostet exakt dasselbe wie 1 kW für 4 h — es ist ein reiner Tie-Break, genau die Klasse von Entscheidung, die evcc in Stufe 2 trifft. Netzdienlichkeit ist hier **gratis**, nicht erkauft.

Die Umsetzung passt zur bestehenden Architektur: miniEMS läuft ohnehin als 30-s-Regelschleife, also **jeden Tick neu rechnen** statt einen Fahrplan zu speichern:

```
P_soll = benötigte_Restenergie / verbleibende_Fensterzeit
I_soll = clamp(P_soll / Batteriespannung, 0, max aus Entity-Attribut)
```

Das ist selbstkorrigierend: Wird das Fenster unterbrochen oder die Last höher, zieht die nächste Iteration nach. Kein Solver, kein Zustand. Die Batteriespannung liegt als `sensor.deye8k_battery_voltage` vor; ein Sicherheitspuffer (Ziel: fertig bei ~80 % der Fensterlänge) fängt Störungen ab.

### V2 — Tarifkalender lesen statt Schwellwert (Fensterwahl)

Eine `PriceCurve`-Abstraktion, die aus der Preis-Entity die Vorschau zieht, in dieser Reihenfolge:

1. `rates[]` bzw. `unit_rate_forecast[]` (Attribute existieren bereits an der Entity, aktuell leer — Markttarife wie Tibber/aWATTar füllen sie)
2. `activation_rules` → deterministischer ToU-Kalender (unser Fall)
3. Fallback: nur der aktuelle Preis → heutiges Verhalten

Damit wird aus „Preis unter Schwelle" die Frage „ist dies das günstigste Fenster, bevor die Energie gebraucht wird?". Das schließt das 12–16-Uhr-Fenster von selbst aus, weil bis dahin PV liefert.

!!! note "Warum die Fensterwahl noch nicht verdrahtet ist"
    `PriceCurve` beantwortet „ist jetzt so günstig wie irgendetwas vor der Frist?"
    (`is_cheapest_now`). Für das eigentliche Ziel — **nicht** im Fenster 12–16 Uhr
    laden, weil dann die PV liefert — reicht das nicht: 02–06 und 12–16 haben
    **denselben** Satz (27,4414 ct). „Am günstigsten" trifft auf beide zu.

    Nötig wäre „ist dies das **letzte** günstigste Fenster vor der Frist?", also ein
    bewusstes Aufschieben auf das spätere gleich teure Fenster. Aufschieben ist aber
    nur zulässig, wenn der Akku im späteren Fenster noch vollständig gefüllt werden
    kann — sonst spart man einen Cent und verliert eine Ladung. Diese
    Zulässigkeitsprüfung ist der offene Teil.

    Das Modul ist deshalb fertig, getestet und ungenutzt. Eine halb durchdachte
    Regel in den Steuerpfad einer echten Batterie zu hängen, wäre der falsche
    Kompromiss.

### V3 — Halbstündliche PV-Kurve statt Skalar

`detailedForecast` (48 × `period_start` + `pv_estimate`) liegt an der Solcast-Entity. Damit ersetzbar:

- **`pv_charge_backstop_hour = 14`** — eine hartkodierte Stunde, die den tatsächlichen Tagesgang ignoriert. Aus der Kurve ist der reale Zeitpunkt ableitbar, ab dem die Restprognose den Bedarf nicht mehr deckt.
- Der Export-Halt (`_should_hold_pv_charge`) kann den **Zeitpunkt der Einspeisespitze** treffen, statt ihn über `pv_charge_margin_factor` zu approximieren.

Konzeptionell ist das unser Gegenstück zu evccs `attenuate_feedin_peaks` — die bestehende Heuristik zielt schon richtig (Kopf für die Mittagsspitze freihalten), trifft aber unscharf.

### V4 — Wirtschaftlichkeit statt Schwellwert

`is_cheap_rate()` prüft nur `price < threshold`. Ob sich Laden *rechnet*, prüft niemand. Mit bekanntem Kalender ist der Spread bekannt: NIEDRIG 27,44 → HOCH 39,44 = 12 ct, bei ~90 % Round-Trip bleiben ~8,5 ct/kWh. Ein falsch gesetzter Schwellwert kann heute unwirtschaftlich laden.

!!! success "Umgesetzt in v2.0.4"
    `_should_grid_charge` verlangt jetzt `η × Entladetarif − Preis > 2 ct`.
    Übersprungen — **nicht** fail-closed — solange Wirkungsgrad oder Entladetarif
    unbekannt sind: Beide stammen aus akkumulierter Historie, fail-closed würde eine
    frische Installation eine Woche lang am Netzladen hindern. Das Gate ist eine
    wirtschaftliche Verfeinerung, keine Sicherheitsverriegelung.

    **`avg_discharge_tariff_eur_kwh` wird hergeleitet statt konfiguriert.** Der Wert
    stand auf `0.0` und war nie gesetzt — die eine Zahl, die beziffert, was eine
    gespeicherte kWh wert ist, existierte schlicht nicht. Jetzt:
    Σ(entladene kWh × Preis) / Σ(entladene kWh) über 7 Tage, tagesweise gecacht; ein
    konfigurierter Wert schlägt die Herleitung weiterhin.

!!! warning "Die Gewichtung ist der Kern — zwei Fehlversuche"
    Zwei einfachere Mittelwerte wurden gebaut und sind beide **der Art nach** falsch:

    | Variante | Wert auf `.59` | Marge `0,916 × Tarif − 0,2744` | Gate (2 ct) |
    |---|---|---|---|
    | lastgewichtet `load_cost/load_kwh` | 0,2942 | −0,5 ct | blockiert |
    | zeitgewichtet `avg_price_eur_kwh` | ~0,318 | +1,7 ct | blockiert |
    | **entladungsgewichtet** | ~0,38 | **+7,8 ct** | lädt |

    Beide Mittelwerte hätten das Netzladen auf der Produktivanlage vollständig
    abgeschaltet. Der Akku verdrängt aber keinen Durchschnittsverbrauch — er entlädt
    überproportional in das teure Abendfenster (HOCH 0,3944). Eine Mittelung über
    Last oder Zeit unterschätzt den Wert so stark, dass sie die Entscheidung
    umdreht.

    **Randbefund:** Die zunächst gemessenen 0,1370 €/kWh stammten aus der
    Devcontainer-Datenbank, die während der Entwicklung im Minutentakt neu gebaut
    wurde — dort deckt `load_total_kwh` (lebenszeitverankert) die Ausfallzeiten ab,
    `load_cost_eur` (pro Tick) nicht. Auf der permanent laufenden `.59` liegt
    dieselbe Kennzahl bei 0,2942 und damit im Tarifband. Die gemischte Buchungsbasis
    ist real, auf einer durchlaufenden Instanz aber eine Abweichung von Minuten.

    Eine Plausibilitätsuntergrenze verwirft jeden hergeleiteten Wert unterhalb der
    Billigschwelle, statt das Gate damit zu verschärfen.

Beide Zutaten sind schon da und werden nur nicht für Entscheidungen genutzt:

- `avg_discharge_tariff_eur_kwh` (Config, heute nur ROI-Anzeige in `cost_optimizer.py:332-340`)
- `sensor.miniems_heutiger_wechselrichter_wirkungsgrad` (bereits berechnet)

### V5 — Zweistufigkeit als Prinzip übernehmen

Heute vermischt `pv_charge_margin_factor` Wirtschaftlichkeit und Netzdienlichkeit in einer Zahl. Sauberer, evccs Trennung folgend:

- **Stufe 1 (Kosten):** *Ob* und *wie viel* geladen wird — V2 + V4.
- **Stufe 2 (Netzdienlichkeit):** *Wie* die Energie im Fenster verteilt wird — V1 + V3. Innerhalb eines Preisfensters kostenneutral; wo sie doch Geld kostet, mit explizitem Budget begrenzt.

## Was wir bewusst *nicht* übernehmen

- **MILP/Solver im Add-on.** PuLP+CBC ist eine schwere Abhängigkeit; die Laufzeitprobleme, die evcc mit Zeitlimits, Skalierung und CBC-Bugs löst (`optimizer.py:46-67`, `757-764`), holen wir uns nicht ins Haus, um eine einzelne Batterie zu steuern.
- **Mehrbatterie-/EV-Zielmodellierung.** Kein Anwendungsfall hier.
- **Fahrplan-Architektur.** miniEMS bleibt reaktiv; die Regelschleife *ist* der Vorteil (V1 selbstkorrigierend).

## Umsetzungsreihenfolge

| Schritt | Inhalt | Aufwand | Nutzen |
|---|---|---|---|
| 1 | ✅ **Attribut-Zugriff** in `ha_ws_client` (`get_state_attribute()`) — **umgesetzt in v2.0.4** | klein | Freischalter |
| 2 | **V1 Peak-Strecken** — kostenneutral, unmittelbar netzdienlich | mittel | hoch |
| 3 | **V2 PriceCurve** — Modul gebaut und getestet in v2.0.4; die Fensterwahl in `_should_grid_charge` ist bewusst noch **nicht** verdrahtet, siehe Kasten unten | mittel | hoch |
| 4 | ✅ **V4 Wirtschaftlichkeits-Gate** — **umgesetzt in v2.0.4**, inkl. aus der Historie hergeleitetem Entladetarif | klein | mittel |
| 5 | **V3 PV-Kurve** ersetzt `pv_charge_backstop_hour` | mittel | mittel |

Die Schritte 2–5 sind einzeln nutzbar und je für sich abschaltbar zu halten (wie `pv_export_priority_enabled`), damit ein Rückfall auf das heutige Verhalten jederzeit möglich bleibt.

## Betroffene Dateien

- `ha_ws_client.py` — `get_state_attribute()` neben `get_state_value()`; `_state_cache` hält die Attribute bereits.
- `price_curve.py` *(neu)* — Vorschau aus `rates[]` / `unit_rate_forecast[]` / `activation_rules`, mit Fallback auf den Skalar.
- `pv_curve.py` *(neu)* oder Erweiterung von `solcast_client.py` — `detailedForecast` als Zeitreihe; `SolcastClient` liest heute nur drei Skalare.
- `ems_controller.py` — `_should_grid_charge()` (Fensterwahl + Wirtschaftlichkeit), `_should_hold_pv_charge()` (Kurve statt fixe Backstop-Stunde), neuer Sollwert „Ladeleistung" statt „immer Volllast".
- `inverter_controller.py` — `GRID_CHARGING` setzt einen **berechneten** Ladestrom statt `battery_max_charge_current_a`; Grenzen aus dem Entity-Attribut `max` statt `const.BATTERY_MAX_CURRENT_A`.
- `config_loader.py` / `const.py` — Schalter je Stufe, Sicherheitspuffer, optionales Netzdienlichkeits-Budget.
- Doku DE/EN + CHANGELOG.

!!! note "Berührungspunkt zur Geräteprofil-Roadmap"
    Der Punkt „Grenzen aus Entity-Attributen statt `BATTERY_MAX_CURRENT_A`" steht bereits in [v3.0 – Geräteprofile](v3.0-geraeteprofile.md). Beide Vorhaben brauchen denselben Attribut-Zugriff (Schritt 1) — der sollte einmal gebaut und von beiden genutzt werden.

## Verifikation

Kein Test-Verzeichnis im Projekt; Vorgehen wie bewährt (Smoke-Tests via `docker exec` im Add-on-Container).

1. **Tarifkalender-Parsing** gegen die echten `activation_rules` der Produktivanlage: Die abgeleitete 24-h-Kurve muss NIEDRIG 02–06/12–16, HOCH 18–21, STANDARD sonst ergeben — inklusive des über Mitternacht laufenden Fensters 21–02.
2. **Fensterwahl:** Simulierter Tick um 13:00 Uhr bei NIEDRIG **und** guter PV-Prognose darf **nicht** grid-charge auslösen (heutiges Verhalten: kann auslösen). Um 03:00 Uhr bei leerem Akku muss es auslösen.
3. **Peak-Strecken:** Bei Bedarf *E* und Fensterrest *h* muss der gesetzte Strom ≈ `E/h/U` sein und über die Fensterdauer monoton nachgeführt werden; die insgesamt geladene Energie muss der Volllast-Variante entsprechen (gleiche Kosten, halbe Spitze).
4. **Selbstkorrektur:** Fenster künstlich verkürzen → der berechnete Strom muss ansteigen, bis er an `max` klemmt; danach Warnung statt stiller Unterdeckung.
5. **Wirtschaftlichkeits-Gate:** Mit `avg_discharge_tariff` unter dem Bezugspreis darf nicht geladen werden.
6. **Regressionen:** Die Fixes aus v2.0.1/2.0.2 (Tomorrow-Forecast-Fallback, Confirm/Retry, SoC-Lebenszeichen über `battery_power`) müssen unverändert grün bleiben.
7. **Abschaltbarkeit:** Alle neuen Stufen aus → Entscheidungen identisch zum heutigen Stand.
