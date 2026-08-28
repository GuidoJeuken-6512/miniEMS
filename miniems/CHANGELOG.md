<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 2.6.1

### Fixed

- **Devices-Seite fast unlesbar hell auf manchen Installationen.** `.devices-table`
  war seit ihrer Einführung (I2/v2.1.0) nur im ersten (blauen, inzwischen von der
  zweiten, dunklen Theme-Definition weiter unten in `style.css` überschriebenen)
  Block gestylt — anders als `.log-table`/`.db-table`, die beide eine passende
  dunkle Fassung bekamen. Die Tabelle fiel dadurch auf `background: #fff` zurück,
  während die Zellen den (für dunkle Hintergründe gedachten) hellgrauen
  `body`-Text erbten: helle Schrift auf weißem Grund, kaum lesbar — und zwar nur
  auf dieser einen Seite, alle anderen Tabs blieben unverändert dunkel. Neue,
  eigenständige dunkle `.devices-table`-Regeln nach dem Vorbild von `.db-table`
  ergänzt.

## 2.6.0

### Added

- **Geräteprofile (I6, sichtbar): Mapping-Vorschau löst jetzt mehrere
  Klassen auf, mit einem Ampel-Status.** `/api/devices` liefert
  `resolutions.<klasse>` statt einer einzelnen `resolution` (inverter UND
  jetzt auch energy_meter), jede mit einem neuen `status`-Feld
  (`device_resolver.profile_status()`): `ok` (alles gebunden) / `degraded`
  (mindestens eine Rolle kam aus mehr als einer Quelle) / `unresolved`
  (mindestens eine Pflichtrolle fehlt) — dieselbe Drei-Werte-Form wie der
  bestehende `inverter_write_status`-Sensor. Auf der Devices-Seite als
  Ampelpunkt neben jeder Klassenüberschrift sichtbar.

  **Bewusst NICHT Teil dieses Schritts** (siehe
  docs/roadmap/v3.0-geraeteprofile.md, I6): ein echter
  `sensor.miniems_device_profile_status` in der HA-Integration, und
  Fail-closed-Schreiben bei unaufgelöster Steuerrolle. Beides würde den
  Resolver in die EMS-Tick-Schleife verdrahten — heute läuft er nur einmal
  pro `/api/devices`-Aufruf, ein WS-Registry-Roundtrip alle 30s in der
  Steuerschleife wäre eine eigene, sicherheitsrelevante Änderung für sich,
  kein Nebeneffekt dieses Schritts. Bleibt offen für einen eigenen Commit.

- **Geräteprofile (I7): erstes energy_meter-Profil, live gegen echte
  Shelly-Hardware verifiziert.** Neu: `profiles/energy_meter/
  shelly_gen1_3em.yaml` (Shelly 3EM, Gen1-Firmware) — Registry-Struktur
  UND Live-Zustände (Vorzeichen, Einheiten) wurden gegen ein echtes Gerät
  gelesen, nicht geraten (`verified: true`).

  Zwei Eigenheiten dieser Hardware zwangen zu echten Generalisierungen,
  keine Shelly-Spezialfälle:
  - **Kein translation_key.** Jede Entity dieser Integration hat
    `translation_key=null`. Neuer Fallback-Schlüssel
    `RoleHint.unique_id_suffix` (Suffix-Vergleich, bewusst nicht `in`/
    Substring — `"...-power"` ist sonst auch ein Präfix von
    `"...-powerFactor"` und hätte beide gleichzeitig getroffen).
  - **Ein physisches Gerät, vier Registry-Geräte.** HA registriert dieses
    3EM als Eltern-Gerät plus ein Kind-Gerät je Phase, ohne
    `parent_device_id`-Verknüpfung zwischen ihnen. Neues
    `DeviceProfile.group_by_config_entry` poolt alle Geräte, die dieselbe
    `config_entry_id` teilen, zu einem Auflösungs-Kandidaten — verhindert,
    dass vier identisch aussehende, unentscheidbare Kandidaten den ganzen
    Verbund unauflösbar machen (`RegistryDevice.config_entry_id`,
    `RegistryEntity.unique_id`, `RegistrySnapshot.devices_in_config_entry()`/
    `entities_of_group()`, alle neu).

  9 Rollen gebunden (l1..l3 power/voltage/current), Vorzeichen empirisch
  verifiziert (negative Leistung korreliert mit dem dominanten
  Einspeisungs-Zähler). Bewusst ungebunden: `active_power`/
  `import_energy`/`export_energy` (kein Summen-Sensor auf dieser
  Gen1-Hardware — eine Summe wäre erfunden, kein Messwert) und
  `power_factor` (pro Phase vorhanden, Katalog kennt nur eine
  Klassen-Ebene-Rolle). Alles als Caveats im Profil dokumentiert. Ein
  zweites, echtes Shelly-Gerät in derselben Instanz (Shelly 1 Mini Gen3)
  ist ein reiner Schalter ohne Leistungssensor — bestätigt den früher
  berichteten Fall "die anderen sind nur Schalter", kein energy_meter-
  Kandidat, kein Profil dafür.

  `tests/fixtures/ha_registry_snapshot.json` um die 5 realen Shelly-Geräte
  erweitert (bislang 17→22 Geräte), neue Golden-Resolution-Tests
  (`TestGoldenResolutionEnergyMeter`) reproduzieren die Live-Auflösung
  exakt. 24 neue Tests insgesamt. 690 Tests grün, Coverage 94%. Live gegen
  die lokale Testinstanz verifiziert: alle 9 Rollen lösen korrekt gegen
  die echten Shelly-Entities auf, `status=ok`, keine Fehler im Log.

## 2.5.0

### Added

- **Geräteprofile: Config-Schema v20 – Substrat für die Erkennung.**
  Zwei neue, additive Felder: `entity_overrides` (`"<klasse>.<rolle>"` →
  Entity-ID, die höchste-Priorität-Quelle in
  `device_resolver.resolve_class()`'s Auflösungskette — heute nur über
  den rohen `config.json`-Editor setzbar, ein Entity-Picker folgt in I6)
  und `device_detection_enabled` (schaltet noch nichts im Steuerpfad,
  ist die Vorbereitung dafür).

  **Migration v19→v20 ist bewusst die eine Ausnahme von "Migrationen
  ändern nie das Verhalten":** `device_detection_enabled` steht bei
  jedem Update auf bestehenden Installationen auf `false` — byte-identisch
  zum bisherigen Verhalten —, aber bei einer echten Neuinstallation
  (kein `config.json` vorhanden) von Anfang an auf `true`, damit neue
  Nutzer nie die fünf historisch falschen Deye-Standardwerte erben.
  Das Unterscheidungsmerkmal ist `os.path.exists(CONFIG_FILE)` **vor**
  dem Laden, nicht die Schema-Version selbst — beide Fälle starten
  `migrate()` sonst identisch bei Version 0.

  `entity_overrides` ist bei jeder Migration leer: nichts wird hier
  erraten, ein Override entsteht ausschließlich durch explizites Setzen.
  Die `/api/devices`-Mapping-Vorschau berücksichtigt es bereits — es
  gewinnt gegenüber einem legacy `*_entity`-Feld, das lediglich von
  seinem Standardwert abweicht (beides ist Quelle 1 der Auflösungskette,
  aber `entity_overrides` ist die bewusstere der beiden Signale).

  `web_server._coerce()` bekommt eine `_DICT_FIELDS`-Ausnahme, damit ein
  künftiger POST auf `entity_overrides` nicht in den `str(value)`-Fallback
  läuft und das Feld korrumpiert (der genaue Fehlerfall, den
  docs/roadmap/v3.0-geraeteprofile.md unter "Migration" vorab benannt hatte).

  10 neue Tests (`test_migration.py`, `test_config_loader.py`,
  `test_web_server.py`). 665 Tests grün, Coverage 94%. Live gegen die
  lokale Testinstanz verifiziert: das Update der dort bestehenden
  `config.json` läuft klaglos durch, `device_detection_enabled` bleibt
  `false`, keine Fehler im Log.

