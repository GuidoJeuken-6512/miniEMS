---
revision_date: 2026-08-15
---

# Kosten & Einsparungen

Diese Seite erklärt jeden Kosten- und Einsparungswert, den miniEMS im Dashboard und als
Home-Assistant-Sensor bereitstellt: was er bedeutet, wie er berechnet wird, und ein
Rechenbeispiel dazu. Für die reine Entitäts-/Einheiten-Referenz siehe
[HA-Sensoren](sensors.md); für die vollständigen Formeln mit Code-Bezug siehe den
Abschnitt „Kosten & Einsparungen" in [Berechnungen](../technical/calculations.md).

## Wie die Werte entstehen

Alle Kosten- und Energiewerte werden von `CostOptimizer` bei **jedem EMS-Tick** (Standard
alle 30 Sekunden) ein Stückchen weiter aufsummiert — nicht einmal am Tagesende neu berechnet.
Das hat zwei Konsequenzen, die beim Lesen der Werte hilfreich sind:

- **Tageswerte wachsen im Tagesverlauf** und werden um Mitternacht auf 0 zurückgesetzt.
- **Ein Neustart des Add-ons verliert bereits Aufgelaufenes nicht:** Alles bis dahin
  Akkumulierte wird aus der SQLite-Datenbank wiederhergestellt, bevor der erste neue Tick
  verarbeitet wird. Was während der Ausfallzeit selbst geflossen ist, kann eine reine
  Tick-Hochrechnung aber grundsätzlich nicht nachholen — dafür siehe den nächsten Absatz.

Für drei kWh-Größen (Netzbezug, Einspeisung, Last) nutzt miniEMS bevorzugt den **eigenen
Tageszähler des Wechselrichters**, wenn er konfiguriert ist — das ist präziser, weil kurze
Messaussetzer *und* Add-on-Neustarts den Wechselrichter-internen Zähler nicht
beeinflussen, eine reine Tick-Hochrechnung aber schon (jeder Neustart reißt dort eine
nie nachgeholte Lücke). Ist die jeweilige Entität nicht gesetzt, rechnet miniEMS
stattdessen aus der Momentanleistung hoch. Wo das zutrifft, steht es beim jeweiligen
Wert.

**Störungsfilter:** Bevor ein Leistungsmesswert in irgendeine Rechnung eingeht, prüft
miniEMS ihn auf unplausible Sprünge (mehr als 500 W *und* mehr als 50 % Änderung
gegenüber dem letzten Wert). Ein solcher Ausreißer — typischerweise eine kurze
Kommunikationsstörung zum Wechselrichter — wird verworfen und beeinflusst die
Tageswerte nicht.

---

## Tageswerte

### Netzkosten heute

`sensor.miniems_today_grid_cost_eur` — **was du heute tatsächlich für bezogenen Netzstrom
bezahlt hast** (bzw. bei einer Vorauszahlung: bezahlt hättest), zum jeweils gültigen
dynamischen Preis.

Bei jedem Tick, in dem Netzbezug (nicht Einspeisung) vorliegt:

```
Kosten += (Netzleistung_W / 1000) × Tick-Dauer_h × aktueller_Preis_€/kWh
```

**Beispiel:** Ein Tick von 30 Sekunden (= 0,008333 h) mit 1200 W Netzbezug bei
0,2744 €/kWh (Niedrigtarif) trägt `1,2 kW × 0,008333 h × 0,2744 €/kWh ≈ 0,0027 €` zur
Tagessumme bei. Über den ganzen Tag ergibt das — abhängig davon, wie viel zu welchem
Tarif bezogen wurde — deine tatsächliche Tagesrechnung.

### PV-Ersparnis heute

`sensor.miniems_today_pv_savings_eur` — der Betrag, den du **eingespart** hast, weil
PV-Strom den Hausverbrauch gedeckt hat, statt ihn zum aktuellen Preis aus dem Netz zu
kaufen. Nur der Anteil der PV, der **direkt** verbraucht wurde, zählt — ins Netz
eingespeiste PV ist hier ausgeschlossen (die wird separat vergütet, siehe unten).

