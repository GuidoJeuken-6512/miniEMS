/* miniEMS Solar card.
 *
 * Reduced version of the SEM Community integration's sem-solar-card ring
 * hero: live PV power (optional, read from a user-configured entity — PV
 * power is deliberately NOT a miniEMS sensor, see integration/sensor.py's
 * anti-duplication comment, same reasoning as miniems-flow-card.js) plus
 * today's used PV energy vs. the predicted PV yield, both already-published
 * miniEMS sensors (today_pv_used_kwh, predicted_pv_kwh). No per-string, no
 * degradation/specific-yield metrics – miniEMS doesn't track those.
 *
 * Same plain-HTMLElement style as the other miniems-*-card.js files.
 */
(() => {
  function fmt(v, dec = 1, unit = "") {
    return v !== null && v !== undefined && !Number.isNaN(v)
      ? `${Number(v).toFixed(dec)}${unit}`
      : "–";
  }

  function fmtW(w) {
    if (w === null || w === undefined || Number.isNaN(w)) return "–";
    return Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`;
  }

  const CARD_CSS = `
    ha-card { padding: 0.5rem 1rem 0.75rem; }
    .section-title { font-size: 0.9rem; color: var(--secondary-text-color); margin: 0 0 0.6rem; }
    .hero { display: flex; align-items: center; gap: 1.2rem; margin-bottom: 0.75rem; }
    .ring { position: relative; width: 84px; height: 84px; flex-shrink: 0; }
    .ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
    .ring-bg { fill: none; stroke: rgba(255,152,0,0.15); stroke-width: 6; }
    .ring-arc {
      fill: none; stroke: #ff9800; stroke-width: 6; stroke-linecap: round;
      transition: stroke-dashoffset 1s ease;
    }
    .ring-center {
      position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
      text-align: center; pointer-events: none;
    }
    .ring-power { font-size: 0.85rem; font-weight: 700; color: #ff9800; line-height: 1.2; }
    .ring-sub { font-size: 0.65rem; color: var(--secondary-text-color); }
    .compare { flex: 1; min-width: 0; }
    .compare-row { display: flex; justify-content: space-between; font-size: 0.85rem; padding: 0.15rem 0; }
    .compare-row .label { color: var(--secondary-text-color); }
    .compare-row .value { font-weight: 700; color: var(--primary-text-color); }
    .vs-bar { height: 6px; border-radius: 3px; background: var(--secondary-background-color, #2a2a2a);
               overflow: hidden; margin-top: 0.4rem; }
    .vs-bar-fill { height: 100%; background: #ff9800; border-radius: 3px; }
    .vs-pct { font-size: 0.75rem; color: var(--secondary-text-color); margin-top: 0.2rem; }
  `;

  class MiniEMSSolarCard extends HTMLElement {
    setConfig(config) {
      this._config = {
        pv_used_entity: "sensor.miniems_today_pv_used_kwh",
        predicted_pv_entity: "sensor.miniems_predicted_pv_kwh",
        max_power_w: 10000,
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

    _num(entityId) {
      if (!entityId) return null;
      const st = this._hass?.states[entityId];
      if (!st || st.state === "unavailable" || st.state === "unknown") return null;
      const n = parseFloat(st.state);
      return Number.isFinite(n) ? n : null;
    }

    _render() {
      if (!this._config || !this._hass || !this.shadowRoot) return;
      const c = this._config;
      const title = c.title || "Solar";

      const pvW = this._num(c.pv_entity);
      const maxW = c.max_power_w || 10000;
      const pct = pvW !== null ? Math.min(Math.max(pvW / maxW, 0), 1) : 0;
      const circumference = 2 * Math.PI * 36;
      const arcOffset = (circumference * (1 - pct)).toFixed(1);

      const pvUsed = this._num(c.pv_used_entity);
      const predicted = this._num(c.predicted_pv_entity);
      const vsPct =
        predicted !== null && predicted > 0 && pvUsed !== null
          ? Math.min(999, (pvUsed / predicted) * 100)
          : null;

      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CARD_CSS}</style>
          <p class="section-title">${title}</p>
          <div class="hero">
            <div class="ring">
              <svg viewBox="0 0 80 80">
                <circle class="ring-bg" cx="40" cy="40" r="36"/>
                <circle class="ring-arc" cx="40" cy="40" r="36"
                  stroke-dasharray="${circumference.toFixed(1)}"
                  style="stroke-dashoffset:${arcOffset}"/>
              </svg>
              <div class="ring-center">
                <div class="ring-power">${c.pv_entity ? fmtW(pvW) : "–"}</div>
                <div class="ring-sub">PV</div>
              </div>
            </div>
            <div class="compare">
              <div class="compare-row">
                <span class="label">Heute genutzt</span>
                <span class="value">${fmt(pvUsed, 2, " kWh")}</span>
              </div>
              <div class="compare-row">
                <span class="label">Prognose heute</span>
                <span class="value">${fmt(predicted, 2, " kWh")}</span>
              </div>
              <div class="vs-bar">
                <div class="vs-bar-fill" style="width:${vsPct !== null ? Math.min(100, vsPct) : 0}%"></div>
              </div>
              <div class="vs-pct">${vsPct !== null ? `${vsPct.toFixed(0)}% der Prognose` : "–"}</div>
            </div>
          </div>
        </ha-card>
      `;
    }
  }

  if (!customElements.get("miniems-solar-card")) {
    customElements.define("miniems-solar-card", MiniEMSSolarCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "miniems-solar-card",
      name: "miniEMS Solar",
      description: "Live PV power plus today's production vs. forecast",
      preview: false,
    });
  }
})();
