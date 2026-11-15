/* miniEMS Timeline card (C3) – every plan on one time axis: battery action,
 * grid charge, hot water, EV and heating, with price and PV forecast in the
 * background. Reads the `timeline` attribute of sensor.miniems_next_action;
 * the sentence of the next action is the card's subtitle.
 */
(() => {
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
    de: { title: "Plan", price: "Preis", next: "Als Nächstes", none: "Kein Plan.", notfound: "Entität nicht gefunden:",
          rows: { battery: "Akku", grid_charge: "Netzladen", hot_water: "Warmwasser", ev: "E-Auto", heating: "Heizung" },
          segs: { auto: "Eigenverbr.", hold: "halten", charge: "laden", export: "einspeisen", preheat: "vorheizen", lower: "absenken" },
          inputs: "Grundlage", prices: "Preise bis", load: "Verbrauch" },
    en: { title: "Plan", price: "Price", next: "Next", none: "No plan.", notfound: "Entity not found:",
          rows: { battery: "Battery", grid_charge: "Grid charge", hot_water: "Hot water", ev: "EV", heating: "Heating" },
          segs: { auto: "self-use", hold: "hold", charge: "charge", export: "export", preheat: "pre-heat", lower: "lower" },
          inputs: "Based on", prices: "Prices until", load: "Load" },
  };
  const SEG = { auto: "rgba(139,148,158,0.45)", hold: "#e0b04a", charge: "#5aa0ff", export: "#8dc892",
                preheat: "#ef6b63", lower: "#7fc3e8" };

  class MiniEMSTimelineCard extends HTMLElement {
    setConfig(config) { this._config = { entity: "sensor.miniems_next_action", ...(config || {}) }; }
    set hass(hass) { this._hass = hass; this._render(); }
    getCardSize() { return 4; }
    static getStubConfig() { return { entity: "sensor.miniems_next_action" }; }

    _render() {
      const M = window.MiniEMS;
      if (!M || !this._config || !this._hass) return;
      const h = this._hass, t = TXT[M.lang(h)], C = M.COLORS;
      const head = `<div class="hd"><span class="hd-dot"></span><span class="hd-title">${M.esc(this._config.title || t.title)}</span></div>`;
      const st = h.states[this._config.entity];
      if (!st) { M.mount(this, "", `<ha-card>${head}<div class="empty">${t.notfound} ${M.esc(this._config.entity)}</div></ha-card>`, C.gridIn); return; }
      const tl = (st.attributes || {}).timeline || {};
      const bg = tl.background || [], rows = tl.rows || {};
      const spans = [...bg.map((s) => [s.start, s.end]), ...Object.values(rows).flat().map((s) => [s.start, s.end])]
        .map((x) => [new Date(x[0]).getTime(), new Date(x[1]).getTime()]).filter((x) => !Number.isNaN(x[0]));
      const next = st.state && st.state !== "unknown" ? `<div class="next">${t.next}: ${M.esc(st.state)}</div>` : "";
      if (!spans.length) { M.mount(this, "", `<ha-card>${head}${next}<div class="empty">${t.none}</div></ha-card>`, C.gridIn); return; }
      const lo = Math.min(...spans.map((x) => x[0])), hi = Math.max(...spans.map((x) => x[1])), span = Math.max(1, hi - lo);
      const pos = (iso) => ((new Date(iso).getTime() - lo) / span) * 100;
      const time = (ms) => new Date(ms).toLocaleTimeString(h.language || undefined, { hour: "2-digit", minute: "2-digit" });
      const prices = bg.map((s) => s.price_eur_kwh).filter((v) => typeof v === "number");
      const pMin = Math.min(...prices), pMax = Math.max(...prices);
      const pvMax = Math.max(0.001, ...bg.map((s) => s.pv_kwh || 0));
      const bars = (fn) => bg.map(fn).join("");
      const row = (label, inner) => `<div class="row"><div class="lbl">${M.esc(label)}</div><div class="trk">${inner}</div></div>`;
      let body = row(t.price, bars((s) => {
        if (typeof s.price_eur_kwh !== "number") return "";
        const f = pMax > pMin ? (s.price_eur_kwh - pMin) / (pMax - pMin) : 0.5;
        const col = f < 0.33 ? C.good : f < 0.66 ? C.warn : C.bad;
        return `<i title="${M.fmtNum(s.price_eur_kwh * 100, 1)} ct" style="left:${pos(s.start)}%;width:${Math.max(0.4, pos(s.end) - pos(s.start))}%;height:${25 + 75 * f}%;background:${col}"></i>`;
      }));
      body += row("PV", bars((s) => `<i title="${M.fmtKwh(s.pv_kwh, 2)}" style="left:${pos(s.start)}%;width:${Math.max(0.4, pos(s.end) - pos(s.start))}%;height:${100 * (s.pv_kwh || 0) / pvMax}%;background:#f5c542"></i>`));
      for (const [key, segs] of Object.entries(rows)) {
        body += row(t.rows[key] || key, segs.map((s) => {
          const left = pos(s.start), width = Math.max(0.6, pos(s.end) - pos(s.start));
          const label = t.segs[s.label] || s.label;
          return `<b title="${M.esc(label)}${s.energy_kwh ? " · " + M.fmtKwh(s.energy_kwh, 1) : ""}" style="left:${left}%;width:${width}%;background:${SEG[s.label] || C.gridIn}">${width > 7 ? M.esc(label) : ""}</b>`;
        }).join(""));
      }
      const pi = tl.inputs || {};
      const inputs = pi.prices_until || pi.pv || pi.load ? `<div class="inp">${t.inputs}: ${t.prices} ${pi.prices_until ? time(new Date(pi.prices_until).getTime()) : "–"} · PV ${M.esc(pi.pv || "–")} · ${t.load} ${M.esc(pi.load || "–")}</div>` : "";
      M.mount(this, `
        .next { margin: 4px 0 10px; font-size: 13px; }
        .row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
        .lbl { flex: 0 0 78px; font-size: 11.5px; color: var(--secondary-text-color); }
        .trk { position: relative; flex: 1; height: 20px; border-radius: 6px; background: var(--secondary-background-color, rgba(255,255,255,0.06)); overflow: hidden; }
        .trk i { position: absolute; bottom: 0; opacity: .55; border-radius: 2px 2px 0 0; }
        .trk b { position: absolute; top: 2px; bottom: 2px; border-radius: 4px; font-size: 10px; font-weight: 600; color: #10151b;
                 display: flex; align-items: center; justify-content: center; overflow: hidden; white-space: nowrap; }
        .ends { display: flex; justify-content: space-between; margin-left: 86px; font-size: 11px; color: var(--secondary-text-color); }
        .inp { margin-top: 8px; font-size: 11.5px; color: var(--secondary-text-color); }
      `, `<ha-card>${head}${next}${body}<div class="ends"><span>${time(lo)}</span><span>${time(hi)}</span></div>${inputs}</ha-card>`, C.gridIn);
    }
  }
  const reg = () => window.MiniEMS
    ? window.MiniEMS.register("miniems-timeline-card", MiniEMSTimelineCard, "miniEMS Timeline", "Every plan on one time axis")
    : setTimeout(reg, 50);
  reg();
})();
