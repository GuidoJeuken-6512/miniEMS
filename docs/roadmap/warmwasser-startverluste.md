# Warmwasser: Verlust pro Start lernen

**Status:** Idee, nicht umgesetzt. Seit v3.7.0 verhindert ein fester
**Mindest-Ladehub** (`hw_min_charge_k` / `hw_min_charge_running_k`) Mini-Ladungen.
Ziel dieser Roadmap: den festen Hub später durch die **echten Kosten eines Starts**
ersetzen, gelernt aus den eigenen Daten.

## Anlass

Am 14.11.2026 startete miniEMS vier WW-Ladungen, zwei davon von 49,3 bzw. 49,4 °C auf
50 °C (7–8 min). Die Planung bewertete jede Ladung nur **pro kWh Wärme**. Ein Start
selbst kostete nichts, also war eine 0,7-K-Nachladung genauso „günstig“ wie eine volle
Ladung.

Beobachtet in den Lambda-Daten:

- **Temperaturknick nach dem Start:** Der obere Fühler fiel von 49,3 auf 48,1 °C,
  bevor er stieg (kaltes Wasser aus den Leitungen, Umschichtung).
- **Nachlauf** (`TRAILING-STOP`) von etwa 3 min nach jedem Ladungsende.
- **Verdichterstart aus STBY** (15:20), danach 50 min `TIME-BLOCK`. Aus dem Heizbetrieb
  (CH → DHW) dagegen kein zusätzlicher Start.

## Idee

Pro abgeschlossener Ladung aufzeichnen (`heat_pump_recorder`):

| Größe | Quelle |
|---|---|
| Wärme laut Lambda | Zähler `hp1_hot_water_thermal_energy_*` (Differenz über die Ladung) |
| Wärme im Boiler | ΔT × Volumen × 1,163 Wh/(l·K) |
| Strom | Zähler `hp1_hot_water_energy_*` bzw. WP-Leistung |
| Start aus | Verdichter lief (CH) / stand (STBY) |
| Knick | Minimum der Boiler-Temperatur nach Start − Starttemperatur |

Daraus lernen:

- **Fixe Verlustwärme pro Start** = Wärme laut Lambda − Wärme im Boiler (Leitungen,
  Vorlauf aufheizen, Nachlauf), getrennt nach „Start aus STBY“ und „Umschalten aus CH“.
- Regression über die Ladungsgröße: Verlust ≈ fix + variabel × ΔT.

Im Planner wird dann jede Ladung mit `fixe Verlustwärme × Preis ÷ COP` aufgeschlagen.
Kleine Ladungen werden dadurch von selbst unattraktiv, der feste Mindesthub kann
entfallen oder wird nur noch als Untergrenze genutzt.

## Offene Punkte

- Wie viele Ladungen braucht ein stabiles Ergebnis? Bei 2–4 Ladungen pro Tag reichen
  vermutlich 2–3 Wochen.
- Zapfungen während einer Ladung verfälschen die Bilanz. Solche Ladungen verwerfen.
- Kosten eines Verdichterstarts (Verschleiß) lassen sich nicht messen. Gegebenenfalls
  bleibt ein kleiner fester Aufschlag für „Start aus STBY“.
