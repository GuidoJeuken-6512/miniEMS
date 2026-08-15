---
revision_date: 2026-08-15
---

# Tageswechsel & Energiezählung — von Tageszählern auf Total-Deltas

!!! info "Status: Vorschlag"
    Dieses Dokument beschreibt einen Umbau der Energiezählung. Er ist **nicht umgesetzt**.
    Der Stand im Repository ist v2.0.3, ausgeliefert auf der Produktivanlage ist v2.0.2.
    Alle Messwerte auf dieser Seite stammen vom 14./15.08.2026 aus der Produktivanlage.

## Kontext

miniEMS bevorzugt für `grid_import_kwh`, `feed_in_kwh` und (seit v2.0.3)
`load_total_kwh` den Tageszähler des Wechselrichters gegenüber der eigenen
Tick-Akkumulation — „Quelle A" statt „Quelle B". Der Grund war und bleibt richtig: Ein
Zähler im Gerät überlebt einen Add-on-Neustart, ein Akkumulator im Add-on nicht.

Die Prüfung des Tageswechsels hat dabei einen Defekt zutage gefördert, der nicht in der
Logik von miniEMS liegt, sondern in einer stillschweigenden Annahme: **dass der
Wechselrichter denselben Tageswechsel hat wie miniEMS.** Er hat ihn nicht.

## Befund

### Die beiden Uhren laufen auseinander

miniEMS schneidet den Tag exakt auf lokaler Mitternacht — verifiziert an
`sensor.miniems_today_load_total` (v2.0.2, also Quelle B):

```
21:59:38 UTC   4.779
22:00:08 UTC   0.001      ← lokale Mitternacht = 22:00:00 UTC
```

Der Wechselrichter setzt seine Tageszähler dagegen erst **4 min 54 s später** zurück,
alle gleichzeitig aus demselben Modbus-Poll:

| Zähler | Reset | Stand davor |
|---|---|---|
| `deye8k_today_energy_import` | 22:04:54 UTC | 0,2 kWh |
| `deye8k_today_energy_export` | 22:04:54 UTC | **45,9 kWh** |
| `deye8k_today_production` | 22:04:54 UTC | 54,3 kWh |
| `deye8k_today_load_consumption` | 22:04:54 UTC | 4,9 kWh |

### Was daraus folgt

In diesem Fenster hat miniEMS bereits den neuen Tagesschlüssel, bekommt von Quelle A
aber noch den **Endstand von gestern** — und schreibt ihn per `=`-Override in den neuen
Tag (`cost_optimizer.record_tick`). Bei der Einspeisung sind das knapp fünf Minuten lang
45,9 kWh im frischen Tag, inklusive Erlös, denn `feed_in_revenue_eur` wird ebenfalls
überschrieben statt akkumuliert.

Der Fehler heilt sich um 00:04:54 selbst, und weil `flush_to_db()` per Upsert arbeitet,
bleibt kein Schaden in SQLite. Sichtbar ist er trotzdem: Fünf Minuten nach Mitternacht
zeigt das Dashboard die Werte von gestern.

### Zwei Folgeprobleme, die schwerer wiegen als der Anzeigefehler

**1. kWh und Kosten beschreiben verschiedene Zeiträume.** Die kWh-Werte stammen aus dem
Wechselrichter-Tag (00:04:54 → 00:04:54), die Kostenwerte werden pro Tick über den
miniEMS-Tag akkumuliert (00:00 → 00:00). `today_load_total_kwh` und
`today_load_cost_eur` beziehen sich also streng genommen nicht auf dasselbe Fenster.
Das ist ein Korrektheitsproblem, kein kosmetisches.

**2. Latentes Datenverlust-Risiko.** Die Schieflage geht derzeit in die harmlose
Richtung. Liefe die Wechselrichter-Uhr *vor* statt nach, würde derselbe `=`-Override in
den letzten Minuten des Tages eine 0 in **gestern** schreiben, und `flush_to_db()` würde
diese 0 als Tagesabschluss persistieren — der Tageswert wäre dauerhaft verloren. Nichts
im Code verhindert das; die Richtung der Abweichung ist reines Glück.

## Vorschlag: Total-Zähler + eigenes Delta

Statt den fremden Tagesschnitt zu übernehmen, den monoton steigenden Gesamtzähler lesen
und den Tageswert selbst bilden. Damit liegt die Tagesgrenze vollständig in miniEMS'
eigener Zeitrechnung.

