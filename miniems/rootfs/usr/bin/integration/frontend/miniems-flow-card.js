/* miniEMS Power Flow card.
 *
 * Illustrated energy flow in the style of the SEM Community integration's
 * flow card: solar panel -> DC/AC inverter -> house, grid pylon and a
 * battery with fill level, connected by curved dashed lines with travelling
 * dots, plus a sun that wanders along an arc with the time of day (driven by
 * HA's own `sun.sun` entity). Plain custom element (no Lit, no build step) so
 * it can be dropped into any dashboard.
 *
 * miniEMS deliberately does not publish its own pv_power_w/battery_power_w/
 * grid_power_w/battery_soc_pct sensors (see integration/sensor.py – that
 * would duplicate the user's own Deye/meter entities). This card therefore
 * takes those as explicit entity references in its config.
 *
 * Required: pv_entity, battery_power_entity, battery_soc_entity,
 *           grid_entity, load_entity
 * Optional: title, sun_entity (default sun.sun), inverter_label,
 *           pv_today_entity, load_today_entity,
 *           grid_import_today_entity, grid_export_today_entity,
 *           battery_charge_today_entity, battery_discharge_today_entity,
 *           pv_forecast_entity (kWh, shown under the sun value),
 *           max_power_w (reference for dot speed, default 5000)
 *
 * Flow direction follows the app's own sign convention
 * (docs/technical/calculations.md): grid_power_w > 0 = import,
 * battery_power_w > 0 = discharge.
 */
