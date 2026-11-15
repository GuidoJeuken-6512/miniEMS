/* miniEMS – shared helpers for the Phase C pages (plan, consumers, costs,
 * system) and the explanation banner on the dashboard. Plain JS, no build. */
(function () {
  const LANG = (document.documentElement.lang || 'en').startsWith('de') ? 'de' : 'en';
  const de = LANG === 'de';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '–'
    : Number(v).toLocaleString(de ? 'de-DE' : 'en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
  const time = (iso) => { if (!iso) return '–'; const t = new Date(iso);
    return Number.isNaN(t.getTime()) ? '–' : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
  const eur = (v, d = 2) => (v === null || v === undefined) ? '–' : `${num(v, d)} €`;

  async function status() {
    const r = await fetch('api/status');
    return r.json();
  }

  async function post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  /* C1 + C3: one sentence for the mode, one for the next action. */
  function banner(d) {
    const ex = d.explain && d.explain.mode ? d.explain.mode[LANG] : null;
    const na = d.next_action && d.next_action.text ? d.next_action.text[LANG] : null;
    if (!ex && !na) return '';
    return `<div class="explain-banner">${esc(ex || '')}
      ${na ? `<div class="next">${de ? 'Als Nächstes' : 'Next'}: ${esc(na)}</div>` : ''}</div>`;
  }

  const ROW_NAMES = {
    battery: de ? 'Akku' : 'Battery', grid_charge: de ? 'Netzladen' : 'Grid charge',
    hot_water: de ? 'Warmwasser' : 'Hot water', ev: de ? 'E-Auto' : 'EV', heating: de ? 'Heizung' : 'Heating',
  };
  const SEG_NAMES = {
    auto: de ? 'Eigenverbr.' : 'self-use', hold: de ? 'halten' : 'hold', charge: de ? 'laden' : 'charge',
    export: de ? 'einspeisen' : 'export', preheat: de ? 'vorheizen' : 'pre-heat', lower: de ? 'absenken' : 'lower',
  };

  /* C3: every plan on one time axis, price and PV in the background. */
  function timeline(tl) {
    if (!tl) return `<p class="muted">${de ? 'Kein Plan.' : 'No plan.'}</p>`;
    const bg = tl.background || [];
    const rows = tl.rows || {};
    const all = [...bg.map((s) => [s.start, s.end]),
      ...Object.values(rows).flat().map((s) => [s.start, s.end])];
    if (!all.length) return `<p class="muted">${de ? 'Kein Plan.' : 'No plan.'}</p>`;
    const lo = Math.min(...all.map((x) => new Date(x[0]).getTime()));
    const hi = Math.max(...all.map((x) => new Date(x[1]).getTime()));
    const span = Math.max(1, hi - lo);
    const pos = (iso) => ((new Date(iso).getTime() - lo) / span) * 100;
    const prices = bg.map((s) => s.price_eur_kwh).filter((v) => v !== null && v !== undefined);
    const pMin = Math.min(...prices), pMax = Math.max(...prices);
    const pvMax = Math.max(0.001, ...bg.map((s) => s.pv_kwh || 0));
    const priceBars = bg.map((s) => {
      if (s.price_eur_kwh === null || s.price_eur_kwh === undefined) return '';
      const f = pMax > pMin ? (s.price_eur_kwh - pMin) / (pMax - pMin) : 0.5;
      const col = f < 0.33 ? '#8dc892' : (f < 0.66 ? '#e0b04a' : '#ef6b63');
      return `<div class="tl-bar" title="${time(s.start)} ${num(s.price_eur_kwh * 100, 1)} ct"
        style="left:${pos(s.start)}%;width:${Math.max(0.5, pos(s.end) - pos(s.start))}%;height:${20 + 80 * f}%;background:${col};opacity:.55"></div>`;
    }).join('');
    const pvBars = bg.map((s) => `<div class="tl-bar" title="${time(s.start)} PV ${num(s.pv_kwh, 2)} kWh"
        style="left:${pos(s.start)}%;width:${Math.max(0.5, pos(s.end) - pos(s.start))}%;height:${100 * (s.pv_kwh || 0) / pvMax}%;background:#f5c542;opacity:.6"></div>`).join('');
    const row = (label, inner) => `<div class="tl-row"><div class="tl-label">${label}</div><div class="tl-track">${inner}</div></div>`;
    let html = row(de ? 'Preis' : 'Price', priceBars) + row('PV', pvBars);
    for (const [key, segs] of Object.entries(rows)) {
      const inner = segs.map((s) => {
        const left = pos(s.start), width = Math.max(0.6, pos(s.end) - pos(s.start));
        const tip = `${time(s.start)}–${time(s.end)} ${SEG_NAMES[s.label] || s.label}${s.energy_kwh ? ` · ${num(s.energy_kwh, 1)} kWh` : ''}`;
        return `<div class="tl-seg seg-${esc(s.label)}" title="${esc(tip)}" style="left:${left}%;width:${width}%">${width > 6 ? esc(SEG_NAMES[s.label] || s.label) : ''}</div>`;
      }).join('');
      html += row(ROW_NAMES[key] || key, inner);
    }
    return `<div class="tl">${html}</div><div class="tl-axis"><span>${time(new Date(lo).toISOString())}</span><span>${time(new Date((lo + hi) / 2).toISOString())}</span><span>${time(new Date(hi).toISOString())}</span></div>`;
  }

  function inputs(pi) {
    if (!pi) return '';
    return `<dl class="kv">
      <dt>${de ? 'Preise bis' : 'Prices until'}</dt><dd>${pi.prices_until ? new Date(pi.prices_until).toLocaleString() : '–'}</dd>
      <dt>PV</dt><dd>${esc(pi.pv)}</dd>
      <dt>${de ? 'Verbrauch' : 'Load'}</dt><dd>${esc(pi.load)}</dd></dl>`;
  }

  const OVR_NAMES = {
    auto: 'Auto', grid_charge: de ? 'Netzladen' : 'Grid charge', hold: de ? 'Halten' : 'Hold',
    no_grid_charge: de ? 'Kein Netzladen' : 'No grid charge', now: de ? 'Jetzt' : 'Now',
    pv: de ? 'Nur PV' : 'PV only', stop: de ? 'Stopp' : 'Stop',
  };

  /* C2: overrides with an expiry, same API the HA entities use. */
  function overrides(ov) {
    if (!ov || !ov.options) return '';
    const dev = (key, label) => {
      const cur = ov[key] || { value: 'auto' };
      const opts = ov.options[key].map((v) => `<option value="${v}"${v === cur.value ? ' selected' : ''}>${OVR_NAMES[v] || v}</option>`).join('');
      return `<div class="ovr-row"><strong style="min-width:6rem">${label}</strong>
        <select onchange="MiniEMSPages.setOverride('${key}', this.value)">${opts}</select>
        ${cur.until ? `<span class="muted">${de ? 'bis' : 'until'} ${time(cur.until)}</span>
        <button class="btn-secondary" onclick="MiniEMSPages.setOverride('${key}','auto')">${de ? 'Beenden' : 'End'}</button>` : ''}</div>`;
    };
    return `${dev('battery', ROW_NAMES.battery)}${dev('ev', ROW_NAMES.ev)}
      <div class="ovr-row"><span>${de ? 'Dauer' : 'Duration'}</span>
        <input type="number" min="${ov.min_minutes}" max="${ov.max_minutes}" step="15" value="${ov.minutes}"
          onchange="MiniEMSPages.setMinutes(this.value)"> min <span id="ovr-msg" class="save-msg"></span></div>`;
  }

  async function setOverride(device, value) {
    const msg = document.getElementById('ovr-msg');
    try { await post('api/override', { device, value, source: 'ingress' }); if (msg) { msg.textContent = '✓'; msg.className = 'save-msg ok'; } }
    catch (e) { if (msg) { msg.textContent = e.message; msg.className = 'save-msg err'; } }
    if (window.MiniEMSRefresh) window.MiniEMSRefresh();
  }
  async function setMinutes(value) {
    const msg = document.getElementById('ovr-msg');
    try { await post('api/override', { minutes: Number(value) }); if (msg) { msg.textContent = '✓'; msg.className = 'save-msg ok'; } }
    catch (e) { if (msg) { msg.textContent = e.message; msg.className = 'save-msg err'; } }
  }

  function loop(render, everyMs = 5000) {
    const run = async () => { try { render(await status()); } catch (e) { console.error(e); } };
    window.MiniEMSRefresh = run;
    run();
    setInterval(run, everyMs);
  }

  window.MiniEMSPages = { LANG, de, esc, num, time, eur, status, post, banner, timeline, inputs,
    overrides, setOverride, setMinutes, loop, ROW_NAMES };
})();