### Die Total-Zähler taugen dafür — geprüft

| Zähler | Stand | Auflösung |
|---|---|---|
| `deye8k_total_load_consumption` | 9445,7 kWh | 0,1 kWh |
| `deye8k_total_energy_import` | 5013,8 kWh | 0,1 kWh |
| `deye8k_total_energy_export` | 11516,1 kWh | 0,1 kWh |
| `deye8k_total_production` | 18216,5 kWh | 0,1 kWh |

**Kein Auflösungsverlust.** Über 24 h wurden 49 Wertänderungen gezählt (45× 0,1 kWh,
3× 0,2 kWh, 1× 0,3 kWh). Der Tageszähler bewegt sich in denselben 0,1-kWh-Schritten —
es sind dieselben Registerdaten, nur anders dargestellt. Gegenprobe: Der Total-Zähler
lief über den Tag von 9440,3 auf 9445,7 (= 5,4 kWh), `today_load_consumption` stand bei
5,2 kWh.

**Über Mitternacht vollständig stetig** — kein Reset, kein Sprung:

```
21:55:00 UTC   9440.3     ← lokale Mitternacht (22:00) liegt in einer
22:23:01 UTC   9440.4        28-Minuten-Lücke ohne jede Wertänderung
22:52:44 UTC   9440.5
23:22:45 UTC   9440.6
```

Dass die Tagesgrenze in eine Lücke fällt, ist kein Problem, sondern der Normalfall: Der
Anker ist der zuletzt gesehene Wert vor lokaler Mitternacht, der Quantisierungsfehler
beträgt höchstens 0,1 kWh — also genau die Auflösung des Sensors selbst.

### Rechenweg

```
anchor[heute] fehlt?   →  anchor[heute] = total_jetzt − today_sensor_wert
today_kwh              =  total_jetzt − anchor[heute]
```

Der Bootstrap über den vorhandenen `today_*`-Sensor ist der entscheidende Kniff: Startet
das Add-on mittags neu, lässt sich der Anker rückwirkend exakt rekonstruieren, ohne dass
jemals ein Tick um Mitternacht gelaufen sein muss. Die bestehenden `today_*`-Felder
bleiben damit in der Konfiguration — ihre Rolle wechselt vom Messwert zum
Anker-Bootstrap.

### Absicherungen

1. **Zählerrücksetzung** (Firmware-Update, Gerätetausch, Modbus-Störung): Bei
   `total_jetzt < anchor` neu ankern und warnen, statt ein negatives Delta zu liefern.
   Ein monoton steigender Zähler darf nie rückwärts laufen — tut er es doch, ist das ein
   meldepflichtiges Ereignis, kein Rechenfall.
2. **Registerbreite** — unkritisch: 11516,1 kWh bei 0,1er-Auflösung sind 115 161
   Rohwert und laufen damit bereits über 32 Bit. Ein 16-Bit-Überlauf ist ausgeschlossen.
3. **Fehlender Bootstrap** (kein `today_*`-Sensor konfiguriert): Rückfall auf „erster
   gesehener Total-Wert nach Start". Das verliert die Lücke vor dem Start — dasselbe
   Verhalten wie die heutige Quelle B, also keine Verschlechterung gegenüber dem
   Status quo.

### Was sich ausdrücklich **nicht** ändert

- **Kosten bleiben tick-akkumuliert.** Sie brauchen den dynamischen Preis je Intervall,
  den kein Tages- oder Gesamtzähler liefern kann. Der Umbau betrifft ausschließlich die
  kWh-Größen.
- **Quelle B bleibt Fallback.** Ist kein Total-Zähler konfiguriert oder erreichbar, gilt
  unverändert die Tick-Akkumulation.
- **Die Tagesschlüssel-Mechanik bleibt.** `defaultdict` mit `date`-Schlüssel und
  `date.today()` funktioniert korrekt und ist geprüft; die Zeitzone ist im Container
  gesetzt (`TZ=Europe/Berlin`), der Tagesschnitt liegt lokal. Daran ist nichts zu
  reparieren.

## Umsetzungsreihenfolge

