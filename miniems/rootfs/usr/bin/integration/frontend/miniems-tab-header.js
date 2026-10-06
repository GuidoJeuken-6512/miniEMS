/* miniEMS Tab Header card – section header with glow icon, title and three
 * live stats, placed at the top of every dashboard tab (same idea as SEM's
 * sem-tab-header). Config: tab: home | battery | plan | costs | system.
 */
(() => {
  const TXT = {
    de: {
      home: ["Übersicht", "Energiefluss und Status"],
      battery: ["Batterie", "Kapazität und Ladereserve"],
      plan: ["Energieplan", "Geplante Netzladung für die Nacht"],
      costs: ["Kosten", "Netzkosten, Ersparnis und Tarifvergleich"],
      system: ["System", "Wechselrichter und Effizienz"],
      mode: "Modus", autarky: "Autarkie", pvUsed: "PV genutzt", usable: "Nutzbar", free: "Frei",
      cap: "Kapazität", deficit: "Defizit", cost: "Kosten", windows: "Fenster", gridCost: "Netzkosten",
      saved: "Ersparnis", feedIn: "Einspeisung", inverter: "Wechselrichter", eff: "Wirkungsgrad", tier: "Tarifstufe",
    },
    en: {
      home: ["Overview", "Energy flow and status"],
      battery: ["Battery", "Capacity and charge reserve"],
      plan: ["Energy plan", "Planned grid charging for tonight"],
      costs: ["Costs", "Grid cost, savings and tariff comparison"],
      system: ["System", "Inverter and efficiency"],
      mode: "Mode", autarky: "Autarky", pvUsed: "PV used", usable: "Usable", free: "Free",
      cap: "Capacity", deficit: "Deficit", cost: "Cost", windows: "Windows", gridCost: "Grid cost",
      saved: "Saved", feedIn: "Feed-in", inverter: "Inverter", eff: "Efficiency", tier: "Tariff tier",
    },
  };

  class MiniEMSTabHeader extends HTMLElement {
    setConfig(config) {
      const p = (config && config.entity_prefix) || "sensor.miniems_";
      this._config = { tab: "home", entity_prefix: p, ...config };
      this._p = p;
      this._el = this._el || {};
    }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 1; }
    static getStubConfig() { return { tab: "home" }; }

    _stats(t) {
      const M = window.MiniEMS, h = this._hass, n = (s) => M.num(h, this._p + s);
      const st = (s) => M.state(h, this._p + s);
      switch (this._config.tab) {
        case "home": {
          const load = n("today_load_total_kwh"), imp = n("today_grid_import_kwh");
          const aut = load > 0 && imp !== null ? Math.max(0, Math.min(100, (1 - imp / load) * 100)) : null;
          return [[t.mode, st("mode") || "–"], [t.autarky, M.fmtPct(aut)], [t.pvUsed, M.fmtKwh(n("today_pv_used_kwh"))]];
        }
        case "battery":
          return [[t.usable, M.fmtKwh(n("battery_kwh_useable"))], [t.free, M.fmtKwh(n("battery_kwh_freetochange"))], [t.cap, M.fmtKwh(n("battery_capacity_kwh"))]];
        case "plan": {
          const e = h?.states?.[`${this._p}energy_plan_deficit_kwh`];
          const w = Array.isArray(e?.attributes?.windows) ? e.attributes.windows.length : null;
          const c = e?.attributes?.estimated_cost_eur;
          return [[t.deficit, M.fmtKwh(n("energy_plan_deficit_kwh"))], [t.cost, M.fmtEur(c === undefined ? null : c)], [t.windows, w === null ? "–" : String(w)]];
        }
        case "costs":
          return [[t.gridCost, M.fmtEur(n("today_grid_cost_eur"))], [t.saved, M.fmtEur(n("today_pv_savings_eur"))], [t.feedIn, M.fmtEur(n("today_feed_in_revenue_eur"))]];
        default:
          return [[t.inverter, st("inverter_write_status") || "–"], [t.eff, M.fmtPct(n("today_efficiency_pct"))], [t.tier, st("price_tier") || "–"]];
      }
    }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const t = TXT[M.lang(this._hass)];
      const tab = M.TABS[this._config.tab] || M.TABS.home;
      const [title0, sub0] = t[this._config.tab] || t.home;
      const title = this._config.title || title0, sub = this._config.subtitle || sub0;
      const stats = this._stats(t).map(([k, v]) =>
        `<div class="stat"><div class="sv">${M.esc(v)}</div><div class="k">${M.esc(k)}</div></div>`).join("");
      M.mount(this, `
        ha-card { padding: 14px 18px; }
        .row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
        .icon { width: 58px; height: 58px; flex-shrink: 0; }
        .icon svg { width: 100%; height: 100%; display: block; overflow: visible; }
        .ttl { flex: 1 1 150px; min-width: 0; }
        .t { font-size: 21px; font-weight: 700; letter-spacing: 0.02em; color: var(--accent);
             text-shadow: 0 0 14px color-mix(in srgb, var(--accent) 35%, transparent); }
        .s { font-size: 12.5px; color: var(--secondary-text-color); margin-top: 2px; }
        .stats { display: flex; gap: 18px; flex-wrap: wrap; }
        .stat { text-align: center; min-width: 56px; }
        .sv { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--primary-text-color); white-space: nowrap; }
        @media (max-width: 480px) { .icon { width: 46px; height: 46px; } .t { font-size: 18px; } .stats { gap: 12px; } }
        @media (prefers-reduced-motion: reduce) { .pulse { animation: none !important; } }
        .pulse { animation: pulse 3s ease-in-out infinite; transform-origin: center; }
        @keyframes pulse { 50% { opacity: 0.35; transform: scale(1.07); } }
      `, `
        <ha-card><div class="row">
          <div class="icon"><svg viewBox="-34 -34 68 68" aria-hidden="true">
            <circle class="pulse" r="30" fill="none" stroke="${tab.color}" stroke-width="1.2" opacity="0.28"/>
            <circle r="25" fill="${tab.color}" fill-opacity="0.07" stroke="${tab.color}" stroke-width="1.5"
                    style="filter:drop-shadow(0 0 5px ${tab.color})"/>
            <g stroke="${tab.color}" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.85" transform="scale(0.78)">${M.ICONS[tab.icon]}</g>
          </svg></div>
          <div class="ttl"><div class="t">${M.esc(title)}</div><div class="s">${M.esc(sub)}</div></div>
          <div class="stats">${stats}</div>
        </div></ha-card>`, tab.color);
    }
  }

  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-tab-header", MiniEMSTabHeader, "miniEMS Tab Header", "Section header with live stats for each dashboard tab")
    : setTimeout(reg, 50);
  reg();
})();