## 2.4.1

### Added

- **Geräteprofile (I5b): Live-`min`/`max`/`step`-Klemmung jetzt auf jedem
  Schreibvorgang, nicht nur beim Netzladen.** v2.4.0 klemmte nur den
  GRID_CHARGING-Ladestrom gegen die live gemeldeten Grenzen der
  Lade-Entity. Neue `InverterController._resolve_charge_current_a()`/
  `_resolve_discharge_current_a()` wenden dieselbe Disziplin jetzt auf
  **jeden** Modus und **beide** Richtungen an — ein vom BMS gemeldetes
  Lade- *oder* Entladelimit unter dem konfigurierten Wert wird jetzt in
  jedem Modus respektiert, nicht nur während des Netzladens. Entlade- und
  Lade-Grenzen werden mit getrennten `LiveLimitsCache`-Instanzen verfolgt
  (ein BMS kann pro Richtung unterschiedliche Limits melden).
  `battery_max_charge_current_a`/`battery_max_discharge_current_a`
  bleiben dabei weiterhin die harte äußere Grenze.

  Live gegen die lokale Testinstanz mit echten Daten verifiziert: sowohl
  die Lade- als auch die Entlade-Entity melden `max=350` (Hardware-Limit),
  die konfigurierten 185 A bleiben in beiden Richtungen korrekt die
  wirksame Grenze — keine Verhaltensänderung für die Produktivanlage,
  wo der konfigurierte Wert bereits unter dem Hardware-Maximum liegt.

  9 neue Tests in `test_inverter_controller.py`. 655 Tests grün, Coverage 94%.

## 2.4.0

### Added

