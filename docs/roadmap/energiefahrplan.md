---
revision_date: 2026-08-27
---

# Energiefahrplan — netzdienliches Laden für Batterie und E-Auto

!!! info "Status: Planung, nichts umgesetzt außer den vorbestehenden Bausteinen"
    Dieses Dokument führt die bisherige Roadmap „Netzdienliches Laden verbessern"
    (Inhalt vollständig hier aufgegangen, Datei gelöscht) mit einer neuen Idee zusammen:
    statt einzelner, lose verbundener Prüfungen (Fensterwahl, Wirtschaftlichkeits-Gate,
    Peak-Zeitpunkt) einen
    expliziten **Tagesfahrplan** berechnen — und die Architektur von Anfang an so bauen, dass
    ein zweites ladbares Gerät (E-Auto) später dazukommen kann, ohne den Kern umzubauen.

    | Baustein | Stand |
    |---|---|
    | Schritt 1 — Attribut-Zugriff | ✅ umgesetzt in v2.0.4 |
    | V1 — Ladeleistung strecken | offen; braucht die Frist aus V2 |
    | V2 — PriceCurve | Modul gebaut und getestet; Fensterwahl bewusst nicht verdrahtet |
    | V3 — Export-Halt auf den echten PV-Peak-Zeitpunkt | offen; V3a (Peak-Sensoren) und V3b (volle Kurve) |
    | **Gelernte Ladeleistung** — SoC-gebuckerte Historie statt Konfigurationswert | offen |
    | V4 — Wirtschaftlichkeits-Gate | ✅ umgesetzt in v2.0.4 |
    | V5 — Zweistufigkeit als Prinzip | offen |
    | **Energiefahrplan** — Tagesbilanz statt Einzelchecks | offen, dieses Dokument |
    | **ChargeTask** — Batterie/EV-Ladeplan getrennt, gemeinsame Ressource | offen, Architektur-Vorschlag |
    | **Prioritätsschalter** Batterie ↔ E-Auto (GUI + HA-Switch) | offen |
    | **E-Auto-Lader** als leere Hülle | offen — bewusst kein echtes Gerät, nur die Struktur |
    | **Preisquelle a)** Day-Ahead-Börsenpreis | offen — `PriceCurve` hat den Stub schon, Parsing fehlt |
    | **Preisquelle b)** Zeitgesteuerte Mini-Tabelle in der Config | offen — neue Konstruktormethode + UI-Widget |
    | Dashboard-Anzeige des Tagesfahrplans | offen |

    Nicht Teil der V-Stufen, aber dort entstanden: fehlende Hysterese im Netzlade-Pfad
    (behoben) und der aus der Historie hergeleitete Entladetarif.

## Zielbild: vom Einzeltick zum Tagesplan

miniEMS trifft heute jede Entscheidung **pro Tick, isoliert**: `_should_grid_charge()` fragt
„ist es jetzt günstig", `_should_hold_pv_charge()` fragt unabhängig davon „reicht die
Restprognose". Beide kennen nicht den jeweils anderen Blick auf den Tag, und keine kennt ein
zweites ladbares Gerät. Das ist für **eine** Batterie an **einem** Wechselrichter lange gut
gegangen — aber miniEMS bewegt sich zunehmend Richtung generisches EMS-Add-on (vgl.
[v3.0 – Geräteprofile](v3.0-geraeteprofile.md), die genau das für die Hardware-Seite tut). Die
Planungs-Seite braucht dieselbe Verallgemeinerung: nicht „die eine Batterie", sondern
„irgendwie viele ladbare Geräte, die sich denselben Tag, dieselbe PV-Prognose und denselben
Tarif teilen".

Dieses Dokument schlägt vor:

