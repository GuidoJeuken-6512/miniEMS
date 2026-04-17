---
revision_date: 2026-04-07
---

# Ereignislog

Der Tab **Log** (`/log`) bietet ein chronologisches, automatisch aktualisierendes Ereignisjournal für miniEMS. Es erfasst jeden wesentlichen Zustandsübergang in einer einheitlichen Ansicht und erleichtert so das Verständnis, *warum* das System so gehandelt hat.

---

## Zusammenfassungsleiste

Am oberen Rand der Seite zeigt eine Live-Zusammenfassungsleiste immer den aktuellen Systemzustand:

| Feld | Beschreibung |
|---|---|
| Modus | Aktueller EMS-Betriebsmodus (Badge) |
| Batterie-SoC | Aktueller Ladezustand (%) |
| Freie Ladekapazität | Verfügbarer Batteriefreiraum (kWh) |
| Solcast-Rest | Heute noch erwartete PV-Energie (kWh) |
| Preis | Aktueller Strompreis – grün hervorgehoben beim Günstigtarif |

---

## Ereignistabelle

Unterhalb der Zusammenfassungsleiste listet die Ereignistabelle bis zu den **letzten 100 Ereignissen**, neueste zuerst. Zwei Arten von Ereignissen erscheinen im selben chronologischen Strom:

### Moduswechsel-Ereignisse

Werden bei jedem Wechsel des EMS zwischen Betriebsmodi aufgezeichnet.

| Spalte | Beschreibung |
|---|---|
| Ereignis | **▲ ON** (Netzladen gestartet) oder **▼ OFF** (Netzladen gestoppt) — grün / grau angezeigt |
| Zeit | ISO-8601-Zeitstempel des Übergangs |
| Preis (€/kWh) | `–` (nicht anwendbar für Moduswechsel) |
| Frei (kWh) | Verfügbarer Batteriefreiraum zum Zeitpunkt des Wechsels |
| Nutzbar (kWh) | Nutzbare Batterieenergie zum Zeitpunkt des Wechsels |
| Vorher. Last (kWh) | Vorhergesagte tägliche Hauslast zum Zeitpunkt des Wechsels |

### Preisänderungs-Ereignisse

Werden aufgezeichnet, wenn der Strompreissensor einen neuen Wert meldet, der sich vom vorherigen unterscheidet. Dies erlaubt es, Netzladeentscheidungen mit den genauen Preisschritten zu korrelieren, die sie ausgelöst haben.

| Spalte | Beschreibung |
|---|---|
| Ereignis | **€ Price Change** — gelb angezeigt |
| Zeit | ISO-8601-Zeitstempel der Preisaktualisierung |
| Preis (€/kWh) | Der **neue** Strompreis |
| Frei (kWh) | Verfügbarer Batteriefreiraum zum Zeitpunkt |
| Nutzbar (kWh) | Nutzbare Batterieenergie zum Zeitpunkt |
| Vorher. Last (kWh) | Vorhergesagte Tageslast zum Zeitpunkt |

!!! tip "Das Log lesen"
    Suche nach einem **€ Price Change**-Eintrag kurz vor einem **▲ ON**-Eintrag — das ist der Preisabfall, der den Günstigkeitsschwellenwert unterschritten und das Netzladen ausgelöst hat. Ähnlich erklärt ein Preisanstieg gefolgt von einem **▼ OFF**-Eintrag, warum das Laden gestoppt hat.

---

## Ereigniskapazität

Der In-Memory-Puffer hält die **letzten 100 Ereignisse** (Moduswechsel und Preisänderungen zusammen). Ältere Ereignisse werden still aus dem Puffer entfernt. Die Hinweiszeile am oberen Rand der Tabelle zeigt die aktuelle Anzahl.

---

## Persistenz

Ereignisse werden bei jedem Anhängen in die Tabelle `event_log` in `/data/miniems.db` geschrieben. Beim Start werden die letzten 100 Einträge aus der Datenbank wiederhergestellt, sodass das Log **nicht über Neustarts oder Updates verloren geht**.

Alte Einträge werden einmal täglich basierend auf der Einstellung **Ereignislog-Aufbewahrung** bereinigt (Standard: 30 Tage). Siehe [Konfiguration](configuration.md#ems-parameter).

---

## Automatische Aktualisierung

Die Seite fragt `/api/status` alle **5 Sekunden** ab und rendert die Tabelle neu. Kein manuelles Neuladen erforderlich.

---

## Implementierungshinweise

Preisänderungen werden nur aufgezeichnet, wenn der Preis *sich vom zuletzt beobachteten Wert unterscheidet*. Der allererste Messwert nach dem Start wird nicht als Änderung protokolliert; nachfolgende Messwerte werden mit dem laufenden Wert verglichen.

Das Datenbankschema der Tabelle `event_log` findest du unter [Datenspeicherung](../technical/data-storage.md#event_log-table).