- **Geräteprofile (I5a): der Grid-Charging-Pfad spricht jetzt Watt.**
  Neues Modul `device_control.py` — die Watt-Grenze: `to_actuator_value()`/
  `from_actuator_value()` (A/W/%-Umrechnung je nach Profil-`via:`),
  `clamp_to_limits()` (Clamping **und** Step-Quantisierung — Korrektheit,
  die die bisherige `int()`-Rundung nicht hatte), `LiveLimitsCache` (eine
  live gelesene, aber vorübergehend `unavailable` gewordene Obergrenze
  wird **nie** stillschweigend gesenkt — ein Solarman-Aussetzer darf das
  Laden nicht blockieren).

  `EMSController._grid_charge_current_a()` → `_grid_charge_power_w()`:
  liest **kein** `battery_voltage` mehr, rechnet nur noch in kWh/h → W,
  liefert `None` statt eines geraten Ampere-Fallbacks („kein präziser
  Zielwert berechenbar — nutze das Maximum des Stellglieds"). Die A↔W-
  Umrechnung passiert jetzt an **genau einer** Stelle:
  `InverterController._resolve_grid_charge_current_a()`, mit `48 V` als
  Rückfall-Spannung, falls `battery_voltage` nicht verfügbar ist. Der
  konfigurierte `battery_max_charge_current_a` bleibt dabei die harte
  äußere Grenze — ein live gemeldetes Maximum kann sie nur **verschärfen**
  (z. B. ein BMS-Limit unter dem konfigurierten Wert), nie **lockern**.

  `InverterController.apply_mode()`: Parameter `grid_charge_current_a`
  (int, Ampere) → `grid_charge_power_w` (float, Watt).

  **Bewusst kleiner geschnitten als ursprünglich in
  `docs/roadmap/v3.0-geraeteprofile.md` als „I5" geplant** — dort war eine
  komplett Watt-native `apply_mode()`-Dispatch über das Profil-`modes:`-
  Vokabular, Migration auf Schema v20 und eine live vom Resolver
  gespeiste Laufzeitkonfiguration in einem Schritt vorgesehen. Das hätte
  Entladen und alle vier anderen Modi (bisher rein amperebasiert, nie ein
  berechneter Zwischenwert) in denselben sicherheitskritischen Umbau
  hineingezogen, ohne zusätzlichen Nutzen für den heutigen Stand. Diese
  Version ändert **ausschließlich** den GRID_CHARGING-Ladepfad — Entladen
  und alle anderen Modi bleiben unverändert amperebasiert. Rest folgt als
  eigener Schritt, siehe Roadmap-Dokument.

  Live gegen die lokale Testinstanz mit echten Daten verifiziert (reale
  Batteriespannung, reale `min`/`max`/`step`-Attribute des Lade-Entities):
  die konfigurierte Ampere-Obergrenze (185 A) wird bei jedem getesteten
  Watt-Zielwert korrekt eingehalten, auch bei einem absichtlich absurden
  Test-Eingabewert (1.000.000 W).

  31 neue Tests (`test_device_control.py`, 100 % Abdeckung; Anpassungen in
  `test_ems_controller.py`, `test_ems_controller_update.py`,
  `test_inverter_controller.py` für die neue Watt-Semantik). 650 Tests
  grün, Coverage 94%.

## 2.3.0

### Changed

- **Geräteprofile (I4): Schreibbestätigungs-Engine nach `write_channel.py`
  extrahiert.** Reiner Refactor, keine Verhaltensänderung — die private
  `_WriteChannel`-Klasse aus `inverter_controller.py` wird zur
  öffentlichen `WriteChannel`, generalisiert über eine neue
  `WriteSpec`/`matches()`-Abstraktion, die `switch`/`number`/`select`
  einheitlich vergleicht (`select` noch ungenutzt, aber getestet — für
  ein künftiges `device_control.py`). Die drei bisher einzeln benannten
  Kanäle (`_charge_ch`/`_discharge_ch`/`_grid_ch`) werden zu einem
  `WriteChannelSet`, das die bestehenden Aggregat-Properties
  (`write_unconfirmed`, `longest_pending_sec`, `stuck_channel_labels`)
  über beliebig viele Kanäle statt der festen drei berechnet.

  **Abnahmekriterium erfüllt:** `test_inverter_controller.py` läuft
  **unverändert** grün — der gesamte Confirm/Retry-Vertrag aus v2.0.1/
  v2.0.2 (HTTP 200 bestätigt nie allein, `None`-Timeout zählt nicht als
  Fehler, Sim bestätigt sofort ohne Event, `unconfirmed_at_shutdown`)
  bleibt bit-für-bit erhalten.

  Live gegen die lokale Testinstanz verifiziert: sauberer Neustart,
  Moduswechsel und Schreibvorgänge im Simulationsmodus funktionieren
  unverändert.

## 2.2.0

### Added

- **Geräteprofile (I3): Rollenauflösung mit sichtbarer Mapping-Vorschau.**
  Neue Module `role_catalog.py` (`roles.yaml` – Rollenkatalog für alle
  fünf Geräteklassen `inverter`/`battery`/`ev`/`energy_meter`/`load_actor`,
  implementiert sind bisher `inverter` und `battery`), `device_profile.py`
  (lädt/matched `profiles/**/*.yaml` gegen die Geräte-Registry, exaktes
  `(manufacturer, model)`-Matching ohne Glob, `model` bewusst nicht
  Pflicht), `device_resolver.py` (die fünfstufige Auflösungskette: Config
  → Energie-Dashboard → Profil → Heuristik → sichtbar unaufgelöst — nie
  stilles Raten, jeder Widerspruch zwischen Quellen wird als Konflikt
  protokolliert, nicht verschwiegen). Neues `legacy_entity_fields.py`
  überträgt die 30 bestehenden `*_entity`-Konfigurationsfelder, die vom
  Standard abweichen, unverändert als `entity_overrides` in die neue
  Auflösungskette — **keine Verhaltensänderung** für Bestandsinstallationen.

  Erste zwei Profile: `profiles/inverter/deye_sg0x_lp3.yaml` (live
  verifiziert), `profiles/battery/pylontech_force.yaml` (offline von der
  Solarman-Definition abgeleitet, **nicht** an echter Hardware geprüft).
  Herkunft und Lizenzstatus der Quellen in `profiles/SOURCES.md`
  dokumentiert (Solarman: MIT; SEM: kein Lizenz-File, nicht übernommen).

  `/devices`-Seite erweitert um eine Mapping-Vorschau: aufgelöste Rolle,
  gewählte Entity, Herkunft, sichtbare Konflikte und unaufgelöste
  Pflichtrollen. Weiterhin rein informativ — nichts fließt in die
  Steuerung ein.

  **Golden-Resolution-Regressionstest** (`test_device_resolver.py`): der
  Resolver muss, gegen eine redigierte echte Registry-Erfassung und die
  echte Produktiv-`config.json`, für 19 Rollen exakt dieselben Entity-IDs
  liefern wie die heutige Konfiguration — automatisiert die stärkste
  Verifikation aus `docs/roadmap/v3.0-geraeteprofile.md`.

  Live gegen die lokale Testinstanz verifiziert, aus dem gebauten
  Container heraus: alle 19 Pflicht-/Optionalrollen der Klasse `inverter`
  lösen korrekt auf (0 unaufgelöst), das erkannte Profil `deye_sg0x_lp3`
  bindet stark an das reale Deye-Gerät, und die Auflösung deckt einen
  echten, bisher unbemerkten Widerspruch auf: `pv_power_entity` in der
  Config zeigt auf `sensor.deye8k_pv_power`, während Home Assistants
  Energie-Dashboard `sensor.deye8k_power` für dieselbe Rolle führt — genau
  der Fall, den die sichtbare Konflikt-Anzeige lösen soll, statt ihn
  stillschweigend zu überschreiben.

  `pyyaml>=6.0` als direkte Abhängigkeit in `requirements.txt` ergänzt
  (vorher nur transitiv über `uvicorn[standard]`).

## 2.1.0

### Added

- **Geräteprofile-Grundlage (I2): Zugriff auf HA-Geräte-Registry und
  Energie-Dashboard.** Die HA-Geräte-Registry, Entity-Registry und die
  Energie-Dashboard-Einstellungen haben keinen REST-Endpunkt — nur
  WebSocket. Neues Modul `ha_ws_api.py`: kurzlebige, einmalige
  WS-Abfragen (`energy/get_prefs`, `config/device_registry/list`,
  `config/entity_registry/list`) über `ws://hassio/homeassistant/websocket`,
  mit demselben SUPERVISOR_TOKEN → Long-Lived-Token-Fallback wie der
  bestehende REST-Pfad. Kein Dauerbetrieb, kein Abo — bleibt getrennt vom
  30-s-EMS-Tick, der weiterhin `ha_state_client.py` (REST) nutzt.

  Neue reine Parser-Module: `energy_dashboard.py` (Energie-Dashboard →
  Rollen-Kandidaten inkl. Vorzeichenkonvention, verlustfrei aus HAs eigenem
  `PowerConfig`-Schema übernommen) und `device_registry.py` (indizierte
  Sicht auf Geräte-/Entity-Registry, inkl. Fallback von `device_class` auf
  `original_device_class` und bewusst **kein** Glob-Matching auf `model`,
  da HA dort teils literale Glob-Strings, teils Firmware-Versionen
  einträgt).

  Neue read-only Seite **Devices** im Ingress-Dashboard (`/devices`,
  `/api/devices`): zeigt, was HA über Energie-Dashboard und Geräte-Registry
  weiß. Rein informativ — nichts davon fließt in die Steuerung ein, siehe
  `docs/roadmap/v3.0-geraeteprofile.md`.

  Live gegen die lokale Testinstanz verifiziert (aus dem gebauten
  Container heraus, über die eigene Ingress-Route): `/api/devices` liefert
  die echten Rollen-Kandidaten (`pv_power`, `battery_power`,
  `battery_soc`, `grid_power`, `price`), korrekte Vorzeichen
  (`battery_power` signed/discharge, `grid_power` signed/import),
  `battery_capacity_kwh: 25.0` und alle 17 Geräte der Registry, inkl. des
  Deye-Wechselrichters mit 201 Entities.

## 2.0.16

### Changed

- **`ha_ws_client.py`/`HAWebSocketClient` umbenannt zu `ha_state_client.py`/
  `HAStateClient`.** Reine Namensänderung, kein Verhalten geändert — die
  Klasse war schon immer ein REST-Poller (`GET /states` alle 15 s), nie ein
  echter WebSocket-Client; der alte Name war irreführend. Macht den Namen
  frei für einen kommenden echten WebSocket-Client
  (`ws://hassio/homeassistant/websocket`) für Geräte-Registry und
  Energie-Dashboard (siehe `docs/roadmap/v3.0-geraeteprofile.md`).

## 2.0.15

### Bug Fixes

- **Wechselrichter-Schreibbestätigung wäre im Live-Betrieb nie bestätigt worden.**
  `InverterController` liest die drei Steuer-Entities
  (`inverter_charge_current_entity`, `battery_discharging_current_entity`,
  `grid_charge_switch_entity`) zur Bestätigung eines Schreibvorgangs aus
  `ws.state_cache` — standen aber nicht in `Config.monitored_entities`, also
  hat `HAWebSocketClient` sie nie abonniert und der Cache-Eintrag war immer
  leer. `matched` war damit **immer** `False`. Im Simulationsmodus (heutiger
  Auslieferungszustand) unsichtbar, weil Sim ohne Cache-Abgleich sofort
  bestätigt — im Live-Betrieb hätte jeder Schreibvorgang dauerhaft
  unbestätigt geblieben, wäre alle 30 s neu gesendet worden und hätte nach
  `INVERTER_WRITE_STUCK_THRESHOLD_SEC` (30 min)
  `sensor.miniems_inverter_write_status = "error"` ausgelöst.

  Live gegen die lokale Testinstanz verifiziert (Simulationsmodus,
  unverändert): `Config loaded (v17): 29 entities monitored` (vorher 23),
  `States refreshed: 29/29 entities` — die drei Steuer-Entities werden jetzt
  tatsächlich abonniert und gecacht.

## 2.0.14

### Added

- **Der Energiefahrplan (Energiefahrplan-Roadmap): proaktive
  Tagesbilanz für heute Nacht → morgen, als Dashboard-Anzeige.** Neues
  Modul `energy_plan.py` (`compute_energy_plan()`) berechnet stündlich
  `deficit_kwh = max(0, bat_kwh_free − predicted_pv_tomorrow_kwh ×
  pv_charge_margin_factor)` – dieselbe Formel, die `_should_grid_charge()`
  bisher nur reaktiv und unsichtbar im Tomorrow-Fallback nutzte – und
  füllt die günstigsten Preisfenster vor der nächsten PV-Spitze
  (`PriceCurve.windows_before()`, neu) zuerst auf. Neuer Dashboard-Bereich
  „Energiefahrplan" zeigt Bedarf, geplante Fenster und geschätzte Kosten;
  bei fehlenden Daten eine transparente Erklärung statt einer erfundenen
  Zahl.

  **Bewusst nur Anzeige:** Der Plan fließt nicht in die tatsächliche
  Ladeentscheidung zurück – die bleibt die tick-basierte, reaktive Logik
  aus V1–V4. Kein gespeicherter Fahrplan, keine neue Steuerungsebene.

## 2.0.13

### Added

- **V1 (Energiefahrplan-Roadmap): Ladeleistung über das Preisfenster
  gestreckt statt Volllast.** `EMSController._grid_charge_current_a()`
  berechnet jeden Tick neu `P_soll = benötigte_Restenergie /
  verbleibende_Fensterzeit` (mit 80 % Sicherheitspuffer) und daraus
  `I_soll = P_soll / Batteriespannung`, geklemmt auf
  `battery_max_charge_current_a`. Innerhalb eines Fensters mit konstantem
  Preis ist das kostenneutral – 4 kW für 1 h kostet dasselbe wie 1 kW für
  4 h – vermeidet aber die Lastspitze einer Volllast-Ladung.
  `InverterController.apply_mode()` erhält dafür einen neuen optionalen
  Parameter `grid_charge_current_a`; ohne Tarifkalender oder
  `battery_voltage` bleibt das Verhalten unverändert (Volllast). Bekannte
  Lücke: kein Warnhinweis, wenn das Fenster für die nötige Energie zu kurz
  ist und der Strom auf das Maximum geklemmt wird (siehe Roadmap, „Offene
  Fragen").

## 2.0.12

### Added

- **V2 (Energiefahrplan-Roadmap): Fensterwahl gegen den vollen Tarifkalender
  verdrahtet.** Bislang unbenutztes `PriceCurve`-Modul jetzt aktiv in
  `_should_grid_charge()`: neue Methode `PriceCurve.later_window_as_cheap()`
  erkennt, wenn ein **späteres**, gleich teures Fenster noch rechtzeitig vor
  dem nächsten PV-Peak erreichbar ist, und schiebt das Netzladen dorthin auf
  – behebt den Fall, dass zwei gleich teure Tarif-Fenster (hier: 02–06 und
  12–16 Uhr) beide auslösen, obwohl das zweite mitten in der PV-Produktion
  liegt. Neue `_next_peak_time()` liefert die Frist aus den V3a-Peak-
  Sensoren. Ohne Tarifkalender, Peak-Zeitpunkt oder Ladeleistungs-Schätzung
  bleibt das Verhalten unverändert (kein Aufschieben).

## 2.0.11

### Added

- **Gelernte Ladeleistung (Energiefahrplan-Roadmap):** neues Modul
  `battery_capability.py` lernt die tatsächliche Batterie-Ladeleistung aus
  der Historie statt sie aus `battery_max_charge_current_a × Spannung`
  anzunehmen – eine BMS-Drosselung (beobachtet: 65 A BMS-Limit gegen 185 A
  Konfiguration) unterschätzt sonst die benötigte Ladezeit in V3a. Nur
  `GRID_CHARGING`-Ticks fließen ein (unzensierte Beobachtung, da der
  Netzanschluss dort nie der Flaschenhals ist), SoC-gebuckert (`low`/`mid`/
  `high` auf absoluten Grenzen, nicht relativ zu `battery_min_soc`/
  `battery_max_soc`). Zwei neue Tabellen (`battery_charge_capability_today`/
  `_history`); fällt auf den Konfigurationswert zurück, solange ein Bucket
  keine ausreichende Historie hat.

## 2.0.10

### Added

- **V3a (Energiefahrplan-Roadmap): Export-Halt endet am echten PV-Peak
  statt an einer hartkodierten Stunde.** `_should_hold_pv_charge()` fragt
  jetzt zuerst `_should_hold_for_peak_time()`: hält den Export exakt bis
  `peak_time_today` minus der geschätzten Ladezeit, geprüft gegen
  `pv_charge_backstop_hour` als späteste Deadline. Fällt zurück auf die
  bisherige Restprognose-Schätzung, solange Peak-Sensor oder
  `battery_voltage` fehlen – Verhalten bleibt für Installationen ohne diese
  Sensoren unverändert. Behebt insbesondere den beobachteten Fall, in dem
  eine überoptimistische Restprognose den Halt weit über den tatsächlichen
  Peak hinaus verlängerte.

## 2.0.9

### Added

- **`battery_voltage_entity` config field** (`sensor.deye8k_battery_voltage`
  by default) – converts a charge/discharge power target (W) into the
  current (A) the Deye's `number` entities accept. Groundwork for the
  [Energiefahrplan](../docs/roadmap/energiefahrplan.md) roadmap (V1 "Ladeleistung
  strecken", V3a "Gelernte Ladeleistung") – not read by any decision yet.

## 2.0.8

### Bug Fixes

- **Newly-registered `sensor.miniems_*` entity IDs came out with a
  duplicated `miniems_` prefix** (`sensor.miniems_miniems_inverter_write_
  status` instead of `sensor.miniems_inverter_write_status`), first noticed
  on `inverter_write_status` and `remaining_load_kwh`. Root cause traced in
  Home Assistant Core's own source (`helpers/entity_registry.py`,
  `_async_get_full_entity_name`): on first-ever registration HA derives the
  entity ID from the *translated* device and entity name; if translations
  or the device registry entry aren't ready at that exact moment, HA falls
  back to `f"{platform}_{unique_id}"` — and since our `unique_id` already
  starts with `"miniems_"`, same as the integration's domain, that fallback
  doubles the prefix. `MiniEMSSensor` now sets `self.entity_id` explicitly
  in `__init__`, which per Home Assistant's own code comment ("An entity
  may suggest the entity_id by setting entity_id itself") bypasses that
  whole derivation — deterministic, and no longer dependent on HA's
  language or startup timing. Verified with a purpose-built test entity
  that had never been registered before.

  > [!NOTE]
  > Entities that already registered with the broken ID keep it – Home
  > Assistant deliberately never renames an already-assigned entity ID on
  > its own. If `sensor.miniems_inverter_write_status` or
  > `sensor.miniems_remaining_load_kwh` show up with the duplicated prefix
  > on an existing installation, remove them from **Settings → Devices &
  > Services → Entities** (or delete and re-add the integration) so they
  > re-register under the fixed logic.

## 2.0.7

### Added

- **`sensor.miniems_inverter_write_status`** — the inverter write-confirm
  state (`write_errors`, `write_unconfirmed`), previously visible only on
  the add-on's own dashboard, is now a real HA sensor with three states:
  `ok`, `warning` (a channel is currently unconfirmed or recently failed,
  but not for long — the normal, usually self-resolving case), and `error`
  (a channel has been unconfirmed for longer than
  `inverter_write_stuck_threshold_sec`, i.e. the inverter demonstrably isn't
  doing what the app decided, not just waiting out a slow confirmation).
  `write_errors`, `write_unconfirmed` and `stuck_channels` are exposed as
  entity attributes. Reachable via the normal HA entity API — closes a gap
  hit while diagnosing a "28 failed write(s)" banner on the production
  system with no way to verify the live state from outside the dashboard.
- **Write-confirm events now persist to `event_log`** (`entry_type=
  "write_confirm"`), not just the add-on's own log buffer (which on the
  production system holds only ~10 minutes). Every confirmation, real
  failure, or write still unconfirmed at shutdown is recorded with its
  channel and latency. Excluded from the default Log-page view (`to_list
  (include_write_confirm=False)`) so it doesn't clutter the human-facing
  timeline — query `event_log` directly for analysis.

  > [!NOTE]
  > `inverter_write_stuck_threshold_sec` (default 1800s / 30 min) is a
  > **provisional** value. The only prior figure on record for how long a
  > legitimate Solarman confirmation can take (~25 min, see 2.0.1 below) is
  > an unsourced historical observation, not measured data. The new
  > write-confirm log exists specifically to replace that guess with real,
  > multi-day latency numbers — revisit the default once that data exists.

> [!IMPORTANT]
> After updating, `sensor.miniems_inverter_write_status` (and any other
> brand-new sensor) may not appear until Home Assistant Core itself
> restarts, not just the add-on. `homeassistant.reload_config_entry`
> reloads the integration but does not re-import its Python module, so a
> sensor added to `SENSOR_DESCRIPTIONS` stays invisible until the next full
> Core restart. Newly-registered entity IDs were also observed with a
> duplicated `miniems_` prefix on first registration — fixed in 2.0.8, see
> below.

## 2.0.6

### Bug Fixes

- **"N failed write(s)" never cleared, even long after the problem was
  fixed.** `InverterController.write_errors` was a lifetime counter,
  incremented on every real HTTP-rejected write and never reset – 28
  failures from days ago looked identical in the dashboard banner to 28
  happening right now, with no way to tell from the UI whether it was
  current or ancient history. `write_errors` is now a property counting
  only failures within the last `INVERTER_WRITE_ERROR_WINDOW_SEC` (default
  1 h); older ones are pruned on every read.

- **A single legitimate power swing could permanently freeze spike
  detection for an entity.** `SensorValidator` rejected a reading that
  differed too much from the last *accepted* value, but never updated that
  reference on rejection – correct for filtering a one-off outlier, but if
  the real value then shifted permanently (a load switches off for good),
  every future real reading looked like a spike against the same stale
  reference forever, until the add-on restarted. Observed live on the
  production system: `battery_power` rejected every tick for 2.5+ hours
  after a real drop from ~1341 W to ~394 W, feeding `0 W` into the cost
  accounting instead of the real ~270–290 W. Two consecutive rejected
  readings that agree with each other now count as a real shift and get
  adopted as the new reference – the same two-tick confirmation rule
  already used for lifetime-counter re-anchoring in `cost_optimizer.py`. A
  single outlier in between only delays recovery by one more tick.

## 2.0.5

### Bug Fixes

- **The grid-friendly export hold ("Export Surplus") could survive nearly
  all morning while the battery sat flat.** `_should_hold_pv_charge()`
  compared the Solcast remaining-today forecast only against the battery's
  free capacity, never against what the house itself would still consume
  before day's end – but PV covers the house first, so part of any
  "remaining" forecast was never reachable by the battery in the first
  place. Measured live on the production system on 2026-08-19: the hold
  held from sunrise (06:11) to 10:55 with SoC flat at 71–72%, released only
  because a live Solcast API refresh happened to cut the forecast, and the
  battery topped out at 89% instead of its ~95% max that day.
  `_should_hold_pv_charge()` now compares the forecast against
  `bat_kwh_free + remaining_load_kwh`, where the new
  `ConsumptionModel.remaining_load_kwh()` is a plain median of the last 14
  complete days' total load minus what has already been measured today –
  `None` (no history yet) degrades to the previous battery-only comparison.

### Removed

- `ConsumptionModel`/`Prediction.should_grid_charge` – computed every tick
  since the netzdienlich PV strategy but never read anywhere (the grid-charge
  decision has used the Solcast forecast exclusively since then, see docs).
  `predicted_load_kwh`/`predicted_pv_kwh` stay as dashboard-only values.

## 2.0.4

### Bug Fixes

- **Every inverter write was reported as failed.** `_call_service` used a 10s
  HTTP timeout, but HA's REST `/api/services/…` blocks until the service has
  *finished* – and a Solarman/Modbus write travels to the inverter and waits
  for its acknowledgement, which regularly takes longer. The resulting
  `asyncio.TimeoutError` was caught as a generic failure, so `write_errors`
  climbed on writes that HA had in fact applied. Measured live on the
  production system: six "write FAILED" entries in six minutes, while the
  entity states matched the written targets exactly. The errors fired 25.2s
  to 25.7s after each preceding tick, i.e. 10.2s to 10.7s into the call – an
  exact fingerprint of the timeout.

  A timeout is now distinguished from a rejection: `_call_service` returns
  `None` for "outcome unknown" instead of `False`, and only an actual non-2xx
  response counts as a write error. The existing state-based confirm/retry
  logic decides the outcome one tick later, which it was already designed to
  do. The timeout itself was raised from 10s to
  `INVERTER_SERVICE_CALL_TIMEOUT_SEC` (15s).
- **`Service call error …:` logged an empty reason.** `str()` on a
  `TimeoutError` is the empty string, so the line ended in a bare colon.
  Exceptions now log `type(exc).__name__` as well, matching the pattern
  already used in `ha_ws_client.py`.

- **The day was cut on the inverter's clock, not ours.** `grid_import_kwh`,
  `feed_in_kwh` and `load_total_kwh` took their daily value straight from the
  inverter's daily counters – but those reset on the *inverter's* clock.
  Measured across all four counters simultaneously on the production system,
  that is **4min54s after local midnight**, and during that window the daily
  sensor still reports yesterday's closing total. The Source-A override wrote
  it into the new day: 45.9 kWh of feed-in, including its revenue, for almost
  five minutes. It self-corrected, but kWh and cost were accumulated over
  windows offset by those minutes, and a clock skew in the *opposite*
  direction would have written a 0 into yesterday and persisted it as that
  day's closing total.

  miniEMS now derives the daily figure from the inverter's monotonic lifetime
  counters (`grid_import_total_entity`, `feed_in_total_entity`,
  `load_consumption_total_entity`), anchored at its own local midnight and
  persisted in `daily_stats`. Verified to carry the same 0.1 kWh resolution as
  the daily counters and to run continuously across midnight. The daily
  sensors stay in use as the anchor bootstrap after a mid-day restart, so no
  energy is lost when the add-on starts up at noon. A lifetime counter that
  runs backwards (firmware update, device swap, Modbus glitch) is re-anchored
  and logged as a warning rather than producing a negative delta.

- **The daily solar forecasts were flagged stale every single day.**
  `solcast_today_entity` and `solcast_tomorrow_entity` were checked against
  `forecast_max_age_sec` (8h) — a threshold tuned for the fast-moving
  "remaining today" sensor. But the daily totals are written only on a
  forecast fetch or at the date rollover, so they crossed 8h on any day
  Solcast fetched early. Measured live: 9h43min old at 18:14 UTC.

  Worse than the false banner, `solcast_tomorrow_entity` gates the v2.0.1
  grid-charge fallback ("tomorrow's sun will refill the battery, don't buy
  grid energy tonight"). Counted as stale, that fallback silently switched
  itself off for the entire dark charging window, every night.

  Both now use `HAWebSocketClient.is_stale_daily()`, which asks *was it
  written today?* instead of *how old is it?* — exact for a value the source
  rewrites at every date rollover, where no age threshold can be right (the
  same forecast is fresh at 09:00 and still correct at 23:00). The comparison
  runs in local time, since the source rolls the day over in the
  installation's timezone while timestamps are stored as UTC.
  `DAILY_VALUE_GRACE_SEC` (15 min) covers the minutes after midnight before
  the source has rewritten.

- **Grid charging had no hysteresis.** `_should_hold_pv_charge` uses an
  asymmetric threshold so the PV-export hold cannot flap around its trigger,
  but `_should_grid_charge` ended in a bare comparison, damped only by
  `mode_dwell_sec`. Since `remaining` moves by up to 0.47 kWh per 5-minute
  Solcast interval (measured), a battery sitting near the trigger could
  oscillate. It now uses the same `pv_charge_hysteresis_frac` band: harder to
  start charging than to keep it running.

- **Grid charging was never checked for whether it pays.** `is_cheap_rate()`
  compares the price against a configured threshold, which says nothing about
  whether the spread covers the round trip. With this installation's numbers
  the margin is thin: buying at NIEDRIG 27.44 ct to displace STANDARD
  34.44 ct yields 0.916 × 34.44 − 27.44 = **+4.1 ct/kWh** at the measured
  91.6% efficiency — and **−2.2 ct/kWh** at 85%. A badly set threshold could
  therefore buy at a loss indefinitely with nothing objecting.

  `_should_grid_charge` now requires `η × discharge_tariff − price >
  grid_charge_min_margin_eur_kwh` (2 ct). The check is *skipped*, not failed
  closed, while efficiency or discharge tariff are unknown: both come from
  accumulated history, so failing closed would leave a fresh installation
  unable to grid charge until a week of data existed. This gate is an economic
  refinement, not a safety interlock.

### Improvements

- **`avg_discharge_tariff_eur_kwh` is derived from history instead of
  configured.** It defaulted to `0.0` and was never set on the live system, so
  the one figure describing what a stored kWh is worth did not exist. It is now
  Σ(discharged kWh × price) / Σ(discharged kWh) over the last 7 days, cached per
  day; an explicitly configured value still wins.

  The weighting is the substance, not a detail. Two simpler averages were tried
  and both are wrong *in kind*: load-weighted (`load_cost_eur /
  load_total_kwh`) gives 0.2942 €/kWh on the production system, time-weighted
  (`avg_price_eur_kwh`) roughly 0.318. At 91.6% efficiency against a 0.2744
  purchase price those are margins of −0.5 ct and +1.7 ct — both below the 2 ct
  gate, so grid charging would have been switched off entirely. The battery does
  not displace average consumption though; it discharges disproportionately into
  the expensive evening (HOCH, 0.3944), where the margin is +8.7 ct. Only
  weighting by discharge measures what is really displaced.

  A plausibility floor discards any derived value below the cheap-rate
  threshold, so a broken figure leaves the gate off instead of silently
  disabling grid charging.
- **Solcast data age is now checked, not just sensor age.** Solcast keeps
  serving its persisted cache when the API is unreachable: the sensors stay
  `available`, HA's timestamps keep advancing, and nothing reveals that the
  numbers are days old. Neither `unavailable`, nor any timestamp, nor the date
  check above detects it — all four signals report "healthy". This affected
  `solcast_remaining_today_entity`, the one Solcast value feeding the
  grid-charge decision directly.

  `solcast_last_fetch_entity` is now evaluated by its **value** — the timestamp
  of the last *successful* fetch — via the new
  `HAWebSocketClient.get_state_datetime()`, since `get_state_value()` ends in
  `float(raw)` and cannot read an ISO string. The entity's own timestamp is
  useless as a freshness signal: it is exactly as old as the sensors it is
  meant to vouch for.

  `SOLCAST_DATA_MAX_AGE_SEC` is 30 h, derived from measurement rather than
  guessed: 6/6/6/5/4 successful fetches per day on five consecutive days, with
  the longest legitimate overnight gap at 15.5 h; shorter winter days push that
  towards an estimated 19 h.
- **Entity attributes are now readable.** `HAWebSocketClient` cached the full
  state dict, but the only way in was `get_state_value()` → `float(state)`, so
  `grep -rn '\["attributes"\]' *.py` found exactly zero hits. Everything HA
  carries beside the plain value was unreachable: the tariff calendar in the
  price entity's `activation_rules`, Solcast's half-hourly `detailedForecast`,
  and the real `min`/`max`/`step` of the inverter's number entities (currently
  hardcoded as `BATTERY_MAX_CURRENT_A`). `get_state_attribute(entity, name)`
  opens that up — the shared prerequisite for the grid-friendly charging
  roadmap and for the v3.0 device profiles.
- **The tick log now shows a pending mode change.** A debounced transition
  waits out `mode_dwell_sec` before it is applied, but `_mode_reason` keeps
  the *old* reason while it waits – so a correct, merely-debounced transition
  was indistinguishable from a stuck EMS. The tick line gains a
  `→ Export Surplus pending 120/300s` suffix while a change is in flight.

## 2.0.3

### New Features

- **`load_consumption_entity`** – optional inverter daily-total sensor
  (default `sensor.deye8k_today_load_consumption`) as a "Source A" for
  `today_load_total_kwh`, matching the existing pattern for grid import and
  feed-in. Migration v13 enables it by default for upgrading installs.

### Bug Fixes

- **`today_load_total_kwh` had no hardware-counter anchor.** Unlike grid
  import and feed-in, it was purely tick-accumulated and therefore
  permanently under-counted across every add-on restart – measured live:
  ~1.1 kWh (≈23%) low after a single restart. Fixed by the new
  `load_consumption_entity` above; `today_load_cost_eur` still accumulates
  per tick as before, since it needs the price at each interval.
- **Migration v11→v12 baked in stale staleness defaults.** It hardcoded
  `forecast_max_age_sec`/`price_max_age_sec` at the pre-v2.0.1 values
  (10800s/3h each), so any config that passed through it before v2.0.1
  raised those defaults kept the old, too-tight numbers permanently in
  `config.json`, immune to the later default change. Migration v13 corrects
  both if they still exactly match the old hardcoded default; an explicitly
  customized value is left alone.
- **The custom integration's reload call has never worked.** `GET
  .../config/config_entries` is not a real Home Assistant REST endpoint
  (config-entry listing/reload is WebSocket-only) – it always returned 404,
  so the integration was silently never reloaded after an add-on update.
  This is also why a renamed sensor `key` (e.g. a past typo fix) could
  leave a permanently orphaned, "unavailable" entity behind that later
  collided with the freshly-registered one and forced a "_2" suffix onto
  it, instead of the clean name. Fixed by calling the
  `homeassistant.reload_config_entry` *service* instead (a real REST
  endpoint), targeted at a miniEMS entity resolved via a Jinja template
  rather than a hardcoded name – even a seemingly stable `key` has turned
  out to have older, pre-repository renames of its own on a real
  installation.
- **Orphaned same-config-entry entities were never cleaned up.** The
  existing registry cleanup only removed entities left behind by a fully
  deleted-and-re-added config entry; it did nothing for a renamed sensor
  `key` within the *same* config entry, which is the actual cause of the
  observed "_2" sensors. `async_setup_entry` now also removes any
  registered miniEMS entity whose `unique_id` is no longer produced by the
  current sensor descriptions.

### Documentation

- Corrected `battery_power_entity`'s sign convention in the configuration
  reference (was documented backwards: **negative** = charging, **positive**
  = discharging, verified live against the inverter).
- New page **Sensor Staleness** (`roadmap/sensor-staleness.md`): every
  `is_stale()` call site in the project, its real-world update cadence, and
  a verdict, with a "solution available" column. Root cause proven in the
  Solcast integration's source: `_handle_coordinator_update` returns early
  for `DEFAULT`-policy sensors, so the daily forecast totals are never
  rewritten and *no* HA timestamp – `last_updated`, `last_changed` or
  `last_reported` – can advance. Live-reproduced: `solcast_today_entity`
  was 9h43min old against an 8h threshold. Also documents why
  `unavailable` cannot substitute for the check (Solarman reports it on
  connection loss, Solcast never does – it serves its disk cache instead).
  Five proposals, none implemented; the recommended one is a date check
  ("is the timestamp from today?") rather than any age threshold.
- New page **Tageswechsel & Energiezählung**
  (`roadmap/tageswechsel-energiezaehlung.md`): the inverter resets its
  daily counters 4min54s *after* local midnight (measured across all four
  counters simultaneously), so for those minutes the Source-A override
  writes yesterday's total into today's bucket. Proposes switching to the
  lifetime `total_*` counters with a self-computed daily delta – verified
  to carry the same 0.1 kWh resolution and to be continuous across
  midnight. Not implemented.
- New page **Costs & Savings** (`user/costs.md`): every cost/savings value
  explained with a worked example, including the two-loss-sensor pitfall
  (`today_losses_entity` vs. the differently-named `loss_daily` helper).

## 2.0.2

### Bug Fixes

- **Write confirmation falsely fired in simulation mode.** The confirm/retry
  logic introduced in v2.0.1 compared the (never-written) target against the
  *real* HA state even with `battery_control_simulation` on, so it could
  never match – every write sat permanently "unconfirmed" and was re-logged
  every tick. Simulated writes are now confirmed immediately after logging,
  matching pre-v2.0.1 behaviour (log once per target change, nothing to
  confirm).
- **SoC "unavailable" fallback used the wrong staleness signal.** The core
  safety check ("no usable SoC → hand control back to the inverter") tested
  `battery_soc_entity`'s own last-updated timestamp against
  `sensor_max_age_sec` (5 min) — but SoC is a coarse percentage that can
  legitimately hold the exact same value for hours, or for days in winter
  with grid charging off, so no fixed timeout on "time since it last changed"
  can ever be correct. The check now uses `battery_power_entity`'s freshness
  instead: it comes from the same BMS/inverter connection and fluctuates
  continuously whenever that connection is alive, so it actually distinguishes
  a dead link from a battery that is simply, legitimately, not changing.

## 2.0.1

### Bug Fixes

- **Grid charging ignored tomorrow's PV forecast.** When today's Solcast
  remaining-forecast was missing, stale, or simply spent (evening — no more
  sun coming), `_should_grid_charge` fell straight back to a blind
  time-of-day check, even when tomorrow's forecast (`solcast_tomorrow_entity`)
  was already known and more than large enough to refill the battery for
  free. The decision now checks `solcast_tomorrow_kwh` first and skips grid
  charging if it alone covers the battery's need; the dark-window fallback
  only applies when neither day has a usable value. Purely restrictive — can
  only prevent a charge, never trigger one, and leaves every existing safety
  gate (SoC floor, `PROTECT_BATTERY`) untouched.
- **Solcast/price staleness thresholds were tighter than reality.**
  `forecast_max_age_sec` (3 h → 8 h) and `price_max_age_sec` (3 h → 6 h) were
  short enough that a Solcast forecast or tariff price that simply hadn't
  needed to change yet (e.g. overnight, when Solcast may not poll again for
  several hours) was misread as "stale" and fell back to less accurate
  decision paths, or produced false dashboard warnings.
- **Inverter writes were assumed applied on HTTP 200.** A service call HA
  accepts is not proof the inverter (or the Deye/Solarman bridge in between)
  actually applied it — confirmed writes lagging by up to ~25 minutes, and
  in one case a grid-charge switch toggle that never landed at all despite
  being logged as sent. `InverterController` now re-checks the real HA state
  against the target on every tick and keeps resending until it matches,
  independently for charge current, discharge current and the grid-charge
  switch. A new `write_unconfirmed` counter (0–3, live) surfaces this as a
  dashboard warning, separate from `write_errors` (outright HTTP failures).

## 2.0.0

### Breaking

- **Battery limits are now current (A), not power (W).** The Deye inverter has
  no charge/discharge *power* entity — it exposes current limits
  (`number.deye8k_battery_max_charging_current`, 0–350 A). The old watt-based
  settings wrote to entities that do not exist, so those writes silently did
  nothing. Config migration **v11** renames the fields, repoints the entities and
  resets the limits to the inverter's rated 185 A (old watt values cannot be
  converted without the battery voltage).
  - `inverter_charge_power_entity` → `inverter_charge_current_entity`
  - `battery_discharging_power_entity` → `battery_discharging_current_entity`
  - `battery_max_charge_power_w` → `battery_max_charge_current_a`
  - `battery_max_discharge_power_w` → `battery_max_discharge_current_a`
  - `default_discharge_power_w` removed (its 185 was the ampere limit, never watts)
- New operating mode `Export Surplus`. Automations matching
  `sensor.miniems_mode == "Idle"` will now see this value during the day.

### New Features

- **Grid-friendly PV charging** (config migration **v12**, off by default via
  `pv_export_priority_enabled`). On PV surplus the energy is exported first;
  the battery only starts charging once the remaining PV forecast has fallen to
  roughly what the battery still needs (`pv_charge_margin_factor`, default 1.2).
  This shaves the midday feed-in peak. Honours `battery_control_simulation`.
  - Guards, all failing *closed* so the battery can never be stranded empty:
    SoC floor (`pv_export_min_soc_pct`), hard time backstop
    (`pv_charge_backstop_hour`), and missing/stale forecast → charge normally.
  - Anti-flapping: asymmetric hysteresis (`pv_charge_hysteresis_frac`) plus a
    dwell time (`mode_dwell_sec`); safety transitions bypass the dwell.
- **Sensor staleness detection.** A frozen sensor (e.g. Solcast after its API
  quota is exhausted) no longer counts as a live reading; it is surfaced as a
  dashboard warning and excluded from control decisions.

### Bug Fixes

- Failed inverter writes were recorded as successful, which suppressed all
  retries and reported the intended value as if it were live. Service calls are
  now checked; `*_target_a` (intended) and `*_limit_a` (confirmed) are separate,
  and failures are counted and surfaced as a warning.
- The grid-charge switch was written on **every** tick (~2880 service calls/day).
  Commands are now deduplicated against the confirmed state.
- Grid charging no longer happens just because the forecast is missing or the
  prediction is unconfident (previously fail-open). Without a forecast, grid
  charging is limited to the configurable dark window.
- The battery could not discharge in normal operation: `Idle` wrote a 185 limit
  to a non-existent entity. `Idle` and `PV Charging` now set the full limit.
- Dashboard showed `–` for *Saved Today*, *Saved This Week* and *Cost at Fix
  Price* — the template read three key names the backend never emitted.
- An unavailable load sensor was read as 0 W, producing phantom PV surplus and
  spurious charging. Control now distinguishes "no reading" from zero.
- `daily_base_price_eur` and `avg_discharge_tariff_eur_kwh` were saved as
  strings by the settings page, raising `TypeError` on first use.
- A non-numeric `_version` in `config.json` crashed startup in a loop.
- The inverter is reset to safe defaults on shutdown, so a restricted charge
  limit is never left behind.

## 1.3.0

### New Features
- **Settings page** in the miniEMS dashboard: all config options are editable
  in the browser; *Save & Restart* writes `/data/config.json` and triggers an
  addon restart via the Supervisor API.
- **Forecast & Prediction** (Phase 3):
  - `weather_client.py`: fetches 24 h OpenWeatherMap forecast (8 × 3 h slots),
    derives average night temperature, PV yield factor and day length.
    Cache TTL: 3 hours.
  - `consumption_model.py`: predicts today's load (temperature-matched
    historical days → 30-day median fallback) and PV yield (75th-percentile
    peak PV × cloud factor × daylight hours).
  - Smart grid-charge gating: `GRID_CHARGING` mode is only triggered when the
    model recommends it (`battery + predicted_pv < predicted_load`).
    Falls back to always-charge during cheap rates when not enough history
    exists (`confidence = none`).
  - SQLite: new `peak_pv_w` and `avg_outdoor_temp_c` columns in `daily_stats`.
- **2 new HA sensors**: `sensor.miniems_predicted_load_kwh`,
  `sensor.miniems_predicted_pv_kwh` (MQTT + REST fallback).
- **Confidence badge** on dashboard: `high` / `low` / `none`.
- **SIM badge** on dashboard mode indicator when battery control is in
  simulation mode.

### Config additions
- `outdoor_temp_entity` – optional HA temperature sensor for historical matching
- `openweathermap_api_key` – OWM API key (leave empty to disable forecast)
- `openweathermap_lat` / `openweathermap_lon` – location for forecast queries

### Schema migration
Config schema v2 → v3 (adds 4 new forecast fields with safe defaults).

---

## 1.2.0

### New Features
- **Battery control** (Phase 2): addon actively controls the Deye inverter via
  HA service calls based on EMS mode.
  - Sets inverter work mode, max charge power, and max discharge power.
  - **Simulation mode** (`battery_control_simulation: true`, default): all
    commands are logged as `[SIM]` but not executed. Safe for testing.
  - **Idempotent**: service calls only sent when value actually changes.
- **4 new config fields**: `battery_control_enabled`,
  `battery_control_simulation`, `battery_max_charge_power_w`,
  `battery_max_discharge_power_w`.
- **5 new entity config fields**: `inverter_work_mode_entity`,
  `inverter_charge_power_entity`, `inverter_discharge_power_entity`,
  `inverter_charge_mode_charge`, `inverter_charge_mode_selfuse`.
- **2 new HA sensors**: `sensor.miniems_charge_power_limit_w`,
  `sensor.miniems_discharge_power_limit_w` (MQTT + REST).

### Schema migration
Config schema v1 → v2 (adds battery control fields with safe defaults).

---

## 1.1.0

### New Features
- **MQTT Discovery**: sensors published via MQTT with `unique_id`, device
  grouping and long-term statistics support. Falls back to REST when Mosquitto
  is not installed.
- **SQLite persistence** (`/data/miniems.db`): daily stats survive restarts.
  Running totals restored from DB on startup — values no longer reset to 0.
- **7 new HA sensors**: `today_load_total_kwh`, `today_load_cost_eur`,
  `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur`,
  `year_grid_cost_eur`, `year_pv_savings_eur`.
- VS Code task "Update and Start Addon" for fast code-only redeployment.

### Bugfixes
- Entity IDs were doubling the device name prefix
  (`sensor.miniems_miniems_…`) — fixed by using short sensor names in MQTT
  Discovery and relying on HA's device-name prepending.

---

## 1.0.1

### Bugfixes
- Rebuild / Update VS Code tasks now patch `homeassistant_api` and
  `services: mqtt:need` into the supervisor in-memory cache to work around
  a supervisor bug in dev builds.

---

## 1.0.0

- Initial release of miniEMS
- WebSocket client for live HA entity states (no polling)
- EMS decision logic: Idle / PV Charging / Grid Charging / Battery Protection
- Cost accounting: grid import cost, PV savings, grid import kWh, PV used kWh
- Weekly aggregated cost/savings sensors
- FastAPI ingress dashboard with auto-refresh
- Config persistence with schema migration (`options.json` → `config.json`)
