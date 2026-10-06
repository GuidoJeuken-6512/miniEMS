/* miniEMS Costs card – period tabs (today / week / month / year), headline
 * tiles, tariff-tier split and the dynamic-vs-fixed tariff comparison that
 * cost_optimizer.py computes. Uses only already-published miniEMS sensors.
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
    de: {
      title: "Kosten", today: "Heute", week: "Woche", month: "Monat", year: "Jahr",
      cost: "Netzkosten", saved: "PV-Ersparnis", feed: "Einspeise-Erlös", load: "Verbrauchskosten",
      tiers: "Netzbezug nach Tarifstufe", low: "niedrig", medium: "mittel", high: "hoch",
      cmp: "Tarifvergleich heute", dyn: "Dynamisch (ohne Netzladung)", fix: "Fixpreis (hypothetisch)",
      win: "Ersparnis durch dynamischen Tarif", lose: "Mehrkosten durch dynamischen Tarif", roi: "ROI Netzladung heute",
    },
    en: {
      title: "Costs", today: "Today", week: "Week", month: "Month", year: "Year",
      cost: "Grid cost", saved: "PV savings", feed: "Feed-in revenue", load: "Load cost",
      tiers: "Grid import by tariff tier", low: "low", medium: "medium", high: "high",
      cmp: "Tariff comparison today", dyn: "Dynamic (without grid charge)", fix: "Fixed price (hypothetical)",
      win: "Saved by dynamic tariff", lose: "Extra cost of dynamic tariff", roi: "Grid-charge ROI today",
    },
  };
  const PERIODS = ["today", "week", "month", "year"];

  class MiniEMSCostsCard extends HTMLElement {
    setConfig(config) {
      const p = (config && config.entity_prefix) || "sensor.miniems_";
      this._p = p;
      this._config = { period: "today", ...config };
      this._period = PERIODS.includes(this._config.period) ? this._config.period : "today";
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 4; }
    static getStubConfig() { return {}; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const h = this._hass, t = TXT[M.lang(h)], C = M.COLORS, per = this._period;
      const n = (s) => M.num(h, this._p + s);
      const cost = n(`${per}_grid_cost_eur`), sav = n(`${per}_pv_savings_eur`);
      const feed = per === "today" ? n("today_feed_in_revenue_eur") : null;
      const load = per === "today" || per === "month" || per === "year" ? n(`${per}_load_cost_eur`) : null;

      const hi = per === "today" || per === "month" ? n(`${per}_kwh_high_rate`) : null;
      const me = per === "today" || per === "month" ? n(`${per}_kwh_medium_rate`) : null;
      const lo = per === "today" || per === "month" ? n(`${per}_kwh_low_rate`) : null;
      const tot = (hi || 0) + (me || 0) + (lo || 0);
      const seg = (v, col, label) => `<span style="width:${tot ? (v / tot) * 100 : 0}%;background:${col}" title="${label}: ${M.fmtKwh(v)}"></span>`;
      const tierBlock = tot > 0 ? `
        <div class="sect">${t.tiers}</div>
        <div class="bar">${seg(lo || 0, C.good, t.low)}${seg(me || 0, C.warn, t.medium)}${seg(hi || 0, C.bad, t.high)}</div>
        <div class="legend">
          <span><i style="background:${C.good}"></i>${t.low} ${M.fmtKwh(lo)}</span>
          <span><i style="background:${C.warn}"></i>${t.medium} ${M.fmtKwh(me)}</span>
          <span><i style="background:${C.bad}"></i>${t.high} ${M.fmtKwh(hi)}</span>
        </div>` : "";

      const dyn = n("today_cost_without_grid_charge"), fix = n("today_cost_fix_price_tariff"), roi = n("today_grid_charge_roi_eur");
      let cmp = "";
      if (per === "today" && dyn !== null && fix !== null) {
        const mx = Math.max(dyn, fix, 0.01), delta = fix - dyn;
        cmp = `
          <div class="sect">${t.cmp}</div>
          <div class="cmp"><span class="muted">${t.dyn}</span><b>${M.fmtEur(dyn)}</b></div>
          <div class="bar"><span style="width:${(dyn / mx) * 100}%;background:${C.gridIn}"></span></div>
          <div class="cmp"><span class="muted">${t.fix}</span><b>${M.fmtEur(fix)}</b></div>
          <div class="bar"><span style="width:${(fix / mx) * 100}%;background:#8b949e"></span></div>
          <div class="delta ${delta >= 0 ? "good" : "bad"}">${delta >= 0 ? t.win : t.lose}: ${M.fmtEur(Math.abs(delta))}</div>
          ${roi !== null ? `<div class="cmp"><span class="muted">${t.roi}</span><b>${M.fmtEur(roi)}</b></div>` : ""}`;
      }

      M.mount(this, `
        .tabs { display: flex; gap: 4px; padding: 3px; border-radius: 999px; margin-left: auto;
                background: var(--secondary-background-color, rgba(255,255,255,0.06)); }
        .tabs button { all: unset; cursor: pointer; padding: 4px 11px; border-radius: 999px; font-size: 12px; font-weight: 600;
                       color: var(--secondary-text-color); }
        .tabs button:focus-visible { outline: 2px solid var(--accent); }
        .tabs button.on { color: var(--primary-text-color); background: color-mix(in srgb, var(--accent) 24%, transparent);
                          box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 50%, transparent); }
        .sect { font-size: 10.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
                color: var(--secondary-text-color); margin: 18px 0 8px; }
        .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; font-size: 12px; color: var(--secondary-text-color); }
        .legend i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
        .cmp { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; margin: 8px 0 4px; }
        .cmp b { font-variant-numeric: tabular-nums; }
        .delta { font-size: 13px; font-weight: 700; margin: 10px 0 6px; }
        .delta.good { color: ${C.good}; } .delta.bad { color: ${C.bad}; }
      `, `
        <ha-card>
          <div class="hd"><span class="hd-dot"></span><span class="hd-title">${M.esc(this._config.title || t.title)}</span>
            <div class="tabs" role="tablist">${PERIODS.map((p) =>
              `<button data-p="${p}" role="tab" aria-selected="${p === per}" class="${p === per ? "on" : ""}">${t[p]}</button>`).join("")}</div></div>
          <div class="tiles">
            ${M.tile(t.cost, M.fmtEur(cost))}
            ${M.tile(t.saved, M.fmtEur(sav), "good")}
            ${feed !== null ? M.tile(t.feed, M.fmtEur(feed), "good") : ""}
            ${load !== null ? M.tile(t.load, M.fmtEur(load)) : ""}
          </div>
          ${tierBlock}${cmp}
        </ha-card>`, C.charge);

      this.shadowRoot.querySelectorAll(".tabs button").forEach((b) =>
        b.addEventListener("click", () => { this._period = b.dataset.p; this._render(); }));
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-costs-card", MiniEMSCostsCard, "miniEMS Costs", "Costs and savings by period, tariff-tier split and dynamic-vs-fixed comparison")
    : setTimeout(reg, 50);
  reg();
})();
