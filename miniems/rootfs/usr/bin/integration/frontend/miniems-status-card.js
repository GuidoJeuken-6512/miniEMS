/* miniEMS Status card – glance panel in the style of SEM's home-status
 * card: autarky ring, mode / tariff / inverter chips and today's key figures.
 * Autarky is derived client-side (1 - grid_import / load_total) from two
 * already-published sensors. All entity fields are optional.
 */
(() => {
  const MODE = (m) => {
    const C = window.MiniEMS.COLORS;
    if (!m) return "#8b949e";
    if (m.includes("Export")) return C.warn;
    if (m.includes("PV")) return C.good;
    if (m.includes("Grid")) return C.gridIn;
    if (m.includes("Protect")) return C.bad;
    return "#8b949e";
  };
  const TIER = { low: "#8dc892", medium: "#e0b04a", high: "#ef6b63" };
  const INV = { ok: "#8dc892", warning: "#e0b04a", error: "#ef6b63" };
  const TXT = {
    de: { title: "Status", autarky: "Autarkie", pv: "PV genutzt", grid: "Netzbezug", load: "Verbrauch", free: "Batterie frei", use: "Batterie nutzbar", today: "heute" },
    en: { title: "Status", autarky: "Autarky", pv: "PV used", grid: "Grid import", load: "Consumption", free: "Battery free", use: "Battery usable", today: "today" },
  };

  class MiniEMSStatusCard extends HTMLElement {
    setConfig(config) {
      this._config = {
        mode_entity: "sensor.miniems_mode",
        price_tier_entity: "sensor.miniems_price_tier",
        grid_import_entity: "sensor.miniems_today_grid_import_kwh",
        pv_used_entity: "sensor.miniems_today_pv_used_kwh",
        load_total_entity: "sensor.miniems_today_load_total_kwh",
        battery_free_entity: "sensor.miniems_battery_kwh_freetochange",
        battery_useable_entity: "sensor.miniems_battery_kwh_useable",
        inverter_status_entity: "sensor.miniems_inverter_write_status",
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
      const mode = M.state(h, c.mode_entity), tier = M.state(h, c.price_tier_entity), inv = M.state(h, c.inverter_status_entity);
      const imp = M.num(h, c.grid_import_entity), pv = M.num(h, c.pv_used_entity), load = M.num(h, c.load_total_entity);
      const aut = load !== null && load > 0 && imp !== null ? Math.max(0, Math.min(100, (1 - imp / load) * 100)) : null;
      const autColor = aut === null ? "#8b949e" : aut >= 66 ? C.good : aut >= 33 ? C.warn : C.gridIn;

      M.mount(this, `${M.RING_CSS}
        .main { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
        .chips { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; flex: 1 1 140px; min-width: 0; }
        .tiles { margin-top: 14px; }
      `, `
        <ha-card>
          <div class="hd"><span class="hd-dot"></span><span class="hd-title">${M.esc(c.title || t.title)}</span></div>
          <div class="main">
            ${M.ring(aut === null ? 0 : aut / 100, autColor, 104, aut === null ? "–" : `${aut.toFixed(0)}%`, t.autarky)}
            <div class="chips">
              ${M.pill(mode || "–", MODE(mode))}
              ${tier ? M.pill(tier, TIER[tier] || "#8b949e") : ""}
              ${inv ? M.pill(inv, INV[inv] || "#8b949e") : ""}
            </div>
          </div>
          <div class="tiles">
            ${M.tile(`${t.pv} · ${t.today}`, M.fmtKwh(pv, 2))}
            ${M.tile(`${t.grid} · ${t.today}`, M.fmtKwh(imp, 2))}
            ${M.tile(`${t.load} · ${t.today}`, M.fmtKwh(load, 2))}
            ${M.tile(t.free, M.fmtKwh(M.num(h, c.battery_free_entity), 2))}
            ${M.tile(t.use, M.fmtKwh(M.num(h, c.battery_useable_entity), 2))}
          </div>
        </ha-card>`, C.home);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-status-card", MiniEMSStatusCard, "miniEMS Status", "Mode, tariff tier, autarky and battery headroom at a glance")
    : setTimeout(reg, 50);
  reg();
})();
