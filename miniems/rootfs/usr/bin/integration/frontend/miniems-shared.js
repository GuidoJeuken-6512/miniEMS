/* miniEMS shared UI helpers.
 *
 * Loaded before the cards (registration order in __init__.py). Cards read
 * `window.MiniEMS` lazily at render time, so a different load order only
 * delays the first paint, it never throws.
 *
 * Visual language follows the SEM Community integration's dashboard: glass
 * cards with a faint dot grid and a soft accent glow in the top-left corner,
 * rounded "tiles" for values, uppercase micro-labels, and one fixed colour
 * per energy flow. Everything takes its surfaces and text from the active
 * Home Assistant theme, so light and dark themes both work.
 */
(() => {
  if (window.MiniEMS) return;

  const COLORS = {
    solar: "#ff9800",
    gridIn: "#488fc2",
    gridOut: "#8353d1",
    charge: "#f06292",
    discharge: "#4db6ac",
    home: "#5bc8d8",
    inverter: "#96caee",
    good: "#8dc892",
    warn: "#e0b04a",
    bad: "#ef6b63",
  };

  const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(v);

  const fmtNum = (v, dec = 1) => (isNum(v) ? Number(v).toFixed(dec) : "–");
  const fmtKwh = (v, dec = 1) => (isNum(v) ? `${Number(v).toFixed(dec)} kWh` : "–");
  const fmtEur = (v, dec = 2) => (isNum(v) ? `${Number(v).toFixed(dec)} €` : "–");
  const fmtPct = (v, dec = 0) => (isNum(v) ? `${Number(v).toFixed(dec)}%` : "–");
  const fmtPower = (w) => {
    if (!isNum(w)) return "–";
    return Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${Math.round(w)} W`;
  };
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const lang = (hass) => ((hass && hass.language) || "en").startsWith("de") ? "de" : "en";

  function state(hass, id) {
    const st = id ? hass?.states?.[id] : null;
    if (!st || st.state === "unavailable" || st.state === "unknown") return null;
    return st.state;
  }
  function num(hass, id) {
    const s = state(hass, id);
    if (s === null) return null;
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
  }

  // inline stroke icons, drawn around 0,0 in a -24..24 box
  const ICONS = {
    home: `<path d="M-18,2 L0,-15 L18,2"/><rect x="-13" y="2" width="26" height="19" rx="2"/><rect x="-4" y="11" width="8" height="10"/>`,
    battery: `<rect x="-9" y="-14" width="18" height="29" rx="3.5"/><rect x="-3.5" y="-18" width="7" height="4" rx="1.5" fill="currentColor" opacity="0.45" stroke="none"/><path d="M-4,-3 L4,-3 M-4,4 L4,4" opacity="0.5"/>`,
    plan: `<rect x="-16" y="-14" width="32" height="30" rx="4"/><path d="M-16,-5 L16,-5 M-8,-18 L-8,-10 M8,-18 L8,-10"/><path d="M-9,5 L-3,5 M1,5 L9,5 M-9,11 L5,11" opacity="0.6"/>`,
    costs: `<circle r="16"/><path d="M6,-8 Q1,-12 -3,-8 Q-8,-3 0,0 Q8,3 3,9 Q-1,13 -7,9 M0,-13 L0,-10 M0,10 L0,13" opacity="0.85"/>`,
    system: `<path d="M-17,5 L-9,-7 L-1,1 L6,-11 L10,-3 L17,5"/><path d="M-17,10 L17,10" opacity="0.3"/>`,
    bolt: `<path d="M-3,-17 L-9,2 L-2,2 L-5,17 L11,-4 L2,-4 L7,-17 Z"/>`,
  };

  const TABS = {
    home:    { color: COLORS.home,     icon: "home" },
    battery: { color: COLORS.discharge, icon: "battery" },
    plan:    { color: COLORS.gridIn,    icon: "plan" },
    costs:   { color: COLORS.charge,    icon: "costs" },
    system:  { color: COLORS.inverter,  icon: "system" },
  };

  // SEM-style glass chrome. Cards set `--accent` (and `--glow`) on :host.
  const BASE_CSS = `
    :host { display: block; --accent: ${COLORS.home}; }
    * { box-sizing: border-box; }
    ha-card {
      display: block; color: var(--primary-text-color);
      position: relative; overflow: hidden; padding: 16px 18px 18px;
      font-family: var(--paper-font-body1_-_font-family, "Segoe UI", Roboto, system-ui, sans-serif);
      background:
        radial-gradient(ellipse 80% 70% at 12% 0%, color-mix(in srgb, var(--accent) 11%, transparent) 0%, transparent 70%),
        radial-gradient(circle at 2px 2px, rgba(128,128,128,0.08) 0.7px, transparent 0.7px),
        var(--ha-card-background, var(--card-background-color, #1c1c1c));
      background-size: 100% 100%, 46px 46px, auto;
      border: 1px solid var(--divider-color, rgba(255,255,255,0.12));
      border-radius: var(--ha-card-border-radius, 18px);
    }
    .hd { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; min-width: 0; }
    .hd-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent);
              box-shadow: 0 0 10px var(--accent); flex-shrink: 0; }
    .hd-title { font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
                color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .hd-right { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 10px; }
    .tile {
      min-width: 0; padding: 10px 12px; border-radius: 14px;
      background: var(--secondary-background-color, rgba(255,255,255,0.06));
      border: 1px solid var(--divider-color, rgba(255,255,255,0.08));
      -webkit-backdrop-filter: blur(16px) saturate(160%); backdrop-filter: blur(16px) saturate(160%);
    }
    .k { font-size: 10.5px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
         color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .v { font-size: 19px; font-weight: 700; margin-top: 3px; font-variant-numeric: tabular-nums;
         color: var(--primary-text-color); white-space: nowrap; }
    .v small { font-size: 12px; font-weight: 600; opacity: 0.7; margin-left: 2px; }
    .v.good { color: ${COLORS.good}; } .v.bad { color: ${COLORS.bad}; } .v.warn { color: ${COLORS.warn}; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px; border-radius: 999px;
      font-size: 12px; font-weight: 600; white-space: nowrap; color: var(--c, var(--primary-text-color));
      background: color-mix(in srgb, var(--c, #888) 14%, transparent);
      border: 1px solid color-mix(in srgb, var(--c, #888) 38%, transparent);
    }
    .pill i { width: 7px; height: 7px; border-radius: 50%; background: var(--c, #888); box-shadow: 0 0 6px var(--c, #888); }
    .bar { height: 8px; border-radius: 999px; overflow: hidden; display: flex;
           background: color-mix(in srgb, var(--primary-text-color, #fff) 10%, transparent); }
    .bar > span { display: block; height: 100%; transition: width 0.8s ease; }
    .muted { color: var(--secondary-text-color); font-size: 12px; }
    .empty { color: var(--secondary-text-color); font-size: 13px; padding: 6px 0; }
    @media (prefers-reduced-motion: reduce) { .bar > span { transition: none; } }
    @media (max-width: 420px) { ha-card { padding: 14px 14px 16px; } .v { font-size: 17px; } }
  `;

  // Re-rendering replaces the whole shadow DOM, which restarts CSS/SMIL
  // animations – so skip it when the markup did not change.
  function mount(el, css, body, accent) {
    if (!el.shadowRoot) el.attachShadow({ mode: "open" });
    const out = `<style>${BASE_CSS}${css || ""}${accent ? `:host{--accent:${accent}}` : ""}</style>${body}`;
    if (el._miniemsHtml === out) return false;
    el._miniemsHtml = out;
    el.shadowRoot.innerHTML = out;
    return true;
  }

  const pill = (text, color) =>
    `<span class="pill" style="--c:${color}"><i></i>${esc(text)}</span>`;
  const tile = (label, value, cls = "") =>
    `<div class="tile"><div class="k">${esc(label)}</div><div class="v ${cls}">${value}</div></div>`;

  // progress ring, 0..1
  function ring(frac, color, size, center, sub) {
    const r = 36, circ = 2 * Math.PI * r;
    const f = isNum(frac) ? Math.min(1, Math.max(0, frac)) : 0;
    return `
      <div class="ring" style="width:${size}px;height:${size}px">
        <svg viewBox="0 0 88 88">
          <circle cx="44" cy="44" r="${r}" fill="none" stroke="${color}" stroke-opacity="0.16" stroke-width="7"/>
          <circle cx="44" cy="44" r="${r}" fill="none" stroke="${color}" stroke-width="7" stroke-linecap="round"
                  stroke-dasharray="${circ.toFixed(1)}" stroke-dashoffset="${(circ * (1 - f)).toFixed(1)}"
                  transform="rotate(-90 44 44)" style="filter:drop-shadow(0 0 5px ${color});transition:stroke-dashoffset 1s ease"/>
        </svg>
        <div class="ring-c"><div class="ring-v" style="color:${color}">${center}</div>${sub ? `<div class="ring-s">${esc(sub)}</div>` : ""}</div>
      </div>`;
  }
  const RING_CSS = `
    .ring { position: relative; flex-shrink: 0; }
    .ring svg { width: 100%; height: 100%; display: block; }
    .ring-c { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
    .ring-v { font-size: 20px; font-weight: 800; line-height: 1.1; font-variant-numeric: tabular-nums; }
    .ring-s { font-size: 10px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--secondary-text-color); margin-top: 2px; }
  `;

  function register(tag, cls, name, description) {
    if (customElements.get(tag)) return;
    customElements.define(tag, cls);
    window.customCards = window.customCards || [];
    window.customCards.push({ type: tag, name, description, preview: false });
  }

  window.MiniEMS = {
    COLORS, ICONS, TABS, RING_CSS, fmtNum, fmtKwh, fmtEur, fmtPct, fmtPower, esc, lang,
    state, num, mount, pill, tile, ring, register, isNum,
  };
})();