| Schritt | Inhalt | Aufwand |
|---|---|---|
| 1 | Anker-Spalten in `daily_stats` ergänzen (additive `ALTER TABLE`-Liste in `store.py`, bestehender Mechanismus) | klein |
| 2 | Config-Felder für die vier `total_*`-Entities + Migration auf neue Schemaversion | klein |
| 3 | Anker-Logik inkl. Bootstrap und Rücksetz-Erkennung in `cost_optimizer` | mittel |
| 4 | Quelle-A-Blöcke auf Delta umstellen (Netzbezug, Einspeisung, Last) | mittel |
| 5 | Wirkungsgrad-Rechnung auf `total_production` / `total_losses` nachziehen | klein |
| 6 | Doku DE/EN (`calculations.md`, `configuration.md`, `costs.md`) + CHANGELOG | mittel |

Schritt 5 ist optional trennbar: Die Wirkungsgrad-Formel nutzt `today_production` und
`today_losses` im Verhältnis zueinander, und weil beide zum selben Zeitpunkt
zurücksetzen, ist der Quotient auch heute konsistent. Der Umbau bringt dort nur
Einheitlichkeit, keine Fehlerbehebung.

## Betroffene Dateien

- `cost_optimizer.py` — `record_tick()`: die drei Quelle-A-Blöcke (Netzbezug,
  Einspeisung, Last) von `= wert` auf `= total − anchor` umstellen; neue Anker-Verwaltung
  inkl. Bootstrap; `restore_today()` um die Anker erweitern.
- `store.py` — vier Spalten in der additiven `ALTER TABLE`-Liste ergänzen; der
  Upgrade-Mechanismus für bestehende Datenbanken existiert bereits und braucht keine
  Änderung.
- `config_loader.py` — vier `total_*_entity`-Felder neben den vorhandenen
  `*_energy_entity`-Feldern; Aufnahme in `monitored_entities`.
- `migration.py` — neue `_v13_to_v14()` mit den Standard-Entities, analog zu
  `_v12_to_v13()`.
- `ems_controller.py` — Auslesen der Total-Entities und Durchreichen an `record_tick()`.
- `templates/settings.html` — vier Felder im Abschnitt „Inverter Entities".
- Doku DE/EN + CHANGELOG.

## Verifikation

Kein Test-Verzeichnis im Projekt; Vorgehen wie bewährt (Smoke-Tests via `docker exec` im
Add-on-Container, danach Rebuild und Live-Beobachtung).

1. **Delta-Grundfall:** Anker gesetzt, Total steigt um 0,3 kWh ⇒ `today_kwh` steigt um
   exakt 0,3 kWh — unabhängig davon, was der `today_*`-Sensor gerade zeigt.
2. **Bootstrap:** Anker fehlt, `total = 9445,7`, `today_sensor = 5,2` ⇒ Anker muss
   9440,5 werden und `today_kwh` sofort 5,2 liefern.
3. **Tageswechsel:** Simulierter Datumswechsel ⇒ neuer Anker = aktueller Total-Stand,
   `today_kwh` startet bei 0. Der Wert darf **nicht** kurzzeitig auf den Vortagesstand
   springen — das ist der eigentliche Regressionstest gegen den heutigen Defekt.
4. **Fremde Uhr irrelevant:** Total-Wert konstant halten, während der `today_*`-Sensor
   auf 0 zurückspringt ⇒ `today_kwh` darf sich nicht verändern.
5. **Zählerrücksetzung:** `total` unter den Anker fallen lassen ⇒ Neuankerung, Warnung
   im Log, kein negatives Delta.
6. **Persistenz:** Add-on-Neustart mitten am Tag ⇒ `today_kwh` unverändert (Anker aus
   SQLite), inklusive der Energie, die während der Ausfallzeit geflossen ist.
7. **Fenstergleichheit:** kWh und zugehörige Kosten müssen nach dem Umbau denselben
   Zeitraum abdecken — prüfbar, indem um Mitternacht beide Größen gleichzeitig auf 0
   springen.
8. **Regressionen:** Quelle B unverändert, wenn keine Total-Entity konfiguriert ist.

## Randbefund

Auf der Produktivanlage existieren `input_number.deye8k_total_load_consumption_last_state`
und `input_number.deye8k_total_energy_import_last_state` — offenbar ein früherer Anlauf zu
genau dieser Anker-Idee, außerhalb von miniEMS. Beide stehen auf `unavailable` und sind
damit funktionslos. Vor der Umsetzung zu klären: aufräumen oder bewusst als
Anker-Speicher übernehmen. Letzteres würde den Anker in HA sichtbar machen, ihn aber von
einer Fremdkomponente abhängig machen — die SQLite-Variante ist in sich geschlossen und
daher vorzuziehen.
