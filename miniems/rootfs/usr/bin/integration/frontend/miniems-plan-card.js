/* miniEMS Energy Plan timeline card.
 *
 * Visualizes energy_plan.py's compute_energy_plan() output (via
 * sensor.miniems_energy_plan_deficit_kwh and its attributes) as a
 * horizontal timeline of the grid-charge windows planned for tonight,
 * in the same self-contained custom-element style as miniems-flow-card.js
 * (no Lit, no build step).
 */
(() => {
  function fmt(v, dec = 0) {
    return v !== null && v !== undefined && !Number.isNaN(v) ? Number(v).toFixed(dec) : "–";
  }

  function fmtTime(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? "–"
      : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  const CARD_CSS = `
    ha-card { padding: 0.5rem 1rem 0.75rem; }
    .section-title { font-size: 0.9rem; color: var(--secondary-text-color); margin: 0 0 0.5rem; }
    .summary { font-size: 0.85rem; color: var(--primary-text-color); margin-bottom: 0.75rem; }
    .summary .cost { font-weight: 700; }
    .reason { font-size: 0.8rem; color: var(--secondary-text-color); font-style: italic; }
    .timeline { display: flex; width: 100%; height: 2.5rem; border-radius: 6px; overflow: hidden;
                background: var(--secondary-background-color, #2a2a2a); }
    .window {
      background: #58a6ff; border-right: 1px solid var(--card-background-color, #1a1a1a);
      display: flex; align-items: center; justify-content: center;
      font-size: 0.65rem; color: #0a0a0a; white-space: nowrap; overflow: hidden;
      min-width: 2px;
    }
    .window:last-child { border-right: none; }
    .timeline-labels { display: flex; justify-content: space-between;
                        font-size: 0.7rem; color: var(--secondary-text-color); margin-top: 0.25rem; }
    .empty { font-size: 0.85rem; color: var(--secondary-text-color); }
  `;

  class MiniEMSPlanCard extends HTMLElement {
    setConfig(config) {
      if (!config || typeof config !== "object" || !config.entity) {
        throw new Error("miniems-plan-card: 'entity' is required");
      }
      this._config = config;
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
      return { entity: "sensor.miniems_energy_plan_deficit_kwh" };
    }

    _render() {
      if (!this._config || !this._hass || !this.shadowRoot) return;

      const st = this._hass.states[this._config.entity];
      const title = this._config.title || "Energy Plan";

      if (!st) {
        this.shadowRoot.innerHTML = `
          <ha-card>
            <style>${CARD_CSS}</style>
            <p class="section-title">${title}</p>
            <p class="empty">Entity ${this._config.entity} not found.</p>
          </ha-card>
        `;
        return;
      }

      const attrs = st.attributes || {};
      const deficitKwh = parseFloat(st.state);
      const windows = Array.isArray(attrs.windows) ? attrs.windows : [];
      const feasible = attrs.feasible !== false;
      const reason = attrs.reason || "";
      const costEur = attrs.estimated_cost_eur;

      const summaryHtml = `
        <div class="summary">
          Deficit: ${fmt(deficitKwh, 2)} kWh
          ${costEur !== undefined && costEur !== null ? ` · Est. cost: <span class="cost">€${fmt(costEur, 2)}</span>` : ""}
        </div>
      `;

      if (windows.length === 0) {
        this.shadowRoot.innerHTML = `
          <ha-card>
            <style>${CARD_CSS}</style>
            <p class="section-title">${title}</p>
            ${summaryHtml}
            <p class="reason">${reason || (feasible ? "Nothing planned." : "Plan not feasible.")}</p>
          </ha-card>
        `;
        return;
      }

      const starts = windows.map((w) => new Date(w.start).getTime());
      const ends = windows.map((w) => new Date(w.end).getTime());
      const rangeStart = Math.min(...starts);
      const rangeEnd = Math.max(...ends);
      const rangeMs = Math.max(1, rangeEnd - rangeStart);

      const segments = windows
        .map((w) => {
          const s = new Date(w.start).getTime();
          const e = new Date(w.end).getTime();
          const widthPct = ((e - s) / rangeMs) * 100;
          const title = `${fmtTime(w.start)}–${fmtTime(w.end)} · ${fmt(w.rate_eur_kwh * 100, 1)} ct/kWh · ${fmt(w.energy_kwh, 2)} kWh`;
          return `<div class="window" style="width:${widthPct}%" title="${title}">${fmt(w.energy_kwh, 1)}</div>`;
        })
        .join("");

      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CARD_CSS}</style>
          <p class="section-title">${title}</p>
          ${summaryHtml}
          <div class="timeline">${segments}</div>
          <div class="timeline-labels">
            <span>${fmtTime(windows[0].start)}</span>
            <span>${fmtTime(windows[windows.length - 1].end)}</span>
          </div>
          ${!feasible && reason ? `<p class="reason">${reason}</p>` : ""}
        </ha-card>
      `;
    }
  }

  if (!customElements.get("miniems-plan-card")) {
    customElements.define("miniems-plan-card", MiniEMSPlanCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "miniems-plan-card",
      name: "miniEMS Energy Plan",
      description: "Timeline of tonight's planned grid-charge windows",
      preview: false,
    });
  }
})();
