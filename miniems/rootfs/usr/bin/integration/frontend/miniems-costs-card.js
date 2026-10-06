/* miniEMS Costs card – layout after SEM's costs card: net hero, one column per
 * period (cash flow + avoided cost), tariff comparison and tariff-tier split.
 * Only already-published miniEMS sensors; missing values show as "–". */
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
      title: "Kosten", netSaving: "Nettoersparnis heute", netCost: "Nettokosten heute", noData: "Keine Kostendaten",
      today: "Heute", week: "Woche", month: "Monatlich", year: "Jährlich",
      flow: "Geldfluss", grid: "Bezugskosten", feed: "Einspeiseerlös", net: "Nettoersparnis", netc: "Nettokosten",
      avoided: "Vermiedene Kosten", solar: "PV-Ersparnis", load: "Verbrauch zum Tarif",
      note: "Vermiedene Kosten sind Geld, das du nicht ausgegeben hast. Es steckt schon im Geldfluss oben, also nicht addieren.",
      tariff: "Tarifvergleich heute", dyn: "Dynamisch (ohne Netzladung)", fix: "Fixpreis (hypothetisch)",
      win: "Ersparnis durch dynamischen Tarif", lose: "Mehrkosten durch dynamischen Tarif",
      charge: "Netzladung", roi: "ROI Netzladung", chargedKwh: "Netzgeladen", baseP: "Basispreis",
      tiers: "Netzbezug nach Tarifstufe", low: "niedrig", medium: "mittel", high: "hoch", today2: "heute", month2: "Monat",
    },
    en: {
      title: "Costs", netSaving: "Net saving today", netCost: "Net cost today", noData: "No cost data",
      today: "Today", week: "Week", month: "Monthly", year: "Yearly",
      flow: "Cash flow", grid: "Grid cost", feed: "Feed-in revenue", net: "Net saving", netc: "Net cost",
      avoided: "Avoided cost", solar: "PV savings", load: "Consumption at tariff",
      note: "Avoided cost is money you did not spend. It is already part of the cash flow above, so do not add it up.",
      tariff: "Tariff comparison today", dyn: "Dynamic (without grid charge)", fix: "Fixed price (hypothetical)",
      win: "Saved by dynamic tariff", lose: "Extra cost of dynamic tariff",
      charge: "Grid charging", roi: "Grid-charge ROI", chargedKwh: "Grid-charged", baseP: "Base price",
      tiers: "Grid import by tariff tier", low: "low", medium: "medium", high: "high", today2: "today", month2: "month",
    },
  };
  const PERIODS = ["today", "week", "month", "year"];

  class MiniEMSCostsCard extends HTMLElement {
    setConfig(config) {
      this._p = (config && config.entity_prefix) || "sensor.miniems_";
      this._config = config || {};
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 6; }
    static getStubConfig() { return {}; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const h = this._hass, t = TXT[M.lang(h)], C = M.COLORS;
      const n = (s) => M.num(h, this._p + s);
      const eur = (v) => M.fmtEur(v);
      const row = (label, val, color, strong) =>
        `<div class="mr${strong ? " net" : ""}"><span>${M.esc(label)}</span><b${color ? ` style="color:${color}"` : ""}>${val}</b></div>`;

      // net = feed-in revenue - grid cost (only where both exist: today)
      const grid0 = n("today_grid_cost_eur"), feed0 = n("today_feed_in_revenue_eur");
      const net0 = grid0 !== null ? (feed0 || 0) - grid0 : null;
      const saving = net0 !== null && net0 >= 0;
      const heroColor = net0 === null ? "var(--secondary-text-color)" : saving ? C.good : C.charge;
      const hero = net0 === null ? "–" : `${saving ? "+" : "−"}${Math.abs(net0).toFixed(2)} €`;

      const section = (per) => {
        const grid = n(`${per}_grid_cost_eur`), sav = n(`${per}_pv_savings_eur`);
        const feed = per === "today" ? feed0 : null;
        const load = per === "today" || per === "month" || per === "year" ? n(`${per}_load_cost_eur`) : null;
        const net = per === "today" ? net0 : null;
        const avoided = sav;
        return `
          <section class="sec">
            <h3>${t[per]}</h3>
            <h4>${t.flow}</h4>
            ${row(t.grid, eur(grid), C.gridIn)}
            ${feed !== null ? row(t.feed, eur(feed), C.gridOut) : ""}
            ${net !== null ? row(net >= 0 ? t.net : t.netc, `${net >= 0 ? "+" : "−"}${Math.abs(net).toFixed(2)} €`, net >= 0 ? C.good : C.charge, true) : ""}
            <h4>${t.avoided}</h4>
            ${row(t.solar, eur(avoided), C.solar)}
            ${load !== null ? row(t.load, eur(load)) : ""}
          </section>`;
      };

      const dyn = n("today_cost_without_grid_charge"), fix = n("today_cost_fix_price_tariff");
      const roi = n("today_grid_charge_roi_eur"), gcKwh = n("today_grid_charge_kwh_bilanz") ?? n("today_grid_charge_kwh");
      let cmp = `<div class="mr"><span>${t.dyn}</span><b>${eur(dyn)}</b></div>
                 <div class="mr"><span>${t.fix}</span><b>${eur(fix)}</b></div>`;
      if (dyn !== null && fix !== null) {
        const mx = Math.max(dyn, fix, 0.01), d = fix - dyn;
        cmp = `
          <div class="mr"><span>${t.dyn}</span><b>${eur(dyn)}</b></div>
          <div class="bar"><span style="width:${(dyn / mx) * 100}%;background:${C.gridIn}"></span></div>
          <div class="mr"><span>${t.fix}</span><b>${eur(fix)}</b></div>
          <div class="bar"><span style="width:${(fix / mx) * 100}%;background:#8b949e"></span></div>
          <div class="delta" style="color:${d >= 0 ? C.good : C.bad}">${d >= 0 ? t.win : t.lose}: ${eur(Math.abs(d))}</div>`;
      }
      const tierCol = (per) => [n(`${per}_kwh_low_rate`), n(`${per}_kwh_medium_rate`), n(`${per}_kwh_high_rate`)];
      const tierBlock = (per, label) => {
        const [lo, me, hi] = tierCol(per), tot = (lo || 0) + (me || 0) + (hi || 0);
        if (!tot) return "";
        const seg = (v, c) => `<span style="width:${((v || 0) / tot) * 100}%;background:${c}"></span>`;
        return `<div class="mr" style="margin-top:8px"><span>${label}</span><b>${M.fmtKwh(tot)}</b></div>
          <div class="bar">${seg(lo, C.good)}${seg(me, C.warn)}${seg(hi, C.bad)}</div>
          <div class="legend"><span><i style="background:${C.good}"></i>${t.low} ${M.fmtKwh(lo)}</span><span><i style="background:${C.warn}"></i>${t.medium} ${M.fmtKwh(me)}</span><span><i style="background:${C.bad}"></i>${t.high} ${M.fmtKwh(hi)}</span></div>`;
      };

      M.mount(this, `
        .hero { text-align: center; padding: 2px 0 6px; }
        .hero b { display: block; font-size: 32px; font-weight: 800; line-height: 1.1; font-variant-numeric: tabular-nums;
                  text-shadow: 0 0 14px color-mix(in srgb, currentColor 30%, transparent); }
        .hero span { font-size: 12px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--secondary-text-color); }
        .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin-top: 12px; }
        .two { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 10px; }
        .sec { min-width: 0; padding: 12px 14px; border-radius: 14px;
               background: var(--secondary-background-color, rgba(255,255,255,0.06));
               border: 1px solid var(--divider-color, rgba(255,255,255,0.1)); }
        .sec h3 { margin: 0 0 6px; font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: ${C.good}; }
        .sec h4 { margin: 12px 0 3px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase; color: var(--secondary-text-color); opacity: 0.85; }
        .mr { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; padding: 3px 0; font-size: 13px; }
        .mr span { color: var(--secondary-text-color); min-width: 0; }
        .mr b { font-variant-numeric: tabular-nums; white-space: nowrap; }
        .mr.net { margin-top: 4px; padding-top: 6px; border-top: 1px solid var(--divider-color, rgba(255,255,255,0.12)); }
        .mr.net span { color: var(--primary-text-color); font-weight: 700; }
        .note { margin: 14px 0 0; font-size: 11.5px; line-height: 1.4; color: var(--secondary-text-color); opacity: 0.8; }
        .bar { margin: 3px 0 6px; }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; font-size: 11.5px; color: var(--secondary-text-color); }
        .legend i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
        .delta { font-size: 13px; font-weight: 700; margin: 8px 0 4px; }
      `, `
        <ha-card>
          <div class="hero" style="color:${heroColor}"><b>${hero}</b><span>${net0 === null ? t.noData : saving ? t.netSaving : t.netCost}</span></div>
          <div class="cols">${PERIODS.map(section).join("")}</div>
          <p class="note">${t.note}</p>
          <div class="two">
            <section class="sec"><h3>${t.tariff}</h3>${cmp}
              ${gcKwh !== null || roi !== null ? `<h4>${t.charge}</h4>${gcKwh !== null ? row(t.chargedKwh, M.fmtKwh(gcKwh, 2)) : ""}${roi !== null ? row(t.roi, eur(roi), roi >= 0 ? C.good : C.bad) : ""}` : ""}
            </section>
            <section class="sec"><h3>${t.tiers}</h3>${tierBlock("today", t.today2)}${tierBlock("month", t.month2)}</section>
          </div>
        </ha-card>`, C.charge);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-costs-card", MiniEMSCostsCard, "miniEMS Costs", "Cash flow and avoided cost by period, tariff comparison and tariff-tier split")
    : setTimeout(reg, 50);
  reg();
})();
