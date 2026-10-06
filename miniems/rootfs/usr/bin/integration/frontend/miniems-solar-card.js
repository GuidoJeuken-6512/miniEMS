/* miniEMS Solar card – PV ring hero in the style of SEM's solar card: live
 * PV power (optional pv_entity – PV power is deliberately not a miniEMS
 * sensor, see integration/sensor.py), today's used PV energy against the
 * forecast, and a marker bar showing how far the day is along.
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
    de: { title: "Solar", used: "Heute genutzt", pred: "Prognose heute", of: "der Prognose", now: "Jetzt", left: "Noch offen" },
    en: { title: "Solar", used: "Used today", pred: "Forecast today", of: "of forecast", now: "Now", left: "Remaining" },
  };

  class MiniEMSSolarCard extends HTMLElement {
    setConfig(config) {
      this._config = {
        pv_used_entity: "sensor.miniems_today_pv_used_kwh",
        predicted_pv_entity: "sensor.miniems_predicted_pv_kwh",
        max_power_w: 10000,
        ...config,
      };
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 3; }
    static getStubConfig() { return {}; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const c = this._config, h = this._hass, t = TXT[M.lang(h)], col = M.COLORS.solar;
      const pvW = M.num(h, c.pv_entity), maxW = c.max_power_w || 10000;
      const used = M.num(h, c.pv_used_entity), pred = M.num(h, c.predicted_pv_entity);
      const vs = pred !== null && pred > 0 && used !== null ? (used / pred) * 100 : null;
      const left = pred !== null && used !== null ? Math.max(0, pred - used) : null;
      M.mount(this, `${M.RING_CSS}
        .main { display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
        .info { flex: 1 1 180px; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
        .line { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
        .line b { font-size: 18px; font-variant-numeric: tabular-nums; }
        .bar > span { background: linear-gradient(90deg, #ffb74d, ${col}); box-shadow: 0 0 10px ${col}88; }
        .ring-v { font-size: 17px; }
        .sunwrap { filter: drop-shadow(0 0 10px ${col}55); }
      `, `
        <ha-card>
          <div class="hd"><span class="hd-dot"></span><span class="hd-title">${M.esc(c.title || t.title)}</span>
            <div class="hd-right">${vs !== null ? M.pill(`${vs.toFixed(0)}% ${t.of}`, vs >= 80 ? M.COLORS.good : col) : ""}</div></div>
          <div class="main">
            <div class="sunwrap">${M.ring(pvW === null ? 0 : pvW / maxW, col, 112, c.pv_entity ? M.fmtPower(pvW) : "–", t.now)}</div>
            <div class="info">
              <div class="line"><span class="muted">${t.used}</span><b>${M.fmtKwh(used, 2)}</b></div>
              <div class="line"><span class="muted">${t.pred}</span><b>${M.fmtKwh(pred, 2)}</b></div>
              <div class="bar"><span style="width:${vs !== null ? Math.min(100, vs) : 0}%"></span></div>
              <div class="line"><span class="muted">${t.left}</span><span class="muted">${M.fmtKwh(left, 2)}</span></div>
            </div>
          </div>
        </ha-card>`, col);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-solar-card", MiniEMSSolarCard, "miniEMS Solar", "Live PV power plus today's production vs. forecast")
    : setTimeout(reg, 50);
  reg();
})();
