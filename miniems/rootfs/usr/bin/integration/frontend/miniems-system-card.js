/* miniEMS System card – inverter write health, today's efficiency and the
 * grid-charge bookkeeping, as a compact list of status rows.
 */
(() => {
  const TXT = {
    de: { title: "System", inverter: "Wechselrichter-Schreibzugriff", eff: "Wirkungsgrad heute", tier: "Aktuelle Tarifstufe", mode: "Betriebsmodus",
          gc: "Netzladung heute", gcCost: "Kosten Netzladung", roi: "ROI Netzladung", base: "Basispreis heute", pred: "Prognose Verbrauch", rem: "Restverbrauch heute" },
    en: { title: "System", inverter: "Inverter write access", eff: "Efficiency today", tier: "Current tariff tier", mode: "Operating mode",
          gc: "Grid charge today", gcCost: "Grid-charge cost", roi: "Grid-charge ROI", base: "Base price today", pred: "Predicted load", rem: "Remaining load today" },
  };
  const INV = { ok: "#8dc892", warning: "#e0b04a", error: "#ef6b63" };
  const TIER = { low: "#8dc892", medium: "#e0b04a", high: "#ef6b63" };

  class MiniEMSSystemCard extends HTMLElement {
    setConfig(config) { this._p = (config && config.entity_prefix) || "sensor.miniems_"; this._config = config || {}; }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 4; }
    static getStubConfig() { return {}; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const h = this._hass, t = TXT[M.lang(h)], C = M.COLORS;
      const n = (s) => M.num(h, this._p + s), st = (s) => M.state(h, this._p + s);
      const inv = st("inverter_write_status"), tier = st("price_tier"), eff = n("today_efficiency_pct");
      const row = (k, v) => `<div class="row"><span>${M.esc(k)}</span><b>${v}</b></div>`;
      M.mount(this, `
        .head { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
        .row { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; font-size: 13px;
               border-top: 1px solid var(--divider-color, rgba(255,255,255,0.1)); }
        .row span { color: var(--secondary-text-color); }
        .row b { font-variant-numeric: tabular-nums; text-align: right; }
        .effbar { margin: 0 0 6px; }
        .effbar > span { background: linear-gradient(90deg, ${C.gridIn}, ${C.good}); }
      `, `
        <ha-card>
          <div class="hd"><span class="hd-dot"></span><span class="hd-title">${M.esc(this._config.title || t.title)}</span></div>
          <div class="head">
            ${inv ? M.pill(`${t.inverter}: ${inv}`, INV[inv] || "#8b949e") : ""}
            ${tier ? M.pill(`${t.tier}: ${tier}`, TIER[tier] || "#8b949e") : ""}
          </div>
          <div class="muted" style="margin-bottom:6px">${t.eff}: <b style="color:var(--primary-text-color)">${M.fmtPct(eff, 1)}</b></div>
          <div class="bar effbar"><span style="width:${eff === null ? 0 : Math.min(100, eff)}%"></span></div>
          ${row(t.mode, M.esc(st("mode") || "–"))}
          ${row(t.gc, M.fmtKwh(n("today_grid_charge_kwh_bilanz") ?? n("today_grid_charge_kwh"), 2))}
          ${row(t.gcCost, M.fmtEur(n("today_grid_charge_cost_bilanz_eur")))}
          ${row(t.roi, M.fmtEur(n("today_grid_charge_roi_eur")))}
          ${row(t.base, M.fmtEur(n("today_base_price_eur")))}
          ${row(t.pred, M.fmtKwh(n("predicted_load_kwh"), 1))}
          ${row(t.rem, M.fmtKwh(n("remaining_load_kwh"), 1))}
        </ha-card>`, C.inverter);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-system-card", MiniEMSSystemCard, "miniEMS System", "Inverter health, efficiency and grid-charge bookkeeping")
    : setTimeout(reg, 50);
  reg();
})();
