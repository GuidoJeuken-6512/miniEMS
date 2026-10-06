/* miniEMS Power Flow card.
 *
 * Same visual idea as the SEM Community integration's animated flow card
 * (Solar/Grid/Battery around a Home hub) and as miniEMS's own former ingress
 * dashboard flow diagram (dashboard.html, removed there because an add-on's
 * ingress page cannot register a reusable Lovelace resource – only an
 * integration can). Reimplemented here as a plain custom element (no Lit,
 * no build step) so it can be dropped into any dashboard.
 *
 * miniEMS deliberately does not publish its own pv_power_w/battery_power_w/
 * grid_power_w/battery_soc_pct sensors (see integration/sensor.py – that
 * would duplicate the user's own Deye/meter entities). This card therefore
 * takes those as explicit entity references in its config, exactly like
 * SEM's own sem-flow-card.js "entities:" mode.
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

  const FLOW_COLORS = {
    solar: "#f0883e", gridIn: "#58a6ff", gridOut: "#bc8cff",
    battery: "#56d4dd", home: "var(--primary-text-color, #e6edf3)",
  };
  const FLOW_MIN_W = 10; // below this, treat as "no flow" (avoids sensor-noise flicker)

  // node centres in the 380×340 viewBox – solar/grid/battery around a home hub
  const FLOW_POS = {
    solar:   { x: 190, y: 45,  r: 26 },
    grid:    { x: 55,  y: 150, r: 26 },
    battery: { x: 325, y: 150, r: 26 },
    home:    { x: 190, y: 240, r: 38 },
  };

  const CARD_CSS = `
    ha-card { padding: 0.5rem 1rem 0.75rem; }
    .section-title { font-size: 0.9rem; color: var(--secondary-text-color); margin: 0 0 0.75rem; }
    svg { width: 100%; height: auto; display: block; }
    .flow-track { fill: none; stroke-width: 2.5; opacity: 0.2; }
    .flow-dot   { filter: drop-shadow(0 0 3px currentColor); }
    .flow-node-label {
      font-family: var(--paper-font-common-base_-_font-family, system-ui, sans-serif);
      font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
    }
    .flow-node-value {
      font-family: var(--paper-font-common-base_-_font-family, system-ui, sans-serif);
      font-size: 16px; font-weight: 700;
    }
    .flow-node-sub {
      font-family: var(--paper-font-common-base_-_font-family, system-ui, sans-serif);
      font-size: 11px; font-weight: 600; opacity: 0.75;
    }
  `;

  function fmt(v, dec = 0) {
    return v !== null && v !== undefined && !Number.isNaN(v) ? Number(v).toFixed(dec) : "–";
  }

  function flowNode(key, color, label, value, sub) {
    const p = FLOW_POS[key];
    const labelY = p.y + p.r + 16;
    return `
      <circle class="flow-node-circle" cx="${p.x}" cy="${p.y}" r="${p.r}"
              fill="${color}22" stroke="${color}" stroke-width="2"/>
      <text x="${p.x}" y="${labelY}" text-anchor="middle" fill="${color}" class="flow-node-label">${label}</text>
      <text x="${p.x}" y="${labelY + 18}" text-anchor="middle" fill="${color}" class="flow-node-value">${value}</text>
      ${sub ? `<text x="${p.x}" y="${labelY + 33}" text-anchor="middle" fill="${color}" class="flow-node-sub">${sub}</text>` : ""}
    `;
  }

  // path + up to 3 animated travelling dots; `active` also picks the
  // direction (a→b vs b→a) along the very same visual line
  function flowConnection(a, b, color, active) {
    const pa = FLOW_POS[a], pb = FLOW_POS[b];
    const track = `M${pa.x},${pa.y} L${pb.x},${pb.y}`;
    let dots = "";
    if (active) {
      const path = active === "reverse" ? `M${pb.x},${pb.y} L${pa.x},${pa.y}` : track;
      for (let i = 0; i < 3; i++) {
        dots += `<circle r="4" fill="${color}" class="flow-dot">
          <animateMotion dur="1.6s" begin="${(i * 0.53).toFixed(2)}s" repeatCount="indefinite" path="${path}"/>
        </circle>`;
      }
    }
    return `<path class="flow-track" d="${track}" stroke="${color}"/>${dots}`;
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
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    getCardSize() {
      return 5;
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
      const st = this._hass?.states?.[entityId];
      if (!st) return null;
      const v = parseFloat(st.state);
      return Number.isNaN(v) ? null : v;
    }

    _render() {
      if (!this._config || !this._hass || !this.shadowRoot) return;

      const pv = this._num(this._config.pv_entity) ?? 0;
      const load = this._num(this._config.load_entity) ?? 0;
      const grid = this._num(this._config.grid_entity) ?? 0;
      const batt = this._num(this._config.battery_power_entity) ?? 0;
      const soc = this._num(this._config.battery_soc_entity);

      const gridImport = Math.max(0, grid);
      const gridExport = Math.max(0, -grid);
      const battCharge = Math.max(0, -batt);
      const battDischarge = Math.max(0, batt);

      const solarActive = pv > FLOW_MIN_W ? "forward" : null;
      const gridActive = gridImport > FLOW_MIN_W ? "forward" : (gridExport > FLOW_MIN_W ? "reverse" : null);
      const battActive = battDischarge > FLOW_MIN_W ? "forward" : (battCharge > FLOW_MIN_W ? "reverse" : null);
      const gridColor = gridActive === "reverse" ? FLOW_COLORS.gridOut : FLOW_COLORS.gridIn;

      const gridLabel = gridActive === "reverse"
        ? `&#8593; ${fmt(gridExport, 0)} W` : `&#8595; ${fmt(gridImport, 0)} W`;
      const battLabel = battActive === "reverse"
        ? `&#9650; ${fmt(battCharge, 0)} W` : `&#9660; ${fmt(battDischarge, 0)} W`;

      const title = this._config.title || "Power Flow";

      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CARD_CSS}</style>
          <p class="section-title">${title}</p>
          <svg viewBox="0 0 380 340" xmlns="http://www.w3.org/2000/svg">
            ${flowConnection("solar", "home", FLOW_COLORS.solar, solarActive)}
            ${flowConnection("grid", "home", gridColor, gridActive)}
            ${flowConnection("battery", "home", FLOW_COLORS.battery, battActive)}
            ${flowNode("solar", FLOW_COLORS.solar, "Solar", `${fmt(pv, 0)} W`)}
            ${flowNode("grid", gridColor, "Grid", gridLabel)}
            ${flowNode("battery", FLOW_COLORS.battery, "Battery", `${fmt(soc, 0)}%`, battLabel)}
            ${flowNode("home", FLOW_COLORS.home, "Home", `${fmt(load, 0)} W`)}
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
      description: "Animated PV/Battery/Grid/Home power flow diagram",
      preview: false,
    });
  }
})();
