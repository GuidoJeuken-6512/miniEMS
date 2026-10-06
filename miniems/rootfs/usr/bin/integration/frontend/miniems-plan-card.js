/* miniEMS Energy Plan card – grid-charge windows from energy_plan.py
 * (sensor.miniems_energy_plan_deficit_kwh and its attributes) on a time
 * axis, each window coloured by its price relative to the cheapest/dearest
 * window of the plan.
 */
(() => {
  const TXT = {
    de: { title: "Energieplan", deficit: "Defizit", cost: "Geschätzte Kosten", windows: "Fenster", none: "Nichts geplant.",
          infeasible: "Plan nicht umsetzbar.", notfound: "Entität nicht gefunden:", cheap: "günstig", dear: "teuer", ct: "ct/kWh" },
    en: { title: "Energy plan", deficit: "Deficit", cost: "Estimated cost", windows: "Windows", none: "Nothing planned.",
          infeasible: "Plan not feasible.", notfound: "Entity not found:", cheap: "cheap", dear: "expensive", ct: "ct/kWh" },
  };
  const mix = (a, b, f) => a.map((v, i) => Math.round(v + (b[i] - v) * f));

  class MiniEMSPlanCard extends HTMLElement {
    setConfig(config) {
      if (!config || typeof config !== "object" || !config.entity) {
        throw new Error("miniems-plan-card: 'entity' is required");
      }
      this._config = config;
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 3; }
    static getStubConfig() { return { entity: "sensor.miniems_energy_plan_deficit_kwh" }; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const h = this._hass, t = TXT[M.lang(h)], C = M.COLORS;
      const title = M.esc(this._config.title || t.title);
      const head = `<div class="hd"><span class="hd-dot"></span><span class="hd-title">${title}</span></div>`;
      const st = h.states[this._config.entity];
      if (!st) {
        M.mount(this, "", `<ha-card>${head}<div class="empty">${t.notfound} ${M.esc(this._config.entity)}</div></ha-card>`, C.gridIn);
        return;
      }
      const a = st.attributes || {};
      const windows = Array.isArray(a.windows) ? a.windows : [];
      const feasible = a.feasible !== false;
      const time = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "–" :
        d.toLocaleTimeString(h.language || undefined, { hour: "2-digit", minute: "2-digit" }); };
      const deficit = parseFloat(st.state);
      const tiles = `<div class="tiles">
        ${M.tile(t.deficit, M.fmtKwh(Number.isNaN(deficit) ? null : deficit, 2))}
        ${M.tile(t.cost, M.fmtEur(a.estimated_cost_eur ?? null))}
        ${M.tile(t.windows, String(windows.length))}</div>`;

      if (!windows.length) {
        M.mount(this, "", `<ha-card>${head}${tiles}<div class="empty" style="margin-top:12px">${M.esc(a.reason || (feasible ? t.none : t.infeasible))}</div></ha-card>`, C.gridIn);
        return;
      }

      const ts = windows.map((w) => [new Date(w.start).getTime(), new Date(w.end).getTime()]);
      const lo = Math.min(...ts.map((x) => x[0])), hi = Math.max(...ts.map((x) => x[1])), span = Math.max(1, hi - lo);
      const rates = windows.map((w) => Number(w.rate_eur_kwh) || 0);
      const rMin = Math.min(...rates), rMax = Math.max(...rates);
      const colorFor = (r) => {
        const f = rMax > rMin ? (r - rMin) / (rMax - rMin) : 0;
        const rgb = f < 0.5 ? mix([141, 200, 146], [224, 176, 74], f * 2) : mix([224, 176, 74], [239, 107, 99], (f - 0.5) * 2);
        return `rgb(${rgb.join(",")})`;
      };
      // hour ticks across the plan range
      const ticks = [];
      for (let ms = Math.ceil(lo / 3600000) * 3600000; ms <= hi; ms += 3600000) {
        const left = ((ms - lo) / span) * 100;
        if (left > 2 && left < 98) ticks.push(`<i style="left:${left}%"></i><em style="left:${left}%">${new Date(ms).toLocaleTimeString(h.language || undefined, { hour: "2-digit" })}</em>`);
      }
      const segs = windows.map((w, i) => {
        const left = ((ts[i][0] - lo) / span) * 100, width = Math.max(0.8, ((ts[i][1] - ts[i][0]) / span) * 100);
        const tip = `${time(w.start)}–${time(w.end)} · ${M.fmtNum(rates[i] * 100, 1)} ${t.ct} · ${M.fmtKwh(w.energy_kwh, 2)}`;
        return `<div class="seg" style="left:${left}%;width:${width}%;background:${colorFor(rates[i])}" title="${M.esc(tip)}"><span>${M.fmtNum(w.energy_kwh, 1)}</span></div>`;
      }).join("");

      M.mount(this, `
        .axis { position: relative; height: 46px; margin-top: 16px; border-radius: 10px;
                background: var(--secondary-background-color, rgba(255,255,255,0.06)); overflow: hidden; }
        .axis i { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--divider-color, rgba(255,255,255,0.12)); }
        .axis em { position: absolute; bottom: 2px; transform: translateX(3px); font-style: normal; font-size: 9.5px; color: var(--secondary-text-color); }
        .seg { position: absolute; top: 6px; bottom: 16px; border-radius: 6px; display: flex; align-items: center; justify-content: center;
               font-size: 11px; font-weight: 700; color: #10151b; box-shadow: 0 0 12px rgba(0,0,0,0.25); overflow: hidden; min-width: 4px; }
        .ends { display: flex; justify-content: space-between; margin-top: 6px; font-size: 12px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .key { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 11px; color: var(--secondary-text-color); }
        .key b { flex: 0 0 90px; height: 6px; border-radius: 3px; background: linear-gradient(90deg, ${C.good}, ${C.warn}, ${C.bad}); }
        .reason { margin-top: 10px; font-size: 12.5px; font-style: italic; color: var(--secondary-text-color); }
      `, `<ha-card>${head}${tiles}
          <div class="axis">${ticks.join("")}${segs}</div>
          <div class="ends"><span>${time(windows[0].start)}</span><span>${time(windows[windows.length - 1].end)}</span></div>
          <div class="key"><span>${t.cheap}</span><b></b><span>${t.dear}</span></div>
          ${!feasible && a.reason ? `<div class="reason">${M.esc(a.reason)}</div>` : ""}
        </ha-card>`, C.gridIn);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-plan-card", MiniEMSPlanCard, "miniEMS Energy Plan", "Timeline of tonight's planned grid-charge windows")
    : setTimeout(reg, 50);
  reg();
})();
