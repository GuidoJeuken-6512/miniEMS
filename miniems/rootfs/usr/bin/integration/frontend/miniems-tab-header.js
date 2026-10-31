/* miniEMS Tab Header card – section header with glow icon, title and three
 * live stats, placed at the top of every dashboard tab (same idea as SEM's
 * sem-tab-header). Config: tab: home | battery | plan | costs | system.
 *
 * Home tab: also the "hot water ad hoc" toggle button, shown once
 * sensor.miniems_hot_water_mode exists (hot-water control configured in the
 * addon). It calls miniems.hot_water_adhoc (start / cancel), stays pressed
 * while the sensor's adhoc_active attribute is true and shows no text next to
 * it (setpoint, shut-off and elapsed time are its tooltip). Optional config:
 * hot_water: false hides it.
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
      home: ["Haus", "Energiefluss und Status"],
      battery: ["Batterie", "Kapazität und Ladereserve"],
      plan: ["Energieplan", "Geplante Netzladung für die Nacht"],
      costs: ["Kosten", "Netzkosten, Ersparnis und Tarifvergleich"],
      system: ["System", "Wechselrichter und Effizienz"],
      mode: "Modus", autarky: "Autarkie", pvUsed: "PV genutzt", usable: "Nutzbar", free: "Frei",
      cap: "Kapazität", deficit: "Defizit", cost: "Kosten", windows: "Fenster", gridCost: "Netzkosten",
      saved: "Ersparnis", feedIn: "Einspeisung", inverter: "Wechselrichter", eff: "Wirkungsgrad", tier: "Tarifstufe",
      hwBtn: "WW Ad-hoc-Bereitung", hwActive: "Ad-hoc-Ladung aktiv", hwSetpoint: "Sollwert", hwOffAt: "aus bei",
      hwBoiler: "Boiler", hwCancel: "zum Abbrechen erneut klicken", hwSending: "wird gesendet …",
      hwAlready: "Speicher bereits auf Abschalttemperatur", hwSim: "Simulation – es wird nichts geschrieben",
      hwOff: "Steuerung aus – es wird nichts geschrieben", hwMin: "min",
    },
    en: {
      home: ["Home", "Energy flow and status"],
      battery: ["Battery", "Capacity and charge reserve"],
      plan: ["Energy plan", "Planned grid charging for tonight"],
      costs: ["Costs", "Grid cost, savings and tariff comparison"],
      system: ["System", "Inverter and efficiency"],
      mode: "Mode", autarky: "Autarky", pvUsed: "PV used", usable: "Usable", free: "Free",
      cap: "Capacity", deficit: "Deficit", cost: "Cost", windows: "Windows", gridCost: "Grid cost",
      saved: "Saved", feedIn: "Feed-in", inverter: "Inverter", eff: "Efficiency", tier: "Tariff tier",
      hwBtn: "Hot water now (ad hoc)", hwActive: "Ad-hoc charge active", hwSetpoint: "setpoint", hwOffAt: "off at",
      hwBoiler: "Boiler", hwCancel: "click again to cancel", hwSending: "sending …",
      hwAlready: "boiler already at shut-off temperature", hwSim: "simulation – nothing is written",
      hwOff: "control off – nothing is written", hwMin: "min",
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

    // ── hot-water ad-hoc button (home tab) ─────────────────────────────
    _hwState() {
      if (this._config.tab !== "home" || this._config.hot_water === false) return null;
      const st = this._hass?.states?.[`${this._p}hot_water_mode`];
      if (!st || st.state === "unavailable" || st.state === "unknown") return null;
      return st.attributes || {};
    }

    _hwHtml(t, a) {
      const M = window.MiniEMS, fmt = (v) => (M.isNum(v) ? `${Number(v).toFixed(1)} °C` : "–");
      // a pending click wins until the sensor reflects it (or 15 s pass)
      if (this._pending && (a.adhoc_active === (this._pending === "start") || Date.now() - this._pendingAt > 15000)) {
        this._pending = null;
      }
      const busy = Boolean(this._pending);
      const pressed = busy ? this._pending === "start" : Boolean(a.adhoc_active);
      const already = !a.adhoc_active && M.isNum(a.boil_temp_c) && M.isNum(a.adhoc_target_c)
        && a.boil_temp_c >= a.adhoc_target_c;
      let info;
      if (busy) info = t.hwSending;
      else if (a.adhoc_active) {
        info = `${t.hwActive} · ${t.hwSetpoint} ${fmt(a.adhoc_setpoint_c)} · ${t.hwOffAt} ${fmt(a.adhoc_target_c)}`
          + ` · ${Math.round((a.adhoc_elapsed_sec || 0) / 60)} ${t.hwMin} · ${t.hwCancel}`;
      } else if (already) info = `${t.hwAlready} (${fmt(a.adhoc_target_c)})`;
      else {
        info = `${t.hwSetpoint} ${fmt(a.adhoc_setpoint_c)} (${t.hwBoiler} ${fmt(a.boil_temp_c)} + `
          + `${M.isNum(a.adhoc_delta_k) ? Math.round(a.adhoc_delta_k) : "–"} K) · ${t.hwOffAt} ${fmt(a.adhoc_target_c)}`;
      }
      const noWrite = a.control_enabled === false ? t.hwOff : (a.simulation ? t.hwSim : "");
      if (noWrite) info += ` · ${noWrite}`;
      return `
        <div class="hw">
          <button class="hw-btn${pressed ? " pressed" : ""}" aria-pressed="${pressed}" title="${M.esc(info)}"
                  data-action="${a.adhoc_active ? "cancel" : "start"}" ${busy ? "disabled" : ""}>
            <span class="led"></span>${M.esc(t.hwBtn)}</button>
          ${this._err ? `<span class="hw-err">${M.esc(this._err)}</span>` : ""}
        </div>`;
    }

    async _hwClick(action) {
      if (this._pending) return;
      this._pending = action;
      this._pendingAt = Date.now();
      this._err = "";
      this._render();
      try {
        await this._hass.callService("miniems", "hot_water_adhoc", { action });
      } catch (e) {
        this._err = (e && e.message) || String(e);
        this._pending = null;
      }
      this._render();
    }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const t = TXT[M.lang(this._hass)];
      const hw = this._hwState();
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
        .hw { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 14px 0 2px; }
        .hw-btn { display: inline-flex; align-items: center; gap: 8px; cursor: pointer;
                  background: #1f6feb; color: #fff; border: none; border-radius: 6px;
                  padding: 8px 20px; font: inherit; font-size: 14px; font-weight: 600;
                  transition: transform .06s, box-shadow .06s, background .15s; }
        .hw-btn:hover:not(:disabled) { background: #388bfd; }
        .hw-btn:active:not(:disabled) { transform: translateY(1px); }
        .hw-btn.pressed { background: #0d419d; transform: translateY(1px); box-shadow: inset 0 3px 6px rgba(0,0,0,.5); }
        .hw-btn.pressed:hover:not(:disabled) { background: #1158c7; }
        .hw-btn:disabled { cursor: wait; opacity: .8; }
        .led { width: 9px; height: 9px; border-radius: 50%; background: rgba(255,255,255,.35); }
        .pressed .led { background: #ff7b72; box-shadow: 0 0 6px #ff7b72; animation: led 1.2s ease-in-out infinite; }
        @keyframes led { 50% { opacity: .35; } }
        @media (prefers-reduced-motion: reduce) { .pressed .led { animation: none; } }
        .hw-err { font-size: 12.5px; color: var(--error-color, #ef6b63); }
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
        </div>${hw ? this._hwHtml(t, hw) : ""}</ha-card>`, tab.color);
      // one delegated listener survives every innerHTML re-render
      if (this.shadowRoot && !this._hwBound) {
        this._hwBound = true;
        this.shadowRoot.addEventListener("click", (ev) => {
          const btn = ev.target.closest && ev.target.closest(".hw-btn");
          if (btn && !btn.disabled) this._hwClick(btn.dataset.action);
        });
      }
    }
  }

  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-tab-header", MiniEMSTabHeader, "miniEMS Tab Header", "Section header with live stats for each dashboard tab")
    : setTimeout(reg, 50);
  reg();
})();