1. Einen **Energiefahrplan** — eine explizite, aber ungespeicherte Tagesbilanz aus Verbrauch,
   PV-Prognose und Preis (Abschnitt „Der Energiefahrplan").
2. Eine **`ChargeTask`-Abstraktion**, damit Batterie und (künftig) E-Auto je einen eigenen
   Ladeplan gegen dieselbe Ressource (billige Fenster, PV-Überschuss) rechnen, mit einem
   **Prioritätsschalter** als Schiedsrichter (Abschnitt „Mehrere Ladeaufgaben").
3. Den **E-Auto-Lader zunächst als leere Hülle** — Struktur und Schalter existieren, aber kein
   echtes Gerät ist angebunden (Abschnitt „E-Auto-Lader als leere Hülle").
4. Zwei neue **Preisquellen** — Day-Ahead-Börsenpreis und eine manuell gepflegte Zeittabelle —
   damit miniEMS nicht mehr an die Attributform einer bestimmten Tarif-Integration gebunden ist.

Der Rest des Dokuments übernimmt zunächst die bestehende Analyse (evcc-Vergleich, V1–V5) fast
unverändert — sie bleibt die fachliche Grundlage — und baut die neuen Abschnitte darauf auf.

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

## Bausteine aus der bisherigen Analyse (V1–V5)

Nach Nutzen/Aufwand sortiert. Diese fünf Punkte bleiben die fachliche Grundlage — der
Energiefahrplan weiter unten führt sie zusammen, ersetzt sie aber nicht.

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
    Zulässigkeitsprüfung ist der offene Teil — der Energiefahrplan weiter unten
    beantwortet sie über `deadline` je `ChargeTask`.

    Das Modul ist deshalb fertig, getestet und ungenutzt. Eine halb durchdachte
    Regel in den Steuerpfad einer echten Batterie zu hängen, wäre der falsche
    Kompromiss.

### V3 — Export-Halt auf den echten PV-Peak-Zeitpunkt statt hartkodierter Stunde

`pv_charge_backstop_hour = 14` ist eine hartkodierte Stunde, die den tatsächlichen
Tagesgang ignoriert. Zwei Datenquellen können das ersetzen — mit unterschiedlichem
Aufwand und unterschiedlicher Reichweite.

#### V3a — dedizierte Peak-Zeitpunkt-Sensoren (leichtgewichtig)

Seit [Sensor-Staleness](../technical/sensor-staleness.md) sind zwei zusätzliche
Solcast-Entities angebunden (`solcast_peak_time_today_entity`,
`solcast_peak_time_tomorrow_entity`, Klasse (d) — einmal täglich geschrieben, per
`is_stale_daily()` geprüft), deren Zustand direkt der gesuchte Zeitpunkt ist: keine
Kurve, kein Parsing, kein zusätzlicher Attribut-Zugriff über Schritt 1 hinaus.

**Zeitbedarf für die Ladung**

```
P_charge_kw  = P_charge_learned_or_configured_kw(soc)     # s. u.
T_needed_h   = bat_kwh_free / P_charge_kw
```

`bat_kwh_free` liefert bereits `BatteryModel.free_to_charge_kwh(soc)`.
`battery_voltage_v` ist neu — dieselbe Entity, die V1 für `I_soll = P_soll /
Batteriespannung` ohnehin braucht (`sensor.deye8k_battery_voltage`, bisher kein
Config-Feld). Beide Vorschläge sollten sich dasselbe Feld teilen.

Ursprünglich stand hier schlicht `battery_max_charge_current_a × battery_voltage_v` — der
Konfigurationswert als angenommene Ladeleistung. Der folgende Abschnitt ersetzt das durch
einen belastbareren Wert.

#### Gelernte Ladeleistung statt Konfigurationswert

!!! danger "Warum der Konfigurationswert allein nicht reicht"
    `battery_max_charge_current_a` (Standard 185 A) ist, was miniEMS dem Wechselrichter
    *vorgibt* — nicht, was die Batterie tatsächlich *annimmt*. `v3.0 – Geräteprofile`
    dokumentiert bereits den konkreten Fall: das BMS meldet auf der Produktivanlage
    `BMS Max Charging Current: 65 A`, deutlich unter den konfigurierten 185 A. Rechnet
    `T_needed_h` mit dem optimistischen Konfigurationswert, wird die Ladedauer
    **unterschätzt** — und genau das höhlt die Deadline-Prüfung weiter unten
    (`latest_start = deadline − T_needed_h`) aus: Der Akku wäre dann *nicht* rechtzeitig
    voll, obwohl die Rechnung „passt genau" ausgewiesen hätte.

Ein live vom BMS gemeldetes Limit-Register (`Battery BMS Max Charging Current` laut
Solarman-Gerätedefinition) wäre der einfachste Fix — auf der Testanlage aber **nicht
vorhanden** (nur `select.deye8k_battery_bms_type` aus der BMS-Gruppe ist belegt, geprüft
per Recorder-Abfrage). Live-Beobachtung des tatsächlichen Durchsatzes (`battery_power`)
scheidet ebenfalls aus: Der Durchsatz ist zu jedem Zeitpunkt
`min(PV-Überschuss, Netzvorgabe, reale Batteriegrenze)` — solange PV-Überschuss oder eine
gedrosselte Vorgabe der Flaschenhals ist, misst man diese, nicht die Batteriegrenze
(**Censoring-Problem**). Bleibt: aus der Historie lernen, aber nur aus **unzensierten**
Beobachtungen.

**Welche Ticks sind unzensiert?** Nur `GRID_CHARGING`-Ticks: Dort gibt miniEMS heute
bereits Volllast vor (`battery_max_charge_current_a`), unabhängig von PV — der Netzanschluss
ist nie der Flaschenhals. Jeder `battery_power`-Wert während `GRID_CHARGING` ist damit eine
echte Grenzwert-Beobachtung zum jeweiligen SoC. `PV_CHARGING`/`EXPORT_SURPLUS`-Ticks bleiben
zensiert und fließen **nicht** ein.

**SoC-Buckets, nicht ein einzelner Wert.** Ladeleistung ist typischerweise nicht über den
ganzen SoC-Bereich konstant (CV-Phase nahe voll). Drei Buckets, bewusst auf **absoluten**
SoC-Grenzen definiert statt relativ zu `battery_min_soc`/`battery_max_soc`:

| Bucket | SoC-Bereich |
|---|---|
| `low` | 0–10 % |
| `mid` | 11–89 % |
| `high` | 90–100 % |

Absolut statt relativ, weil `battery_min_soc`/`battery_max_soc` vom Nutzer geändert werden
können — würden die Buckets an diesen Grenzen hängen, wäre gelernte Historie nach einer
Config-Änderung nicht mehr vergleichbar. Mit `battery_max_soc = 95` (Standard) wird der
`high`-Bucket im Normalbetrieb kaum erreicht — der Lookup unten muss das aushalten (leerer
Bucket → Fallback), nicht ignorieren. Ändert ein Nutzer `battery_max_soc` später auf 100,
füllt sich der Bucket von selbst mit neuen Daten.

**Zwei Tabellen — heutiger Live-Wert, dauerhafte Historie.** Genau das Muster, das
`cost_optimizer.py` für `peak_pv_w` schon nutzt (`_peak_pv_w[today]`, ein laufendes
Tagesmaximum, bei jedem Tick aktualisiert und per `flush_to_db()` restart-sicher
persistiert) — hier SoC-gebuckert und in eigenen Tabellen, um `daily_stats` nicht mit einer
fachlich anderen Fragestellung zu vermischen:

```sql
-- Tabelle 1: heutiger Stand, bei jedem GRID_CHARGING-Tick per UPSERT aktualisiert
CREATE TABLE IF NOT EXISTS battery_charge_capability_today (
    date          TEXT NOT NULL,
    soc_bucket    TEXT NOT NULL,        -- "low" | "mid" | "high"
    max_power_w   REAL NOT NULL DEFAULT 0,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, soc_bucket)
);

-- Tabelle 2: permanente Historie, ein Eintrag je Tag+Bucket, geschrieben vom Tagesabschluss
CREATE TABLE IF NOT EXISTS battery_charge_capability_history (
    date          TEXT NOT NULL,
    soc_bucket    TEXT NOT NULL,
    max_power_w   REAL NOT NULL,
    sample_count  INTEGER NOT NULL,
    PRIMARY KEY (date, soc_bucket)
);
```

**Bei jedem Tick, wenn `mode == GRID_CHARGING`:**

```
bucket = soc_bucket_for(soc)
UPSERT battery_charge_capability_today
  SET max_power_w  = MAX(max_power_w, battery_power_w),
      sample_count = sample_count + 1
  WHERE date = heute AND soc_bucket = bucket
```

**Beim Tageswechsel** (derselbe lokale Mitternachts-Schnitt, den
[Tageswechsel & Energiezählung](tageswechsel-energiezaehlung.md) beschreibt — kein neuer
Timer, derselbe Hook wie für `daily_stats`):

```
für jeden Bucket mit sample_count(gestern) ≥ MIN_SAMPLES_PER_DAY:
    kopiere die Zeile aus battery_charge_capability_today (Datum = gestern)
    nach battery_charge_capability_history
lösche battery_charge_capability_today-Zeilen älter als heute
```

**Laufzeit-Lookup**, analog zu `ConsumptionModel._predict_load()` (Median über die letzten
14 Tage, Mindestanzahl an Tagen mit Daten, sonst Fallback):

```
bucket   = soc_bucket_for(soc)
tage     = letzte 14 Tage aus battery_charge_capability_history
           WHERE soc_bucket = bucket AND sample_count ≥ MIN_SAMPLES_PER_DAY
wenn len(tage) ≥ MIN_DAYS (z. B. 3):
    P_charge_kw = median(max_power_w über tage) / 1000
sonst:
    P_charge_kw = battery_max_charge_current_a × battery_voltage_v / 1000   # Fallback
```

Fehlt ein Bucket (z. B. `high`, weil `battery_max_soc` ihn nie erreicht), fällt der Lookup
auf den Konfigurationswert zurück statt auf einen benachbarten Bucket zu raten — ein falsch
angenommener Wert nahe der Ladeschluss-Grenze ist riskanter als ein bekannt-konservativer
Konfigurationswert.

**Ideales Startfenster um den Peak**

Die Batterie soll beim Erreichen des Peaks noch nicht voll sein — sonst kann sie die
Spitze nicht mehr aufnehmen, und genau die soll der Export-Halt ja glätten. Der Halt
endet deshalb exakt mit dem Peak, nicht davor und nicht zentriert darauf: vor dem Peak
steigt die PV-Kurve noch, Kapazität dort freizuhalten kostet nichts (das leistet der
bestehende Export-Halt schon über die Mengen-Schätzung).

```
ideal_start = peak_time_today
ideal_end   = ideal_start + T_needed_h
```

**Prüfung gegen den spätesten Ladezeitpunkt**

```
latest_start = deadline − T_needed_h        # deadline = pv_charge_backstop_hour (neu interpretiert)
actual_start = min(ideal_start, latest_start)

hold = (now < actual_start)
```

Ersetzt im Erfolgsfall den heutigen Schwellwertvergleich (`remaining > threshold`)
vollständig — kein `pv_charge_margin_factor`, keine Hysterese-Bänder mehr nötig, weil der
Zeitpunkt exakt bekannt ist statt geschätzt.

!!! note "Fail-Safe, passend zur bestehenden Philosophie"
    Deckt sich mit dem Docstring von `_should_hold_pv_charge()` — *"EVERY failure path
    returns hold=False, so a missing or stale input can never leave the battery empty at
    nightfall"*:

    - `peak_time_today` fehlt/`is_stale_daily()` → `hold = False` (sofort laden, wie
      heute bei fehlender Prognose).
    - `now ≥ peak_time_today` (Peak schon vorbei, z. B. nach einem Neustart am
      Nachmittag) → `hold = False`, sofort nachladen statt auf morgen zu warten.
    - `battery_voltage` fehlt → Fallback auf die heutige Mengen-Schätzung statt hartem
      Fehler.

#### V3b — volle 48-Punkte-Kurve (`detailedForecast`)

Die ursprüngliche, allgemeinere Idee: `detailedForecast` (48 × `period_start` +
`pv_estimate`) liegt ebenfalls an der Solcast-Entity. Daraus wäre der reale Zeitpunkt
ableitbar, ab dem die Restprognose den Bedarf nicht mehr deckt — nicht nur der eine
Peak-Zeitpunkt, sondern der ganze Tagesgang, inklusive Wolkeneinbrüchen und mehrerer
lokaler Maxima. Schwerer zu bauen (Kurven-Parsing statt zwei fertige Zeitstempel), aber
allgemeiner. V3a ist der pragmatische erste Schritt mit den Sensoren, die bereits
angebunden sind; V3b bliebe eine mögliche spätere Verfeinerung.

Konzeptionell ist beides unser Gegenstück zu evccs `attenuate_feedin_peaks`.

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

    Eine Plausibilitätsuntergrenze verwirft jeden hergeleiteten Wert unterhalb der
    Billigschwelle, statt das Gate damit zu verschärfen.

Beide Zutaten sind schon da und werden nur nicht für Entscheidungen genutzt:

- `avg_discharge_tariff_eur_kwh` (Config, heute nur ROI-Anzeige in `cost_optimizer.py:332-340`)
- `sensor.miniems_heutiger_wechselrichter_wirkungsgrad` (bereits berechnet)

### V5 — Zweistufigkeit als Prinzip übernehmen

Heute vermischt `pv_charge_margin_factor` Wirtschaftlichkeit und Netzdienlichkeit in einer Zahl. Sauberer, evccs Trennung folgend:

- **Stufe 1 (Kosten):** *Ob* und *wie viel* geladen wird — V2 + V4.
- **Stufe 2 (Netzdienlichkeit):** *Wie* die Energie im Fenster verteilt wird — V1 + V3. Innerhalb eines Preisfensters kostenneutral; wo sie doch Geld kostet, mit explizitem Budget begrenzt.

## Der Energiefahrplan — Bausteine zu einer Tagesbilanz zusammenführen

V1/V2/V4 beantworten je für sich „wann", „ob es sich lohnt" und „wie schnell". Was fehlt, ist
die verbindende Frage: **wie viel Energie muss heute Nacht überhaupt aus dem Netz kommen,
damit die Batterie den morgigen Bedarf übersteht, bevor die PV wieder übernimmt?** Das
beantwortet heute nur ein einzelner, reaktiver Zweig in `_should_grid_charge` (Zeile 543 ff.,
der Tomorrow-Fallback) — still, spät (erst wenn `remaining_today` schon erschöpft ist) und
ohne, dass das Ergebnis irgendwo sichtbar wird.

**Vorschlag:** dieselbe Frage explizit und proaktiv stellen, als Tagesbilanz:

```
deficit_kwh = max(0, bat_kwh_free_now − predicted_pv_tomorrow_kwh × pv_charge_margin_factor)
```

Bewusst dieselbe Formel wie im heutigen Tomorrow-Fallback — nur vorgezogen und sichtbar
gemacht statt in einem `else`-Zweig versteckt.

Ist `deficit_kwh > 0`, wird daraus eine Fensterwahl:

```
kandidaten = price_curve_tomorrow.windows_before(deadline)
sortiere kandidaten nach rate_eur_kwh aufsteigend
fülle billigste zuerst, bis Σ(fenster_dauer × P_charge_kw) ≥ deficit_kwh
→ Ergebnis: eine Menge von (start, ende)-Fenstern
```

`deadline` ist V3as `peak_time_tomorrow` (oder, ohne V3a, `grid_charge_dark_end_hour`) — die
Batterie muss vor der morgigen PV wieder aufnahmefähig sein. Innerhalb jedes gewählten
Fensters übernimmt V1 (Ladeleistung strecken statt Volllast).

!!! note "Tagesgranularität, bewusst nicht stundenweise"
    `predicted_load_tomorrow_kwh` (`ConsumptionModel.predict()`) ist ein Tagestotal, keine
    Stundenkurve — miniEMS hat keine Datenquelle für „wie verteilt sich der Verbrauch über
    den Tag". Eine echte stundenweise Lastverteilung bräuchte entweder HA-Recorder-Statistics
    (neue Abhängigkeit) oder wäre nur für die PV-Seite über V3b lösbar, nicht für den
    Verbrauch. Die Tagesbilanz beantwortet „muss heute Nacht überhaupt geladen werden" — nicht
    „wie genau verteilt sich das". Feinere Auflösung bliebe ein separater, späterer Schritt.

### Trigger: stündlich statt einmal täglich

Statt die Bilanz nur einmal zu berechnen (z. B. wenn Day-Ahead-Preise für morgen
veröffentlicht werden, üblicherweise gegen 13 Uhr), **stündlich neu rechnen**. Gründe:

- Ein fester Tages-Trigger ist ein zusätzlicher Zustand, der verwaltet werden müsste
  („wurde heute schon geplant?"), und widerspricht der Selbstkorrektur-Philosophie aus V1
  (jeden Tick neu rechnen statt Fahrplan speichern).
- Stündlich ist grob genug, um keine Rechenlast zu erzeugen — Preis- und PV-Prognosen ändern
  sich nicht im 30-s-Tick-Rhythmus des EMS-Loops (dasselbe Argument wie in
  [Netzampel-APIs](netzampel-apis.md): 15–60 Minuten Poll-Intervall reicht für
  Erzeugungs-/Lastprognosen) — und fein genug, um eine untertägig aktualisierte
  Solcast-Prognose oder eine neu veröffentlichte Day-Ahead-Preisreihe binnen einer Stunde
  aufzunehmen, statt bis zum nächsten Kalendertag zu warten.
- Kein gespeicherter Fahrplan nötig: `deficit_kwh` und die Fensterwahl bleiben eine reine
  Funktion des aktuellen Zustands (SoC, Prognosen, Preiskurve) — die stündliche Neuberechnung
  ersetzt nur die *Anzeige* (siehe Dashboard-Abschnitt unten), nicht die Steuerentscheidung
  selbst, die weiterhin jeden 30-s-Tick läuft.

## Mehrere Ladeaufgaben — Batterie und E-Auto rechnen getrennt

Statt `deficit_kwh` fest an „die eine Batterie" zu binden, eine kleine Aufgaben-Abstraktion,
gegen die **jedes** ladbare Gerät seinen eigenen Plan rechnet:

```
ChargeTask:
  device_id       # "battery" heute, "ev_charger" als leere Hülle (s. u.)
  needed_kwh       # deficit_kwh bei der Batterie; Ziel-SoC minus Ist beim E-Auto
  max_charge_kw
  deadline          # peak_time_tomorrow bei der Batterie; frei konfigurierbare
                     # Abfahrtszeit beim E-Auto — andere Art von Frist, gleiche Struktur
```

Jedes Gerät berechnet sein eigenes `needed_kwh`/`deadline` **unabhängig** — die Batterie über
die Formel oben, ein künftiger EV-Task über Ziel-Reichweite und Nutzer-Deadline statt über
den PV-Peak. Beide passen ihre Fensterwahl an dieselben zwei gemeinsamen Ressourcen an:

- **`price_curve_tomorrow`** — dieselbe Preiskurve für alle Geräte.
- **PV-Überschuss** — wenn tagsüber mehr PV da ist, als die Batterie allein aufnehmen kann
  (Export-Halt bereits aktiv, Akku nah an `battery_max_soc`), könnte ein EV-Ladepunkt den
  Rest abnehmen, statt ihn ins Netz zu exportieren — netzdienlicher als beides einzeln.

**Zusammenführung ohne Solver:** Eine Prioritätswarteschlange, sortiert nach `deadline`
(dringendste zuerst), bei der jeder `ChargeTask` sich aus den noch freien
Fensterkapazitäten bedient und dem nächsten Task die bereits verplante Kapazität eines
Fensters wegnimmt. Bei **gleicher** Dringlichkeit oder wenn beide Geräte um dieselbe
begrenzte PV-Überschuss-Leistung konkurrieren, entscheidet der Prioritätsschalter (nächster
Abschnitt). Kein MILP — konsistent mit der bereits dokumentierten Haltung weiter unten
(„Was wir bewusst nicht übernehmen").

## Prioritätsschalter Batterie ↔ E-Auto

Zwei gleichberechtigte Bedienwege für dieselbe Einstellung, wie an anderer Stelle im Projekt
schon üblich (Config-Feld **und** HA-Entity für dieselbe Sache):

1. **GUI des Add-ons** — ein Config-Feld `charge_priority: "battery" | "ev"` in den
   Einstellungen, Standard `"battery"` (sicherer Default: solange kein E-Auto real
   angebunden ist, ändert sich am heutigen Verhalten nichts).
2. **Schalter in der HA-Integration** — ein neues `switch`-Entity
   (`switch.miniems_priority_ev`), damit **HA-Automatisierungen** die Priorität umschalten
   können (z. B. „E-Auto hat Vorrang, wenn es an der Wallbox hängt und `car.plugged_in` an
   ist" — eine Automatisierung, die miniEMS selbst nicht kennen muss).

!!! warning "Neue Schreibrichtung — heute nicht vorhanden"
    Die Begleit-Integration (`miniems/rootfs/usr/bin/integration/`) ist bisher **nur lesend**:
    `PLATFORMS = ["sensor"]`, der `MiniEMSCoordinator` pollt ausschließlich `/api/status`. Ein
    Schalter, den HA umlegen kann, braucht eine neue Schreibrichtung zurück zum Add-on. Die
    bestehende `POST /api/config` scheidet dafür aus — sie schreibt `config.json` und löst
    einen **Add-on-Neustart** aus (`web_server.py:234`), viel zu schwer für ein Live-Umschalten
    der Priorität. Nötig ist ein neuer, leichtgewichtiger Endpunkt (z. B.
    `POST /api/priority`), der nur den In-Memory-Zustand des laufenden EMS-Prozesses ändert,
    plus ein neues `switch.py` in der Integration, das ihn aufruft. Die GUI-Einstellung und der
    HA-Schalter müssen sich gegenseitig spiegeln — welche Seite bei einem Konflikt gewinnt
    (zuletzt geschrieben?), ist eine offene Frage.

## E-Auto-Lader als leere Hülle

Explizit **kein** echtes Gerät in diesem Schritt — nur die Struktur, die später ein reales
EV-Profil aufnehmen kann, ohne den Kern nochmal umzubauen:

- `ChargeTask` unterstützt bereits `device_id != "battery"`.
- `charge_priority` und der Schalter existieren und funktionieren end-to-end.
- Ein neues Config-Feld `ev_charger_enabled: bool = False` — bleibt aus, solange kein Gerät
  angebunden ist; `ChargeTask` für `ev_charger` wird dann gar nicht erst erzeugt.
- Das Dashboard zeigt einen EV-Slot (Abschnitt unten), der bei `ev_charger_enabled = False`
  leer/ausgegraut bleibt statt zu verschwinden — sichtbare Vorbereitung statt verstecktem
  Totzweig.

**Bewusste Abgrenzung zu [v3.0 – Geräteprofile](v3.0-geraeteprofile.md):** Dessen
`devices`-Slots (`inverter`, `battery`) lösen auf, welche Entity welche Rolle **am selben
Wechselrichter-Ökosystem** erfüllt. Ein E-Auto-Lader ist kategorial etwas anderes — ein
eigenständiges steuerbares Gerät mit eigener Integration (Wallbox, z. B. go-eCharger, OCPP,
Easee), kein Slot am Deye. Er bräuchte ein **eigenes** Profil-Konzept, parallel zu, aber
unabhängig von `profiles/deye_p3.yaml` — das ist explizit **nicht** Teil dieses Schritts,
nur als künftiger Anschlusspunkt vermerkt.

!!! danger "Widerspruch zur bisherigen Fassung dieses Dokuments"
    Die ursprüngliche Fassung (unter dem gelöschten Titel „Netzdienliches Laden verbessern")
    listete unter „Was wir bewusst nicht übernehmen" ausdrücklich: *„Mehrbatterie-/
    EV-Zielmodellierung. Kein Anwendungsfall hier."* Das war zum damaligen Zeitpunkt richtig
    und ist jetzt überholt — miniEMS bewegt sich Richtung generisches EMS-Add-on, und die
    `ChargeTask`-Struktur ist genau dafür der erste Schritt. Die Zeile ist unten entsprechend
    gestrichen, nicht stillschweigend.

## Preisquellen

**a) Börsenpreis (Day-Ahead)**

`PriceCurve.from_entity()` hat die Weiche dafür schon eingebaut, aber unbenutzt: `rates[]`/
`unit_rate_forecast[]` werden erkannt, aktuell aber nur geloggt („market-tariff parsing is
not implemented yet") und verworfen (`price_curve.py:66-71`). Das ist die natürliche Quelle
für Tibber-/aWATTar-/Nordpool-artige Sensoren, die den kompletten nächsten Tag auf einen
Schlag liefern — passt strukturell am besten zum Energiefahrplan, weil eine Day-Ahead-Auktion
ohnehin einmal täglich den ganzen Tag freigibt.

**b) Zeitgesteuert über Mini-Tabelle in der Config**

Neue Konstruktormethode `PriceCurve.from_config(windows)`, gespeist aus einer kleinen Tabelle
in den Einstellungen (Start, Ende, Preis je Zeile) statt aus einer HA-Entity. Das ist der
eigentliche „generisches EMS"-Schritt: `PriceCurve.from_entity()` funktioniert heute **nur**,
weil die Octopus-Germany-Entity zufällig `timeslots[].activation_rules[]` trägt — jeder
andere statische Tag/Nacht-Tarif ohne genau diese Attributform liefert `None`, und das System
fällt still auf den flachen Schwellwert zurück. Eine manuell gepflegte Tabelle macht miniEMS
unabhängig von der Attributform einer bestimmten Integration.

UI-seitig wäre das ein neues Widget-Muster: `settings.html` hat bisher nur flache
Key-Value-Felder (`buildField()`, ein `<input>` pro Config-Key) — eine wiederholbare Zeile
(Start/Ende/Preis, hinzufügen/entfernen) wäre die erste Tabelle dort.

**Priorität, wenn mehreres konfiguriert ist:** `from_entity()` (a) → `from_config()` (b) →
`cheap_rate_threshold_eur` (heutiges Verhalten) als letzter Fallback — dasselbe
Schichtungsmuster wie überall sonst im Projekt (Solcast, Dunkelfenster, …).

## Dashboard: Anzeige des Tagesfahrplans

Ein neuer Bereich im Ingress-Dashboard, analog zum bestehenden Warnbanner, aber informativ
statt warnend: für jedes aktive `ChargeTask` (heute: nur Batterie) die geplanten
Ladefenster, die erwartete Fertigstellungszeit und die geschätzten Kosten des Netzladens. Bei
`ev_charger_enabled = False` ein sichtbarer, ausgegrauter EV-Slot statt keinem Hinweis auf die
Möglichkeit. Läuft rein reaktiv (heutiges Verhalten, kein `deficit_kwh` ermittelbar — z. B.
fehlende Solcast-Daten), zeigt der Bereich das transparent an, statt eine erfundene Zahl zu
zeigen — dieselbe Zurückhaltung wie beim Warnbanner heute.

Aktualisiert sich mit dem stündlichen Trigger oben, nicht mit jedem 30-s-Tick — die Anzeige
ändert sich ohnehin nur, wenn sich Preis- oder PV-Prognose ändern.

## Was wir bewusst *nicht* übernehmen

- **MILP/Solver im Add-on.** PuLP+CBC ist eine schwere Abhängigkeit; die Laufzeitprobleme, die evcc mit Zeitlimits, Skalierung und CBC-Bugs löst (`optimizer.py:46-67`, `757-764`), holen wir uns nicht ins Haus. Die `ChargeTask`-Warteschlange oben ist bewusst eine simple Prioritätsliste, kein Optimierungsproblem.
- **Fahrplan-Architektur mit persistiertem Zustand.** miniEMS bleibt reaktiv; die Regelschleife *ist* der Vorteil (V1 selbstkorrigierend). Der Energiefahrplan wird stündlich neu berechnet, nie als Datei/DB-Zeile gespeichert.
- **Ein generisches EV-Profilsystem in diesem Schritt.** Die leere Hülle bereitet vor, baut aber keine Wallbox-Integration.

## Umsetzungsreihenfolge

| Schritt | Inhalt | Aufwand | Nutzen |
|---|---|---|---|
| 1 | ✅ **Attribut-Zugriff** in `ha_ws_client` (`get_state_attribute()`) — **umgesetzt in v2.0.4** | klein | Freischalter |
| 2 | **V1 Peak-Strecken** — kostenneutral, unmittelbar netzdienlich | mittel | hoch |
| 3 | **V2 PriceCurve** — Modul gebaut und getestet in v2.0.4; Fensterwahl noch nicht verdrahtet | mittel | hoch |
| 4 | ✅ **V4 Wirtschaftlichkeits-Gate** — **umgesetzt in v2.0.4** | klein | mittel |
| 5 | **V3a Peak-Sensoren** ersetzt `pv_charge_backstop_hour` — Sensoren bereits angebunden | klein–mittel | hoch |
| 5b | **Gelernte Ladeleistung** — SoC-gebuckerte Historie statt Konfigurationswert für `P_charge_kw` | mittel | hoch, macht V3a/Energiefahrplan-Deadlines belastbar |
| 6 | **Energiefahrplan** — `deficit_kwh` proaktiv + Fensterwahl, stündlicher Trigger | mittel | hoch |
| 7 | **`ChargeTask`-Abstraktion** — Batterie als erster/einziger Task, Struktur für weitere | klein | Freischalter für EV |
| 8 | **Preisquelle a) Day-Ahead** — `rates[]`/`unit_rate_forecast[]` parsen | mittel | hoch, sobald Nutzer Tibber/aWATTar haben |
| 9 | **Preisquelle b) Mini-Tabelle** — `PriceCurve.from_config()` + UI-Widget | mittel | hoch für Nicht-Octopus-Nutzer |
| 10 | **Dashboard-Fahrplananzeige** | klein–mittel | Transparenz, Vertrauen in die Automatik |
| 11 | **Prioritätsschalter** — Config-Feld + neues `switch.py` + Schreib-Endpunkt | mittel | Voraussetzung für EV |
| 12 | **E-Auto-Lader als leere Hülle** — `ev_charger_enabled`, EV-Slot im Dashboard, kein echtes Gerät | klein | sichtbare Vorbereitung |
| 13 | **V3b volle PV-Kurve** — Verfeinerung von V3a, nur bei Bedarf | mittel | mittel |

Alle Schritte einzeln nutzbar und für sich abschaltbar (wie `pv_export_priority_enabled`),
damit ein Rückfall auf das heutige Verhalten jederzeit möglich bleibt.

## Betroffene Dateien

- `ha_ws_client.py` — `get_state_attribute()` neben `get_state_value()`.
- `price_curve.py` — Marktpreis-Parsing (`rates[]`/`unit_rate_forecast[]`) fertigstellen; neue Methode `from_config(windows)`.
- `pv_curve.py` *(neu, optional)* oder Erweiterung von `solcast_client.py` — `detailedForecast` als Zeitreihe (nur für V3b).
- `charge_task.py` *(neu)* — `ChargeTask`-Dataclass, Prioritätswarteschlange, `deficit_kwh`-Berechnung. Von `ems_controller.py` aus je Gerät aufgerufen.
- `battery_capability.py` *(neu)* — `soc_bucket_for(soc)`, Tick-Update von `battery_charge_capability_today`, Tagesabschluss-Übertrag nach `battery_charge_capability_history`, Laufzeit-Lookup für `P_charge_kw` (Median über 14 Tage, Fallback auf Konfigurationswert). Von `ems_controller.py` bei jedem Tick (Update) bzw. am Tageswechsel (Abschluss) aufgerufen.
- `store.py` — zwei neue Tabellen `battery_charge_capability_today`/`_history` (Schema s. o.); Tagesabschluss-Hook am selben lokalen Mitternachts-Schnitt wie `daily_stats`.
- `ems_controller.py` — `_should_grid_charge()` (Fensterwahl + Wirtschaftlichkeit), `_should_hold_pv_charge()` (Peak-Zeitpunkt), neuer stündlicher Trigger für den Energiefahrplan, Aufruf von `charge_task.py` und `battery_capability.py`.
- `inverter_controller.py` — `GRID_CHARGING` setzt einen **berechneten** Ladestrom statt `battery_max_charge_current_a`.
- `config_loader.py` / `const.py` — `battery_voltage_entity`, `charge_priority`, `ev_charger_enabled`, `MIN_SAMPLES_PER_DAY`/`MIN_DAYS` für die Ladeleistungs-Historie, Schalter je Stufe.
- `web_server.py` — neuer leichtgewichtiger `POST /api/priority`-Endpunkt (kein Neustart).
- `templates/settings.html` — Mini-Tabellen-Widget für Preisquelle b), Prioritäts-Auswahl, EV-Platzhalterfelder.
- `templates/dashboard.html` — neuer Fahrplan-Bereich, EV-Slot (ausgegraut ohne Gerät).
- `integration/switch.py` *(neu)* — `switch.miniems_priority_ev`; `integration/__init__.py`: `PLATFORMS = ["sensor", "switch"]`.
- Doku DE/EN + CHANGELOG.

!!! note "Berührungspunkt zur Geräteprofil-Roadmap"
    Der Punkt „Grenzen aus Entity-Attributen statt `BATTERY_MAX_CURRENT_A`" steht bereits in [v3.0 – Geräteprofile](v3.0-geraeteprofile.md). Beide Vorhaben brauchen denselben Attribut-Zugriff (Schritt 1) — der sollte einmal gebaut und von beiden genutzt werden. Ein künftiges EV-Profilsystem (s. o.) wäre ein drittes Vorhaben auf derselben Grundlage.

## Verifikation

Seit v2.0.8 existiert `miniems/tests/` (Pytest, `--cov` ≥ 90 % auf den bestehenden Modulen)
— die Punkte unten, die reine Entscheidungslogik prüfen (1, 2, 6, 7), passen als Unit-Tests
gegen `PriceCurve`/`EMSController`/`ChargeTask` direkt dorthin. Ergänzend weiterhin
Smoke-Tests via `docker exec` im Add-on-Container für alles mit echtem HA-/Wechselrichter-Zugriff.

1. **Tarifkalender-Parsing** gegen die echten `activation_rules` der Produktivanlage: Die abgeleitete 24-h-Kurve muss NIEDRIG 02–06/12–16, HOCH 18–21, STANDARD sonst ergeben — inklusive des über Mitternacht laufenden Fensters 21–02.
2. **Fensterwahl:** Simulierter Tick um 13:00 Uhr bei NIEDRIG **und** guter PV-Prognose darf **nicht** grid-charge auslösen. Um 03:00 Uhr bei leerem Akku muss es auslösen.
3. **Peak-Strecken:** Bei Bedarf *E* und Fensterrest *h* muss der gesetzte Strom ≈ `E/h/U` sein und über die Fensterdauer monoton nachgeführt werden.
4. **Selbstkorrektur:** Fenster künstlich verkürzen → der berechnete Strom muss ansteigen, bis er an `max` klemmt; danach Warnung statt stiller Unterdeckung.
5. **Wirtschaftlichkeits-Gate:** Mit `avg_discharge_tariff` unter dem Bezugspreis darf nicht geladen werden.
6. **V3a Deadline-Fallback:** `peak_time_today` künstlich so spät setzen, dass `T_needed_h` nicht mehr bis zur Deadline passt → `actual_start` muss auf `latest_start` zurückfallen.
7. **Energiefahrplan-Bilanz:** `deficit_kwh` muss bei `bat_kwh_free = 0` stets `0` sein, unabhängig von der PV-Prognose (nichts zu laden, wenn der Akku schon voll ist).
8. **`ChargeTask`-Priorität:** Zwei synthetische Tasks mit überlappendem billigstem Fenster und identischer Deadline → der Task mit `charge_priority` gewinnt die Fensterkapazität zuerst, der andere weicht auf das nächstbillige Fenster aus.
9. **Prioritätsschalter-Sync:** GUI-Änderung muss sich im HA-Switch-Zustand spiegeln und umgekehrt, ohne Add-on-Neustart.
10. **EV-Hülle No-Op:** Bei `ev_charger_enabled = False` darf kein `ChargeTask` für `ev_charger` erzeugt werden und das Dashboard darf keinen aktiven EV-Fahrplan zeigen.
11. **Censoring-Filter der Ladeleistungs-Historie:** Synthetische `PV_CHARGING`-Ticks mit hoher `battery_power` dürfen `battery_charge_capability_today` **nicht** verändern — nur `GRID_CHARGING`-Ticks zählen.
12. **SoC-Bucket-Zuordnung:** Ticks bei SoC 10 %/11 %/89 %/90 % müssen exakt in `low`/`mid`/`mid`/`high` einsortiert werden (Grenzfälle).
13. **Bucket-Fallback:** Ein Bucket ohne ausreichende Historie (z. B. `high` bei `battery_max_soc = 95`) muss `P_charge_kw` auf den Konfigurationswert zurückfallen lassen, nicht auf einen benachbarten Bucket.
14. **Tagesabschluss-Übertrag:** Nach dem lokalen Mitternachts-Schnitt muss die gestrige Zeile in `battery_charge_capability_history` stehen und `battery_charge_capability_today` für den neuen Tag bei 0 beginnen.
15. **Regressionen:** Die Fixes aus v2.0.1/2.0.2 (Tomorrow-Forecast-Fallback, Confirm/Retry, SoC-Lebenszeichen über `battery_power`) müssen unverändert grün bleiben.
16. **Abschaltbarkeit:** Alle neuen Stufen aus → Entscheidungen identisch zum heutigen Stand.

## Offene Fragen

1. **Trigger-Feinschliff:** stündlich fix, oder zusätzlich ereignisgetriggert (sobald `rates[]` für morgen erstmals nicht-leer ist, sofort neu rechnen statt bis zur nächsten vollen Stunde zu warten)?
2. **Konflikt-Auflösung beim Prioritätsschalter:** Wenn GUI und HA-Switch gleichzeitig geändert werden — zuletzt geschrieben gewinnt, oder eine Seite ist grundsätzlich führend?
3. **EV-`ChargeTask`-Felder:** `needed_kwh` bei einem E-Auto braucht Ziel-Reichweite/SoC und eine nutzergesetzte Abfahrtszeit — beides existiert in keinem heutigen Config-Feld. Bleibt bewusst offen, bis ein reales EV-Profil ansteht.
4. **PV-Überschuss-Arbitrierung zwischen Batterie und EV:** Die Prioritätswarteschlange oben ist für Preisfenster spezifiziert; wie sie sich auf gleichzeitigen PV-Überschuss (Export-Halt-Situation) überträgt, ist noch nicht durchdacht.
5. **Retention von `battery_charge_capability_history`:** Feste Anzahl Tage wie bei `event_log_retention_days` (Standard 30), oder unbegrenzt wachsen lassen (drei Zeilen pro Tag sind vernachlässigbar klein)? Eine physikalische Grenze ändert sich kaum, ältere Daten schaden also nicht — spricht eher für „unbegrenzt", aber das widerspräche dem bestehenden Retention-Muster im Projekt.
6. **`MIN_SAMPLES_PER_DAY`/`MIN_DAYS`-Werte:** Noch nicht empirisch hergeleitet (anders als z. B. `SOLCAST_DATA_MAX_AGE_SEC`, das aus gemessenen Abrufraten stammt) — sollten vor der Umsetzung anhand realer `GRID_CHARGING`-Häufigkeit auf der Produktivanlage bemessen werden, nicht geraten.
