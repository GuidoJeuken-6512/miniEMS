/* miniEMS Status card — condensed glance panel.
 *
 * Mirrors the intent of the SEM Community integration's sem-home-status-card
 * (a chip row + a couple of detail lines), but scoped to what miniEMS's own
 * sensors actually carry: mode, tariff tier, battery headroom, inverter
 * write health, and a client-side-derived autarky figure
 * (1 - grid_import/load_total, both already-published sensors). No CO2,
 * peak-load, or EV data — miniEMS does not track any of that.
 *
 * Same plain-HTMLElement style as miniems-flow-card.js / miniems-plan-card.js
 * (no Lit, no build step). All entity fields are optional with
 * sensor.miniems_*-defaults, so the card works out of the box once the
 * integration is set up, same as miniems-plan-card.
 */
(() => {
  function fmt(v, dec = 1, unit = "") {
    return v !== null && v !== undefined && !Number.isNaN(v)
      ? `${Number(v).toFixed(dec)}${unit}`
      : "–";
  }

  // Same substring classification as the (now-removed) dashboard.html
  // fmtMode()/mode-badge CSS – kept identical so the colors stay familiar.
  function modeClass(mode) {
    if (!mode) return "idle";
    if (mode.includes("Export")) return "export";
    if (mode.includes("PV")) return "pv";
    if (mode.includes("Grid")) return "grid";
    if (mode.includes("Protect")) return "protect";
    return "idle";
  }

  const MODE_COLORS = {
    idle: "#8b949e",
    pv: "#3fb950",
    export: "#d29922",
    grid: "#58a6ff",
    protect: "#f85149",
  };

  const TIER_COLORS = { low: "#3fb950", medium: "#d29922", high: "#f85149" };

  const INVERTER_COLORS = { ok: "#3fb950", warning: "#d29922", error: "#f85149" };

  const CARD_CSS = `
    ha-card { padding: 0.5rem 1rem 0.75rem; }
    .section-title { font-size: 0.9rem; color: var(--secondary-text-color); margin: 0 0 0.6rem; }
    .chips-row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.6rem; }
    .chip {
      display: inline-flex; align-items: center; gap: 0.35rem;
      padding: 0.3rem 0.7rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
      background: var(--secondary-background-color, #21262d);
      border: 1px solid var(--divider-color, #30363d);
    }
    .chip-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
    .detail-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
      gap: 0.5rem 1rem;
    }
    .detail { display: flex; flex-direction: column; }
    .detail-label { font-size: 0.7rem; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: 0.04em; }
    .detail-value { font-size: 1.1rem; font-weight: 700; color: var(--primary-text-color); }
  `;

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
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    getCardSize() {
      return 2;
    }

    static getStubConfig() {
      return {};
    }

    _state(entityId) {
      const st = this._hass?.states[entityId];
      if (!st || st.state === "unavailable" || st.state === "unknown") return null;
      return st.state;
    }

    _num(entityId) {
      const s = this._state(entityId);
      if (s === null) return null;
      const n = parseFloat(s);
      return Number.isFinite(n) ? n : null;
    }

    _render() {
      if (!this._config || !this._hass || !this.shadowRoot) return;
      const c = this._config;
      const title = c.title || "Status";

      const mode = this._state(c.mode_entity);
      const mCls = modeClass(mode);
      const mColor = MODE_COLORS[mCls];

      const tier = this._state(c.price_tier_entity);
      const tColor = TIER_COLORS[tier] || "#8b949e";

      const gridImport = this._num(c.grid_import_entity);
      const pvUsed = this._num(c.pv_used_entity);
      const loadTotal = this._num(c.load_total_entity);
      // Autarky: share of today's consumption NOT covered by the grid.
      // Derived client-side from two already-published sensors – no new
      // backend computation, see energy_plan.py's own "display-only" stance.
      const autarkyPct =
        loadTotal !== null && loadTotal > 0 && gridImport !== null
          ? Math.max(0, Math.min(100, (1 - gridImport / loadTotal) * 100))
          : null;

      const battFree = this._num(c.battery_free_entity);
      const battUseable = this._num(c.battery_useable_entity);

      const invStatus = this._state(c.inverter_status_entity);
      const invColor = INVERTER_COLORS[invStatus] || "#8b949e";

      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CARD_CSS}</style>
          <p class="section-title">${title}</p>
          <div class="chips-row">
            <span class="chip" style="color:${mColor}">
              <span class="chip-dot" style="background:${mColor}"></span>${mode || "–"}
            </span>
            ${tier
              ? `<span class="chip" style="color:${tColor}">
                   <span class="chip-dot" style="background:${tColor}"></span>${tier}
                 </span>`
              : ""}
            ${invStatus
              ? `<span class="chip" style="color:${invColor}">
                   <span class="chip-dot" style="background:${invColor}"></span>${invStatus}
                 </span>`
              : ""}
          </div>
          <div class="detail-grid">
            <div class="detail">
              <span class="detail-label">Autarkie</span>
              <span class="detail-value">${autarkyPct !== null ? fmt(autarkyPct, 0, "%") : "–"}</span>
            </div>
            <div class="detail">
              <span class="detail-label">PV genutzt</span>
              <span class="detail-value">${fmt(pvUsed, 2, " kWh")}</span>
            </div>
            <div class="detail">
              <span class="detail-label">Batterie frei</span>
              <span class="detail-value">${fmt(battFree, 2, " kWh")}</span>
            </div>
            <div class="detail">
              <span class="detail-label">Batterie nutzbar</span>
              <span class="detail-value">${fmt(battUseable, 2, " kWh")}</span>
            </div>
          </div>
        </ha-card>
      `;
    }
  }

  if (!customElements.get("miniems-status-card")) {
    customElements.define("miniems-status-card", MiniEMSStatusCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "miniems-status-card",
      name: "miniEMS Status",
      description: "Mode, tariff tier, autarky and battery headroom at a glance",
      preview: false,
    });
  }
})();
