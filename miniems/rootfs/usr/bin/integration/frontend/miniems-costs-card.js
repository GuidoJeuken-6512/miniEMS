/* miniEMS Costs card.
 *
 * Combines what SEM Community splits across sem-price-card + sem-costs-card
 * into one panel, using only already-published miniEMS sensors (no
 * user-configured entity mapping needed, unlike the flow/solar cards —
 * these are all addon-native computed values).
 *
 * Highlight: today_cost_without_grid_charge vs. today_cost_fix_price_tariff
 * – cost_optimizer.py computes the fix-price comparison specifically to
 * answer "would a flat tariff have been cheaper than today's dynamic one",
 * but nothing has displayed that comparison until now.
 *
 * Same plain-HTMLElement style as the other miniems-*-card.js files.
 */
(() => {
  function fmtEur(v, dec = 2) {
    return v !== null && v !== undefined && !Number.isNaN(v) ? `${Number(v).toFixed(dec)} €` : "–";
  }

  const CARD_CSS = `
    ha-card { padding: 0.5rem 1rem 0.75rem; }
    .section-title { font-size: 0.9rem; color: var(--secondary-text-color); margin: 0 0 0.6rem; }
    .headline-row { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
    .headline { flex: 1; min-width: 100px; }
    .headline-label { font-size: 0.7rem; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: 0.04em; }
    .headline-value { font-size: 1.2rem; font-weight: 700; color: var(--primary-text-color); }
    .headline-value.good { color: #3fb950; }
    .headline-value.bad { color: #f85149; }
    .compare {
      background: var(--secondary-background-color, #21262d); border-radius: 8px;
      padding: 0.6rem 0.75rem; margin-bottom: 0.75rem;
    }
    .compare-row { display: flex; justify-content: space-between; font-size: 0.85rem; padding: 0.1rem 0; }
    .compare-row .label { color: var(--secondary-text-color); }
    .compare-row .value { font-weight: 600; }
    .compare-delta { font-size: 0.85rem; font-weight: 700; margin-top: 0.3rem; }
    .compare-delta.good { color: #3fb950; }
    .compare-delta.bad { color: #f85149; }
    .trend-table { width: 100%; font-size: 0.8rem; border-collapse: collapse; }
    .trend-table th { text-align: right; color: var(--secondary-text-color); font-weight: 600; padding: 0.2rem 0.4rem; }
    .trend-table th:first-child { text-align: left; }
    .trend-table td { text-align: right; padding: 0.2rem 0.4rem; font-weight: 600; }
    .trend-table td:first-child { text-align: left; color: var(--secondary-text-color); font-weight: 400; }
  `;

  class MiniEMSCostsCard extends HTMLElement {
    setConfig(config) {
      const p = (config && config.entity_prefix) || "sensor.miniems_";
      this._config = {
        today_cost_entity: `${p}today_grid_cost_eur`,
        today_pv_savings_entity: `${p}today_pv_savings_eur`,
        today_feed_in_revenue_entity: `${p}today_feed_in_revenue_eur`,
        today_cost_without_grid_charge_entity: `${p}today_cost_without_grid_charge`,
        today_cost_fix_price_entity: `${p}today_cost_fix_price_tariff`,
        today_grid_charge_roi_entity: `${p}today_grid_charge_roi_eur`,
        week_cost_entity: `${p}week_grid_cost_eur`,
        week_savings_entity: `${p}week_pv_savings_eur`,
        month_cost_entity: `${p}month_grid_cost_eur`,
        month_savings_entity: `${p}month_pv_savings_eur`,
        year_cost_entity: `${p}year_grid_cost_eur`,
        year_savings_entity: `${p}year_pv_savings_eur`,
        ...config,
      };
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    getCardSize() {
      return 3;
    }

    static getStubConfig() {
      return {};
    }

    _num(entityId) {
      const st = this._hass?.states[entityId];
      if (!st || st.state === "unavailable" || st.state === "unknown") return null;
      const n = parseFloat(st.state);
      return Number.isFinite(n) ? n : null;
    }

    _render() {
      if (!this._config || !this._hass || !this.shadowRoot) return;
      const c = this._config;
      const title = c.title || "Kosten";

      const todayCost = this._num(c.today_cost_entity);
      const pvSavings = this._num(c.today_pv_savings_entity);
      const feedInRevenue = this._num(c.today_feed_in_revenue_entity);

      const withoutGridCharge = this._num(c.today_cost_without_grid_charge_entity);
      const fixPrice = this._num(c.today_cost_fix_price_entity);
      const dynamicVsFix =
        withoutGridCharge !== null && fixPrice !== null ? fixPrice - withoutGridCharge : null;

      const roi = this._num(c.today_grid_charge_roi_entity);

      const rows = [
        ["Woche", c.week_cost_entity, c.week_savings_entity],
        ["Monat", c.month_cost_entity, c.month_savings_entity],
        ["Jahr", c.year_cost_entity, c.year_savings_entity],
      ].map(([label, costEnt, savEnt]) => {
        const cost = this._num(costEnt);
        const sav = this._num(savEnt);
        return `<tr><td>${label}</td><td>${fmtEur(cost)}</td><td>${fmtEur(sav)}</td></tr>`;
      }).join("");

      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CARD_CSS}</style>
          <p class="section-title">${title}</p>
          <div class="headline-row">
            <div class="headline">
              <div class="headline-label">Netzkosten heute</div>
              <div class="headline-value">${fmtEur(todayCost)}</div>
            </div>
            <div class="headline">
              <div class="headline-label">PV-Ersparnis</div>
              <div class="headline-value good">${fmtEur(pvSavings)}</div>
            </div>
            <div class="headline">
              <div class="headline-label">Einspeise-Erlös</div>
              <div class="headline-value good">${fmtEur(feedInRevenue)}</div>
            </div>
          </div>
          <div class="compare">
            <div class="compare-row">
              <span class="label">Dynamischer Tarif (ohne Netzladung)</span>
              <span class="value">${fmtEur(withoutGridCharge)}</span>
            </div>
            <div class="compare-row">
              <span class="label">Fixpreistarif (hypothetisch)</span>
              <span class="value">${fmtEur(fixPrice)}</span>
            </div>
            ${dynamicVsFix !== null
              ? `<div class="compare-delta ${dynamicVsFix >= 0 ? "good" : "bad"}">
                   ${dynamicVsFix >= 0 ? "Ersparnis" : "Mehrkosten"} durch dynamischen Tarif: ${fmtEur(Math.abs(dynamicVsFix))}
                 </div>`
              : ""}
            ${roi !== null
              ? `<div class="compare-row" style="margin-top:0.4rem">
                   <span class="label">ROI Netzladung heute</span>
                   <span class="value ${roi >= 0 ? "" : ""}">${fmtEur(roi)}</span>
                 </div>`
              : ""}
          </div>
          <table class="trend-table">
            <tr><th></th><th>Netzkosten</th><th>PV-Ersparnis</th></tr>
            ${rows}
          </table>
        </ha-card>
      `;
    }
  }

  if (!customElements.get("miniems-costs-card")) {
    customElements.define("miniems-costs-card", MiniEMSCostsCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "miniems-costs-card",
      name: "miniEMS Costs",
      description: "Today's costs/savings, dynamic-vs-fixed-tariff comparison, week/month/year trend",
      preview: false,
    });
  }
})();