(() => {
  const REQUIRED_FIELDS = [
    "pv_entity",
    "battery_power_entity",
    "battery_soc_entity",
    "grid_entity",
    "load_entity",
  ];

  const C = {
    solar: "#ff9f1c",
    home: "#5ccfe6",
    gridIn: "#4f9bff",
    gridOut: "#b48cff",
    charge: "#ff5c9a",
    discharge: "#3ddbc0",
    sun: "#e9b72f",
  };
  const FLOW_MIN_W = 10; // below this, treat as "no flow" (avoids sensor-noise flicker)

  const TXT = {
    de: { solar: "Solar", home: "Haus", grid: "Netz", battery: "Batterie",
          today: "heute", import: "Netzbezug", export: "Einspeisung",
          charging: "Laden", discharging: "Entladen", idle: "Bereit",
          forecast: "Prognose", inverter: "Wechselrichter" },
    en: { solar: "Solar", home: "Home", grid: "Grid", battery: "Battery",
          today: "today", import: "Import", export: "Export",
          charging: "Charging", discharging: "Discharging", idle: "Idle",
          forecast: "Forecast", inverter: "Inverter" },
  };

  // node centres in the 480×430 viewBox
  const P = {
    solar:   { x: 78,  y: 168, r: 44 },
    inv:     { x: 205, y: 190 },
    home:    { x: 305, y: 215, r: 46 },
    grid:    { x: 412, y: 300, r: 42 },
    battery: { x: 205, y: 338, r: 40 },
  };
  // sun arc (quadratic bezier) – leaves room above the nodes
  const ARC = { p0: [34, 78], p1: [240, -34], p2: [446, 78] };

  const CARD_CSS = `
    :host { display: block; }
    ha-card { padding: 0.5rem 0.5rem 0.75rem; overflow: hidden; }
    .title { font-size: 0.9rem; color: var(--secondary-text-color); margin: 0.25rem 0.75rem 0; }
    svg { width: 100%; height: auto; display: block; }
    text { font-family: var(--paper-font-common-base_-_font-family, system-ui, sans-serif); }
    .name  { font-size: 12px; font-weight: 700; }
    .value { font-size: 19px; font-weight: 800; }
    .sub   { font-size: 10.5px; font-weight: 500; opacity: 0.8; }
    .small { font-size: 10.5px; fill: var(--secondary-text-color); }
    .halo  { fill: var(--card-background-color, #1c1c1c); }
    .ring  { fill: none; stroke-width: 1.4; opacity: 0.5; }
    .track { fill: none; stroke-width: 2; stroke-linecap: round; stroke-dasharray: 5 7; }
    .track.idle { opacity: 0.28; }
    .track.live { opacity: 0.75; }
    .dot { filter: drop-shadow(0 0 3px currentColor); }
    .arc { fill: none; stroke: ${C.sun}; stroke-width: 1.4; stroke-dasharray: 2 6; stroke-linecap: round; opacity: 0.45; }
    .rays { transform-box: fill-box; transform-origin: center; animation: spin 40s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .rays { animation: none; } }
  `;

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function fmtPower(w) {
    if (w === null || w === undefined || Number.isNaN(w)) return "–";
    const a = Math.abs(w);
    return a >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${Math.round(w)} W`;
  }
  function fmtKwh(v) {
    return v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(1);
  }
  const bez = (t) => {
    const u = 1 - t;
    return [
      u * u * ARC.p0[0] + 2 * u * t * ARC.p1[0] + t * t * ARC.p2[0],
      u * u * ARC.p0[1] + 2 * u * t * ARC.p1[1] + t * t * ARC.p2[1],
    ];
  };

  // ── illustrations (drawn around 0,0, translated into place) ─────────────
  function iconSolar(active) {
    const cells = [];
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) {
        cells.push(`<rect x="${-22 + c * 15}" y="${-20 + r * 11}" width="13" height="9.5" rx="1" fill="url(#pvcell)"/>`);
      }
    }
    return `
      <rect x="-25" y="-23" width="50" height="38" rx="2.5" fill="#0b2a52" stroke="${C.solar}" stroke-width="2"/>
      ${cells.join("")}
      <path d="M-14,15 L-20,26 M14,15 L20,26 M-22,26 L22,26" stroke="${C.solar}" stroke-width="2" stroke-linecap="round" fill="none"/>
      ${active ? `<rect x="-25" y="-23" width="50" height="38" rx="2.5" fill="none" stroke="${C.solar}" stroke-width="5" opacity="0.18"/>` : ""}`;
  }

  function iconHome(color) {
    return `
      <path d="M-26,-2 L0,-26 L26,-2 Z" fill="#d1453b" stroke="#ff7a6e" stroke-width="1.5" stroke-linejoin="round"/>
      <rect x="14" y="-26" width="6" height="12" fill="#3a4a5c"/>
      <rect x="-21" y="-2" width="42" height="28" fill="#33475e" stroke="${color}" stroke-width="1.5"/>
      <rect x="-16" y="3" width="10" height="9" rx="1" fill="#ffd27a"/>
      <rect x="6" y="3" width="10" height="9" rx="1" fill="#ffd27a"/>
      <rect x="-4" y="11" width="8" height="15" rx="1" fill="#1d2a3a" stroke="${color}" stroke-width="1"/>`;
  }

  function iconGrid(color) {
    return `
      <g stroke="${color}" stroke-width="2.2" stroke-linecap="round" fill="none">
        <path d="M0,-30 L0,26 M-9,26 L9,26"/>
        <path d="M-20,-16 L20,-16 M-14,-4 L14,-4"/>
        <path d="M-20,-16 Q-32,-8 -40,-14 M20,-16 Q32,-8 40,-14" opacity="0.7" stroke-width="1.6"/>
      </g>
      <path d="M2,-12 L-3,-1 L1,-1 L-2,9 L5,-3 L1,-3 Z" fill="${C.solar}"/>`;
  }

  function iconBattery(soc, color) {
    const pct = Math.max(0, Math.min(100, soc ?? 0));
    const h = (pct / 100) * 34;
    return `
      <rect x="-5" y="-27" width="10" height="4" rx="1.5" fill="${color}"/>
      <rect x="-14" y="-23" width="28" height="44" rx="4" fill="none" stroke="${color}" stroke-width="2"/>
      <rect x="-11" y="${(18 - h).toFixed(1)}" width="22" height="${h.toFixed(1)}" rx="2" fill="${color}" opacity="0.85"/>
      <text x="0" y="1" text-anchor="middle" font-size="10.5" font-weight="800" fill="#fff"
            stroke="#000" stroke-opacity="0.45" stroke-width="2.6" paint-order="stroke">${soc === null ? "–" : Math.round(pct)}%</text>`;
  }

  function iconInverter() {
    return `
      <rect x="-24" y="-17" width="48" height="34" rx="5" fill="#16263a" stroke="#6b8cae" stroke-width="1.6"/>
      <circle cx="17" cy="-10" r="2.2" fill="#3ddbc0"/>
      <text x="-5" y="6" text-anchor="middle" font-size="10" font-weight="800" fill="#cfe3f7">DC</text>
      <path d="M3,-4 L-1,4 L3,4 L0,11" stroke="${C.solar}" stroke-width="1.6" fill="none" stroke-linejoin="round" stroke-linecap="round" transform="translate(7,-1)"/>
      <text x="15" y="6" text-anchor="middle" font-size="10" font-weight="800" fill="#cfe3f7" opacity="0">AC</text>`;
  }

  function node(key, color, icon, name, value, sub, sub2) {
    const p = P[key];
    const ly = p.y + p.r + 15;
    return `
      <g>
        <circle class="halo" cx="${p.x}" cy="${p.y}" r="${p.r}"/>
        <circle class="ring" cx="${p.x}" cy="${p.y}" r="${p.r}" stroke="${color}"/>
        <g transform="translate(${p.x},${p.y})">${icon}</g>
        <text x="${p.x}" y="${ly}" text-anchor="middle" fill="${color}" class="name">${esc(name)}</text>
        <text x="${p.x}" y="${ly + 20}" text-anchor="middle" fill="${color}" class="value">${value}</text>
        ${sub ? `<text x="${p.x}" y="${ly + 34}" text-anchor="middle" fill="${color}" class="sub">${sub}</text>` : ""}
        ${sub2 ? `<text x="${p.x}" y="${ly + 47}" text-anchor="middle" fill="${color}" class="sub">${sub2}</text>` : ""}
      </g>`;
  }

  // curved connection with dashed track and travelling dots. `d` is the
  // path from a to b; dir "reverse" sends the dots from b to a.
  function connection(d, rev, color, dir, watts, maxW) {
    const live = Boolean(dir);
    let dots = "";
    if (live) {
      const path = dir === "reverse" ? rev : d;
      const dur = 2.8 - Math.min(1, watts / maxW) * 1.5;
      for (let i = 0; i < 3; i++) {
        dots += `<circle r="3.6" fill="${color}" color="${color}" class="dot">
          <animateMotion dur="${dur.toFixed(2)}s" begin="-${((i * dur) / 3).toFixed(2)}s" repeatCount="indefinite" path="${path}"/>
        </circle>`;
      }
    }
    return `<path class="track ${live ? "live" : "idle"}" d="${d}" stroke="${color}"/>${dots}`;
  }

  class MiniEMSFlowCard extends HTMLElement {
    setConfig(config) {
      if (!config || typeof config !== "object") {
        throw new Error("miniems-flow-card: invalid config");
      }
      const missing = REQUIRED_FIELDS.filter((k) => !config[k]);
      if (missing.length) {
        throw new Error(`miniems-flow-card: missing required field(s): ${missing.join(", ")}`);
      }
      this._config = config;
      this._sig = null;
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    connectedCallback() {
      // the sun moves even when no entity changes
      this._timer = setInterval(() => this._render(), 60000);
    }

    disconnectedCallback() {
      clearInterval(this._timer);
    }

    getCardSize() {
      return 7;
    }

    static getStubConfig() {
      return {
        pv_entity: "sensor.pv_power",
        battery_power_entity: "sensor.battery_power",
        battery_soc_entity: "sensor.battery_soc",
        grid_entity: "sensor.grid_power",
        load_entity: "sensor.load_power",
      };
    }

    _num(entityId) {
      if (!entityId) return null;
      const st = this._hass?.states?.[entityId];
      if (!st) return null;
      const v = parseFloat(st.state);
      return Number.isNaN(v) ? null : v;
    }

    // position of the sun on the day arc: fraction 0..1, or null at night
    _sunFraction() {
      const st = this._hass?.states?.[this._config.sun_entity || "sun.sun"];
      if (!st) return { frac: null, rise: null, set: null };
      const a = st.attributes || {};
      const nextRise = a.next_rising ? new Date(a.next_rising) : null;
      const nextSet = a.next_setting ? new Date(a.next_setting) : null;
      const day = 24 * 3600 * 1000;
      if (st.state === "above_horizon" && nextSet) {
        const set = nextSet;
        const rise = nextRise ? new Date(nextRise.getTime() - day) : new Date(set.getTime() - 12 * 3600 * 1000);
        const f = (Date.now() - rise.getTime()) / (set.getTime() - rise.getTime());
        return { frac: Math.min(0.99, Math.max(0.01, f)), rise, set };
      }
      // below horizon: show tomorrow's rise and (approx.) set
      const rise = nextRise;
      const set = nextSet;
      return { frac: null, rise, set };
    }

    _time(d) {
      return d ? d.toLocaleTimeString(this._hass?.language || undefined, { hour: "2-digit", minute: "2-digit" }) : "";
    }

    _render() {
      if (!this._config || !this._hass || !this.shadowRoot) return;
      const c = this._config;
      const lang = (this._hass.language || "en").startsWith("de") ? "de" : "en";
      const t = TXT[lang];

      const pv = Math.max(0, this._num(c.pv_entity) ?? 0);
      const load = Math.max(0, this._num(c.load_entity) ?? 0);
      const grid = this._num(c.grid_entity) ?? 0;
      const batt = this._num(c.battery_power_entity) ?? 0;
      const soc = this._num(c.battery_soc_entity);

      const gridImport = Math.max(0, grid);
      const gridExport = Math.max(0, -grid);
      const battCharge = Math.max(0, -batt);
      const battDischarge = Math.max(0, batt);

      const solarDir = pv > FLOW_MIN_W ? "forward" : null;
      const gridDir = gridImport > FLOW_MIN_W ? "forward" : (gridExport > FLOW_MIN_W ? "reverse" : null);
      const battDir = battCharge > FLOW_MIN_W ? "forward" : (battDischarge > FLOW_MIN_W ? "reverse" : null);
      const homeDir = load > FLOW_MIN_W ? "forward" : null;

      const sun = this._sunFraction();

      const pvToday = this._num(c.pv_today_entity);
      const loadToday = this._num(c.load_today_entity);
      const impToday = this._num(c.grid_import_today_entity);
      const expToday = this._num(c.grid_export_today_entity);
      const chgToday = this._num(c.battery_charge_today_entity);
      const disToday = this._num(c.battery_discharge_today_entity);
      const forecast = this._num(c.pv_forecast_entity);

      // only rebuild the SVG when something visible changed – re-creating it
      // restarts the dot animations
      const sig = JSON.stringify([
        lang, c.title, pv, load, grid, batt, soc, pvToday, loadToday, impToday,
        expToday, chgToday, disToday, forecast, sun.frac === null ? null : Math.round(sun.frac * 200),
        sun.rise?.getTime(), sun.set?.getTime(),
      ]);
      if (sig === this._sig) return;
      this._sig = sig;

      const maxW = c.max_power_w || 5000;
      const gridColor = gridDir === "reverse" ? C.gridOut : C.gridIn;
      const battColor = battDir === "forward" ? C.charge : C.discharge;

      // ── paths (a → b) and their reverse ───────────────────────────────
      const L = {
        solar: [`M${P.solar.x},${P.solar.y} Q${P.solar.x + 70},${P.solar.y + 4} ${P.inv.x},${P.inv.y}`,
                `M${P.inv.x},${P.inv.y} Q${P.solar.x + 70},${P.solar.y + 4} ${P.solar.x},${P.solar.y}`],
        home:  [`M${P.inv.x},${P.inv.y} Q${P.home.x - 40},${P.inv.y + 5} ${P.home.x},${P.home.y}`,
                `M${P.home.x},${P.home.y} Q${P.home.x - 40},${P.inv.y + 5} ${P.inv.x},${P.inv.y}`],
        grid:  [`M${P.grid.x},${P.grid.y} Q${P.grid.x - 60},${P.grid.y - 10} ${P.home.x},${P.home.y}`,
                `M${P.home.x},${P.home.y} Q${P.grid.x - 60},${P.grid.y - 10} ${P.grid.x},${P.grid.y}`],
        batt:  [`M${P.inv.x},${P.inv.y} L${P.battery.x},${P.battery.y}`,
                `M${P.battery.x},${P.battery.y} L${P.inv.x},${P.inv.y}`],
      };

      // ── sun on its arc ────────────────────────────────────────────────
      const arcD = `M${ARC.p0} Q${ARC.p1} ${ARC.p2}`;
      let sunSvg = "";
      if (sun.frac !== null) {
        const [sx, sy] = bez(sun.frac);
        const strength = Math.min(1, pv / 4000);
        sunSvg = `
          <g transform="translate(${sx.toFixed(1)},${sy.toFixed(1)})">
            <circle r="${(20 + strength * 8).toFixed(1)}" fill="url(#sunglow)"/>
            <g class="rays">
              ${Array.from({ length: 8 }, (_, i) => `<line x1="0" y1="-14" x2="0" y2="-19" stroke="${C.sun}" stroke-width="2" stroke-linecap="round" transform="rotate(${i * 45})"/>`).join("")}
            </g>
            <circle r="9" fill="${C.sun}"/>
            <text y="-27" text-anchor="middle" class="small" fill="${C.sun}" style="fill:${C.sun}">${fmtPower(pv)}</text>
          </g>`;
      } else {
        const [mx, my] = bez(0.5);
        sunSvg = `<g transform="translate(${mx},${my + 6})" opacity="0.55">
          <path d="M-6,-8 A9,9 0 1 0 8,5 A7,7 0 1 1 -6,-8 Z" fill="#9fb4d8"/></g>`;
      }
      const forecastTxt = forecast !== null ? `${fmtKwh(forecast)} kWh ${t.forecast}` : "";

      const subSolar = pvToday !== null ? `${t.today} ${fmtKwh(pvToday)} kWh` : "";
      const subHome = loadToday !== null ? `${t.today} ${fmtKwh(loadToday)} kWh` : "";
      const gridName = gridDir === "reverse" ? t.export : (gridDir === "forward" ? t.import : t.grid);
      const gridArrow = gridDir === "reverse" ? "&#8593;" : (gridDir === "forward" ? "&#8595;" : "");
      const subGrid = impToday !== null || expToday !== null
        ? `&#8595;${fmtKwh(impToday)} / &#8593;${fmtKwh(expToday)} kWh` : "";
      const battState = battDir === "forward" ? t.charging : (battDir === "reverse" ? t.discharging : t.idle);
      const battArrow = battDir === "forward" ? "&#8593;" : (battDir === "reverse" ? "&#8595;" : "");
      const subBatt = chgToday !== null || disToday !== null
        ? `+${fmtKwh(chgToday)} / -${fmtKwh(disToday)} kWh` : "";

      const title = c.title ? `<p class="title">${esc(c.title)}</p>` : "";

      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CARD_CSS}</style>
          ${title}
          <svg viewBox="0 -22 480 462" xmlns="http://www.w3.org/2000/svg" role="img"
               aria-label="${esc(c.title || "Power flow")}">
            <defs>
              <radialGradient id="sunglow"><stop offset="0" stop-color="${C.sun}" stop-opacity="0.55"/><stop offset="1" stop-color="${C.sun}" stop-opacity="0"/></radialGradient>
              <linearGradient id="pvcell" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b82e0"/><stop offset="1" stop-color="#1f5bb5"/></linearGradient>
            </defs>

            <path class="arc" d="${arcD}"/>
            <text x="${ARC.p0[0]}" y="${ARC.p0[1] + 16}" class="small">${esc(this._time(sun.frac !== null ? sun.rise : null))}</text>
            <text x="${ARC.p2[0]}" y="${ARC.p2[1] + 16}" text-anchor="end" class="small">${esc(this._time(sun.frac !== null ? sun.set : null))}</text>
            ${forecastTxt ? `<text x="240" y="64" text-anchor="middle" class="small" opacity="0.8">${esc(forecastTxt)}</text>` : ""}
            ${sunSvg}

            ${connection(L.solar[0], L.solar[1], C.solar, solarDir, pv, maxW)}
            ${connection(L.home[0], L.home[1], C.home, homeDir, load, maxW)}
            ${connection(L.grid[0], L.grid[1], gridColor, gridDir, gridDir === "reverse" ? gridExport : gridImport, maxW)}
            ${connection(L.batt[0], L.batt[1], battColor, battDir, battDir === "forward" ? battCharge : battDischarge, maxW)}

            <g transform="translate(${P.inv.x},${P.inv.y})">${iconInverter()}</g>
            <text x="${P.inv.x}" y="${P.inv.y + 33}" text-anchor="middle" class="small">${esc(c.inverter_label || t.inverter)}</text>

            ${node("solar", C.solar, iconSolar(Boolean(solarDir)), t.solar, fmtPower(pv), subSolar)}
            ${node("home", C.home, iconHome(C.home), t.home, fmtPower(load), subHome)}
            ${node("grid", gridColor, iconGrid(gridColor), gridName, `${gridArrow} ${fmtPower(gridDir === "reverse" ? gridExport : gridImport)}`.trim(), subGrid)}
            ${node("battery", battColor, iconBattery(soc, battColor), t.battery, `${battArrow} ${fmtPower(battDir === "forward" ? battCharge : battDischarge)}`.trim(), battState, subBatt)}
          </svg>
        </ha-card>
      `;
    }
  }

  if (!customElements.get("miniems-flow-card")) {
    customElements.define("miniems-flow-card", MiniEMSFlowCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "miniems-flow-card",
      name: "miniEMS Power Flow",
      description: "Illustrated PV/Battery/Grid/Home power flow with a sun that follows the time of day",
      preview: false,
    });
  }
})();