```
PV-zu-Last_W = min(PV-Leistung_W, Last-Leistung_W)     # nie mehr, als gerade gebraucht wird
Ersparnis    += (PV-zu-Last_W / 1000) × Tick-Dauer_h × aktueller_Preis_€/kWh
```

Die Bewertung erfolgt zum **aktuellen Spotpreis** — dieselbe kWh PV spart bei
Hochtarif-Last mehr als bei Niedrigtarif-Last. Das ist beabsichtigt: Es zeigt den
tatsächlichen finanziellen Nutzen deiner Anlage, nicht nur die erzeugte Energiemenge.

### Gesamtlastkosten heute

`sensor.miniems_today_load_cost_eur` — die **hypothetischen** Kosten, wenn dein gesamter
Hausverbrauch heute zum jeweils aktuellen Spotpreis aus dem Netz gekommen wäre — egal, ob
er tatsächlich aus PV, Batterie oder Netz gedeckt wurde:

```
Lastkosten += (Last-Leistung_W / 1000) × Tick-Dauer_h × aktueller_Preis_€/kWh
```

Dieser Wert ist immer **größer oder gleich** den echten Netzkosten, weil PV und Batterie
einen Teil davon abfangen. Die Differenz zwischen beiden ist ein direktes Maß für den
Wert deiner PV-Anlage und deines Speichers zusammen.

Die Kosten selbst sind — wie bei den Netzkosten oben — zwingend tick-basiert, weil dafür
der Preis zu jedem Zeitpunkt gebraucht wird. Die zugrunde liegende **kWh-Menge**
(`today_load_total_kwh`) kann seit v2.0.3 dagegen optional aus dem Tageszähler des
Wechselrichters kommen (`load_consumption_entity`) statt rein aus Ticks hochgerechnet zu
werden — genau wie bei Netzbezug und Einspeisung. Ohne diese Entität war der Wert
strukturell anfällig: Jeder Neustart des Add-ons ließ eine Lücke, die nie nachgeholt
wurde, weil es keinen Hardware-Zähler als Anker gab. Live gemessen: nach einem einzigen
Neustart lag `today_load_total_kwh` bereits ~1,1 kWh (≈23 %) unter dem
Wechselrichter-eigenen Tageswert.

