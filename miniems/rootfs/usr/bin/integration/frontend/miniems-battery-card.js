/* miniEMS Battery card – battery illustration with fill level plus the
 * capacity split miniEMS tracks: usable energy, free headroom (what can
 * still be charged/discharged) and total capacity. The state of charge is
 * optional (battery_soc_entity) because miniEMS does not publish it itself;
 * without it the fill is derived from usable/capacity.
 */
(() => {
  // shared helpers normally load first (see __init__.py); if this card won
  // the race, or the page was cached, pull them in next to this script
  if (!window.MiniEMS && !document.querySelector("script[data-miniems-shared]")) {
    const src = document.currentScript && document.currentScript.src;
    if (src) {
      const sh = document.createElement("script");
      sh.src = src.replace(/[^/?#]*([?#].*)?$/, "miniems-shared.js");
      sh.dataset.miniemsShared = "1";
      document.head.appendChild(sh);
    }
  }
  const TXT = {
    de: { title: "Batterie", soc: "Ladezustand", cap: "Kapazität", use: "Nutzbar", free: "Frei (Ladereserve)", stored: "Gespeichert",
          charging: "Lädt", discharging: "Entlädt", idle: "Bereit", power: "Leistung" },
    en: { title: "Battery", soc: "State of charge", cap: "Capacity", use: "Usable", free: "Free headroom", stored: "Stored",
          charging: "Charging", discharging: "Discharging", idle: "Idle", power: "Power" },
  };

  class MiniEMSBatteryCard extends HTMLElement {
    setConfig(config) {
      this._config = {
        battery_useable_entity: "sensor.miniems_battery_kwh_useable",
        battery_free_entity: "sensor.miniems_battery_kwh_freetochange",
        battery_capacity_entity: "sensor.miniems_battery_capacity_kwh",
        ...config,
      };
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 3; }
    static getStubConfig() { return {}; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const c = this._config, h = this._hass, t = TXT[M.lang(h)], C = M.COLORS;
      const use = M.num(h, c.battery_useable_entity), free = M.num(h, c.battery_free_entity), cap = M.num(h, c.battery_capacity_entity);
      let soc = M.num(h, c.battery_soc_entity);
      if (soc === null && use !== null && cap > 0) soc = (use / cap) * 100;
      const p = M.num(h, c.battery_power_entity); // > 0 = discharge
      const dir = p === null || Math.abs(p) < 10 ? "idle" : p > 0 ? "discharging" : "charging";
      const col = dir === "charging" ? C.charge : C.discharge;
      const pct = soc === null ? 0 : Math.max(0, Math.min(100, soc));
      const fillColor = pct < 20 ? C.bad : pct < 40 ? C.warn : col;
      const h2 = (pct / 100) * 78;
      const seg = (v, color) => `<span style="width:${cap > 0 && v !== null ? Math.min(100, (v / cap) * 100) : 0}%;background:${color}"></span>`;

      M.mount(this, `
        .main { display: flex; gap: 22px; align-items: center; flex-wrap: wrap; }
        .batt { width: 74px; flex-shrink: 0; filter: drop-shadow(0 0 10px ${fillColor}44); }
        .batt svg { width: 100%; display: block; }
        .info { flex: 1 1 190px; min-width: 0; }
        .big { font-size: 34px; font-weight: 800; color: ${fillColor}; font-variant-numeric: tabular-nums; line-height: 1; }
        .bar { margin-top: 14px; }
        .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; font-size: 12px; color: var(--secondary-text-color); }
        .legend i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
        .tiles { margin-top: 16px; }
      `, `
        <ha-card>
          <div class="hd"><span class="hd-dot"></span><span class="hd-title">${M.esc(c.title || t.title)}</span>
            <div class="hd-right">${p !== null ? M.pill(`${t[dir]}${dir === "idle" ? "" : ` · ${M.fmtPower(Math.abs(p))}`}`, dir === "idle" ? "#8b949e" : col) : ""}</div></div>
          <div class="main">
            <div class="batt"><svg viewBox="0 0 60 104" aria-hidden="true">
              <rect x="21" y="1" width="18" height="7" rx="2.5" fill="${fillColor}" opacity="0.5"/>
              <rect x="3" y="8" width="54" height="94" rx="9" fill="none" stroke="${fillColor}" stroke-width="2.5"/>
              <rect x="8" y="${(97 - h2).toFixed(1)}" width="44" height="${h2.toFixed(1)}" rx="5" fill="${fillColor}" opacity="0.85"/>
              <path d="M33,38 L24,58 L31,58 L27,76 L40,52 L32,52 Z" fill="#fff" opacity="0.8"/>
            </svg></div>
            <div class="info">
              <div class="big">${soc === null ? "–" : `${soc.toFixed(0)}%`}</div>
              <div class="muted">${c.battery_soc_entity ? t.soc : t.stored}</div>
              <div class="bar">${seg(use, col)}${seg(free, "color-mix(in srgb, " + col + " 35%, transparent)")}</div>
              <div class="legend"><span><i style="background:${col}"></i>${t.use}</span><span><i style="background:color-mix(in srgb, ${col} 35%, transparent)"></i>${t.free}</span></div>
            </div>
          </div>
          <div class="tiles">
            ${M.tile(t.use, M.fmtKwh(use, 2))}
            ${M.tile(t.free, M.fmtKwh(free, 2))}
            ${M.tile(t.cap, M.fmtKwh(cap, 2))}
          </div>
        </ha-card>`, col);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-battery-card", MiniEMSBatteryCard, "miniEMS Battery", "Battery fill level, usable energy and free headroom")
    : setTimeout(reg, 50);
  reg();
})();