!!! tip "Zwei verschiedene Verlust-Sensoren"
    Für die [Wechselrichter-Wirkungsgrad-Berechnung](#wechselrichter-wirkungsgrad) weiter
    unten liest miniEMS `today_losses_entity` — standardmäßig
    `sensor.deye8k_today_losses`. Auf manchen Anlagen existiert **zusätzlich** ein
    ähnlich benannter Sensor `sensor.deye8k_loss_daily` (ein separater
    Utility-Meter-Helfer) mit einem leicht abweichenden Wert. Für einen Vergleich mit
    dem Wirkungsgrad-Sensor zählt ausschließlich `today_losses_entity` — mit dem falschen
    Sensor nachgerechnet, weicht das Ergebnis sichtbar ab, obwohl die Formel stimmt.

### Einspeisevergütung heute

`sensor.miniems_today_feed_in_revenue_eur` — deine **Einnahme** aus ins Netz
eingespeister PV-Energie, zum festen Einspeisetarif (`feed_in_tariff_eur_kwh`, Standard
0,08 €/kWh) — **nicht** zum dynamischen Spotpreis, da die Einspeisevergütung vertraglich
fest ist.

```
Einspeisung_kWh   = eingespeiste Energie heute (aus Wechselrichter-Zähler oder Momentanleistung)
Vergütung        = Einspeisung_kWh × feed_in_tariff_eur_kwh
```

Die zugrunde liegende kWh-Menge zeigt die Dashboard-Karte „Einspeisung heute"
(`today_feed_in_kwh`); als eigener HA-Sensor ist nur der Euro-Betrag exponiert.

### Netzladung heute

`sensor.miniems_today_grid_charge_kwh` (Energie) und die daraus abgeleiteten Kosten
zeigen, **wie viel deine Batterie heute aus dem Netz geladen hat** — nicht aus PV-Überschuss.
Das ist unabhängig vom aktuellen EMS-Modus aus der Leistungsbilanz abgeleitet:

```
Batterie-Ladeleistung_W = max(0, −Batterieleistung_W)         # negativ = Laden
PV-Überschuss_W         = max(0, PV-Leistung_W − Last-Leistung_W)
Netzladeleistung_W      = max(0, Batterie-Ladeleistung_W − PV-Überschuss_W)
```

Mit anderen Worten: Was die Batterie mehr lädt, als der PV-Überschuss hergibt, muss aus
dem Netz stammen. Die Kosten dieses Anteils werden — wie bei den Netzkosten oben — zum
jeweils aktuellen Preis aufsummiert.

!!! tip "Genauere Variante, falls konfiguriert"
    Sind zusätzliche Wechselrichter-Sensoren gesetzt (siehe Abschnitt „Erweiterte
    Sensoren für bilanzbasierte Kostenberechnung" in [Konfiguration](configuration.md)),
    ergänzt miniEMS diesen Leistungs-basierten Wert um eine **energiebilanzbasierte**
    Variante, die robuster gegenüber kurzen Messlücken ist — siehe
    [Erweiterte Auswertung](#erweiterte-auswertung-optional) weiter unten.

### Kosten ohne Netzladung

`sensor.miniems_today_cost_without_grid_charge` — was deine Netzrechnung heute gewesen
wäre, **wenn die Batterie nicht aus dem Netz geladen worden wäre**:

```
Kosten ohne Netzladung = max(0, Netzkosten_heute − Netzladekosten_heute)
```

Zeigt unmittelbar, ob die Netzladestrategie deine Gesamtkosten heute erhöht oder gesenkt
hat, sobald du sie mit den echten Netzkosten vergleichst.

### Kosten zum Festpreis

`sensor.miniems_today_cost_fix_price_tariff` — zum Vergleich: was dieselbe Tageslast bei
einem klassischen **Festpreistarif** gekostet hätte, statt bei deinem dynamischen Tarif:

```
Kosten_Festpreis = Gesamtlast_kWh_heute × fix_price + daily_base_price_eur
```

`fix_price` (Standard 0,30 €/kWh) und der optionale Tages-Grundpreis
`daily_base_price_eur` (Standard 0, taucht als eigener Sensor
`sensor.miniems_today_base_price_eur` auf, sobald er gesetzt ist) sind in den
[Einstellungen](configuration.md) konfigurierbar. Damit lässt sich auf einen Blick
beantworten, ob sich der dynamische Tarif für dich lohnt.

---

## Die vier Kosten-Szenarien im Vergleich

Vier Sensoren beantworten zusammen dieselbe Frage aus vier Blickwinkeln — praktisch für
eine gemeinsame Dashboard-Karte oder einen Automatisierungsvergleich:

| Sensor | Szenario | Beantwortet |
|---|---|---|
| `today_grid_cost_eur` | **Ist-Zustand** — was du heute wirklich bezahlt hast (inkl. Netzladen) | Deine echte Tagesrechnung |
| `today_cost_without_grid_charge` | **Ohne Netzladen** — was es ohne die Batterieladung aus dem Netz gekostet hätte | Erhöht das Netzladen deine Kosten? |
| `today_load_cost_eur` | **Ohne Anlage** — volle Last zum dynamischen Preis, als gäbe es weder PV noch Batterie | Was bringt dir deine Anlage insgesamt? |
| `today_cost_fix_price_tariff` | **Festtarif-Vergleich** — dieselbe Last bei einem klassischen Festpreis | Lohnt sich der dynamische Tarif? |

---

## Wochen-, Monats- und Jahreswerte

Dieselben Netzkosten- und PV-Ersparnis-Beträge stehen zusätzlich rollierend bzw.
kalendarisch aggregiert zur Verfügung:

| Zeitraum | Sensoren | Berechnung |
|---|---|---|
| **Woche** (rollierend) | `week_grid_cost_eur`, `week_pv_savings_eur` | Summe der letzten 7 Kalendertage (heute + 6 vorangegangene), direkt aus dem Tagesspeicher — keine Datenbankabfrage nötig |
| **Monat** (Kalendermonat) | `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur` | `SUM(...) WHERE Datum im aktuellen Kalendermonat`, aus der Datenbanktabelle `daily_stats` |
| **Jahr** (Kalenderjahr) | `year_grid_cost_eur`, `year_pv_savings_eur`, `year_load_cost_eur` | `SUM(...) WHERE Jahr = aktuelles Jahr`, ebenfalls aus `daily_stats` |

Der Wochenwert ist **rollierend** (immer die letzten 7 Tage), Monat und Jahr sind
**kalendarisch** (starten jeweils am 1.). Alle drei bauen auf denselben Tageswerten auf,
die oben beschrieben sind — es wird nichts neu berechnet, nur aufsummiert.

---

## Erweiterte Auswertung (optional)

Drei weitere Sensoren stehen zur Verfügung, sobald die zusätzlichen Wechselrichter-Sensoren
für die bilanzbasierte Berechnung konfiguriert sind (siehe Abschnitt „Erweiterte Sensoren
für bilanzbasierte Kostenberechnung" in [Konfiguration](configuration.md)).
Sie **ergänzen**, ersetzen aber nicht die Werte oben.

### Wechselrichter-Wirkungsgrad

`sensor.miniems_today_efficiency_pct` — wie viel der heute produzierten PV-Energie
tatsächlich nutzbar ankommt (der Rest geht als Umwandlungsverlust im Wechselrichter
verloren):

```
η = (PV-Produktion_heute − Verluste_heute) / PV-Produktion_heute × 100
```

**Beispiel:** 32,5 kWh Produktion, 2,6 kWh Verluste → η = 92,0 %. Typische Werte liegen
zwischen 88 % und 95 %.

### Netzladung (Energiebilanz)

`sensor.miniems_today_grid_charge_kwh_bilanz` — dieselbe Größe wie „Netzladung heute"
oben, aber aus den **Tages-Gesamtzählern** des Wechselrichters berechnet statt aus
Momentanleistungen — dadurch robuster gegenüber kurzen Messlücken:

```
Energie_Netzladen = Netz-Import_heute − Hausverbrauch_heute + Batterie-Entladung_heute
```

*Warum nicht einfach den Netz-Import nehmen?* Ein Teil davon deckt direkt den
Haushaltsverbrauch. Die Formel zieht diesen Anteil ab, sodass nur der tatsächliche
Batterielade-Anteil übrig bleibt.

### ROI der Netzladestrategie

`sensor.miniems_today_grid_charge_roi_eur` — der wichtigste Sensor, um zu beurteilen, ob
sich Netzladen heute **finanziell gelohnt hat**:

```
nutzbare_Energie = Netzladung_kWh_bilanz × η
Einsparung       = nutzbare_Energie × avg_discharge_tariff_eur_kwh
ROI              = Einsparung − Netzladekosten
```

`avg_discharge_tariff_eur_kwh` ist der Tarif, den die geladene Energie sonst beim
Entladen (typischerweise abends/nachts zum Hochtarif) *vermieden* hat — in den
Einstellungen konfigurierbar, Standard `0.0` = Sensor deaktiviert.

**Beispiel:** 5,5 kWh Netzladung, η = 92 %, Entladetarif 0,394 €/kWh, Ladekosten 1,51 €:

```
nutzbare Energie = 5,5 × 0,92        = 5,06 kWh
Einsparung        = 5,06 × 0,394      = 1,99 €
ROI               = 1,99 − 1,51       = +0,48 €  ✅
```

- **Positiver ROI** → das Netzladen hat sich heute gelohnt.
- **Negativer ROI** → die Netzladung war teurer als der Nutzen, den sie später bringt.

---

## Wo die Werte angezeigt werden

- **Dashboard** (`/`) — die wichtigsten Tageswerte als Karten, siehe
  [Dashboard & Oberfläche](dashboard.md).
- **Home Assistant** — jeder hier beschriebene Wert steht als eigener Sensor zur
  Verfügung, mit Langzeitstatistik-Unterstützung. Vollständige Entitäts-Liste in
  [HA-Sensoren](sensors.md).
- **Datenbank-Tab** (`/database`) — alle Tageswerte historisch durchsuchbar, siehe
  [Datenspeicherung](../technical/data-storage.md).
