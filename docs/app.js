/* Market Desk — dashboard front end.
 *
 * Reads only the committed JSON in data/. No API calls at view time, so
 * the page keeps working when a data source breaks and every number on
 * screen traces back to one refresh run.
 */
'use strict';

const LWC = window.LightweightCharts;

const state = {
  index: null,
  meta: null,
  rows: [],
  bySymbol: new Map(),
  current: null,          // loaded symbol payload
  currentSymbol: null,
  cache: new Map(),
  range: 252,
  overlays: new Set(['sma']),
  pane: 'rsi',
  sortKey: 'symbol',
  sortDir: 1,
  screenSort: { key: 'symbol', dir: 1 },
  filter: '',
};

const charts = { main: null, lower: null, series: {} };

/* ---------------- formatting ---------------- */

const fmt = {
  price(v) {
    if (v == null) return '—';
    const digits = Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4;
    return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  },
  pct(v, digits = 2) {
    if (v == null) return '—';
    return (v * 100).toFixed(digits) + '%';
  },
  signedPct(v, digits = 2) {
    if (v == null) return '—';
    return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%';
  },
  num(v, digits = 2) {
    if (v == null) return '—';
    return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  },
  compact(v) {
    if (v == null) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e12) return (v / 1e12).toFixed(2) + 'T';
    if (abs >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (abs >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toFixed(0);
  },
  ratio(v) { return v == null ? '—' : v.toFixed(2) + '×'; },
  pval(p) {
    if (p == null) return '—';
    return p < 0.001 ? p.toExponential(1) : p.toFixed(4);
  },
  cls(v) { return v == null ? '' : v >= 0 ? 'up' : 'down'; },
};

const el = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------------- boot ---------------- */

async function boot() {
  try {
    const [index, meta] = await Promise.all([
      fetch('data/index.json?t=' + Date.now()).then((r) => r.json()),
      fetch('data/meta.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
    ]);
    state.index = index;
    state.meta = meta;
    state.rows = index.rows || [];
    state.rows.forEach((r) => state.bySymbol.set(r.symbol, r));
  } catch (err) {
    el('symbol-list').innerHTML =
      '<p class="empty">Could not load <code>data/index.json</code>.<br>' +
      'Run <code>python scripts/refresh.py</code> to generate it.</p>';
    showBanner('No data yet — the refresh has not run, or its output was not deployed.');
    return;
  }

  stampHeader();
  buildCharts();
  renderSidebar();
  renderScreen();
  wireControls();

  const wanted = new URLSearchParams(location.search).get('symbol');
  const first = (wanted && state.bySymbol.has(wanted.toUpperCase()))
    ? wanted.toUpperCase()
    : (state.rows[0] && state.rows[0].symbol);
  if (first) selectSymbol(first);
}

function stampHeader() {
  const m = state.meta || {};
  const last = state.rows.length ? state.rows[0].last_date : (state.index.fetch_date || '');
  el('stamp-date').textContent = 'data through ' + (last || '—');

  const failed = m.symbols_failed && Object.keys(m.symbols_failed).length;
  if (failed) {
    showBanner(`${failed} symbol${failed > 1 ? 's' : ''} failed to update this run: ` +
      Object.entries(m.symbols_failed).map(([s, why]) => `${s} (${why})`).join(', '));
  }
}

function showBanner(text) {
  const b = el('banner');
  b.textContent = text;
  b.hidden = false;
}

/* ---------------- sidebar ---------------- */

function renderSidebar() {
  const host = el('symbol-list');
  const term = state.filter.trim().toUpperCase();
  const match = (r) => !term ||
    r.symbol.includes(term) || (r.name || '').toUpperCase().includes(term);

  let html = '';
  const sorted = (syms) => {
    const rows = syms.map((s) => state.bySymbol.get(s)).filter(Boolean).filter(match);
    if (state.sortKey === 'symbol') return rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    return rows.sort((a, b) => {
      const av = a[state.sortKey], bv = b[state.sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;          // missing values sink, either direction
      if (bv == null) return -1;
      return bv - av;
    });
  };

  for (const tier of state.index.tiers || []) {
    const rows = sorted(tier.symbols || []);
    if (!rows.length) continue;
    html += `<div class="tier-head">${esc(tier.label)}</div>`;
    for (const r of rows) {
      html += `<div class="sym-row" data-symbol="${esc(r.symbol)}">
        <span class="t">${esc(r.symbol)}</span>
        <span class="p">${fmt.price(r.last)}</span>
        <span class="n">${esc(r.name || '')}</span>
        <span class="c ${fmt.cls(r.change_1d)}">${fmt.signedPct(r.change_1d)}</span>
      </div>`;
    }
  }
  host.innerHTML = html || '<p class="empty">No symbols match that filter.</p>';

  host.querySelectorAll('.sym-row').forEach((node) => {
    node.addEventListener('click', () => selectSymbol(node.dataset.symbol));
    if (node.dataset.symbol === state.currentSymbol) node.classList.add('is-active');
  });
}

/* ---------------- charts ---------------- */

const chartTheme = {
  layout: { background: { color: '#151b23' }, textColor: '#8b98a8', fontSize: 11,
    fontFamily: 'ui-monospace, SF Mono, Menlo, monospace' },
  grid: { vertLines: { color: '#1e242d' }, horzLines: { color: '#1e242d' } },
  rightPriceScale: { borderColor: '#262d38' },
  timeScale: { borderColor: '#262d38', rightOffset: 6 },
  crosshair: {
    mode: LWC.CrosshairMode.Normal,
    vertLine: { color: '#5f6b7a', width: 1, style: 3, labelBackgroundColor: '#262d38' },
    horzLine: { color: '#5f6b7a', width: 1, style: 3, labelBackgroundColor: '#262d38' },
  },
};

function buildCharts() {
  // Sized explicitly rather than with `autoSize: true`. Under autoSize the
  // chart's internal width stays 0 in this build, which pins barSpacing at
  // its 0.5 minimum — fitContent() and setVisibleLogicalRange() both become
  // no-ops and every series renders squeezed against the right edge. Passing
  // real dimensions and driving resize ourselves keeps the time scale honest.
  const mainBox = el('chart-main');
  const lowerBox = el('chart-lower');

  charts.main = LWC.createChart(mainBox, {
    ...chartTheme,
    width: mainBox.clientWidth,
    height: mainBox.clientHeight,
  });
  charts.lower = LWC.createChart(lowerBox, {
    ...chartTheme,
    width: lowerBox.clientWidth,
    height: lowerBox.clientHeight,
    timeScale: { ...chartTheme.timeScale, visible: false },
    // The lower pane is a slave view, never a control surface. Disabling
    // its own scroll/scale is what makes the one-way sync below safe.
    handleScroll: false,
    handleScale: false,
  });

  // Sync is deliberately ONE-WAY: the main chart drives the lower pane and
  // the lower pane never writes back. A two-way link guarded by a boolean
  // does not work here — the range-change event fires asynchronously, so
  // the guard is already cleared when the echo arrives, and the two charts
  // ratchet each other into an ever-tighter window until the chart shows a
  // handful of bars. With no back-channel the loop cannot form.
  charts.main.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range) charts.lower.timeScale().setVisibleLogicalRange(range);
  });

  charts.main.subscribeCrosshairMove(updateLegend);

  // Keep both panes matched to their containers.
  const resize = () => {
    if (!mainBox.clientWidth) return;         // hidden view; nothing to measure
    charts.main.resize(mainBox.clientWidth, mainBox.clientHeight);
    charts.lower.resize(lowerBox.clientWidth, lowerBox.clientHeight);
  };
  if (window.ResizeObserver) new ResizeObserver(resize).observe(mainBox);
  window.addEventListener('resize', resize);
  charts.resize = resize;
}

function clearSeries() {
  for (const key of Object.keys(charts.series)) {
    const entry = charts.series[key];
    const chart = entry.pane === 'lower' ? charts.lower : charts.main;
    try { chart.removeSeries(entry.series); } catch (_) { /* already gone */ }
  }
  charts.series = {};
}

function addSeries(key, pane, series) { charts.series[key] = { pane, series }; }

/** Trim aligned arrays to the selected range. 0 means "everything". */
function windowed(payload) {
  const n = payload.candles.length;
  const take = state.range > 0 ? Math.min(state.range, n) : n;
  const from = n - take;
  const slice = (arr) => (Array.isArray(arr) ? arr.slice(from) : []);
  return {
    from,
    candles: payload.candles.slice(from),
    ind: Object.fromEntries(Object.entries(payload.indicators || {}).map(([k, v]) => [k, slice(v)])),
    vol: Object.fromEntries(Object.entries(payload.volume_analytics || {})
      .filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k, slice(v)])),
  };
}

/** Pair a value series with candle times, dropping nulls (unwarmed windows). */
function lineData(times, values) {
  const out = [];
  for (let i = 0; i < times.length; i++) {
    const v = values[i];
    if (v != null) out.push({ time: times[i], value: v });
  }
  return out;
}

function drawChart() {
  const payload = state.current;
  if (!payload) return;
  clearSeries();

  const w = windowed(payload);
  const times = w.candles.map((c) => c.t);

  // --- price ---
  const candles = charts.main.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350',
    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  });
  candles.setData(w.candles.map((c) => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c })));
  candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.28 } });
  addSeries('candles', 'main', candles);

  // --- volume, as an overlay pinned to the lower quarter ---
  const volume = charts.main.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
    lastValueVisible: false,
    priceLineVisible: false,
  });
  volume.setData(w.candles.map((c) => ({
    time: c.t, value: c.v,
    color: c.c >= c.o ? 'rgba(38,166,154,0.45)' : 'rgba(239,83,80,0.45)',
  })));
  charts.main.priceScale('volume').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
  addSeries('volume', 'main', volume);

  // --- overlays ---
  if (state.overlays.has('sma')) {
    const specs = [
      ['sma20', '#58a6ff', 'SMA 20'],
      ['sma50', '#d29922', 'SMA 50'],
      ['sma200', '#a371f7', 'SMA 200'],
    ];
    for (const [key, color, title] of specs) {
      const data = lineData(times, w.ind[key] || []);
      if (!data.length) continue;
      const s = charts.main.addLineSeries({ color, lineWidth: 1, title, priceLineVisible: false, lastValueVisible: false });
      s.setData(data);
      addSeries(key, 'main', s);
    }
  }

  if (state.overlays.has('bb')) {
    for (const [key, title] of [['bb_upper', 'BB upper'], ['bb_lower', 'BB lower']]) {
      const data = lineData(times, w.ind[key] || []);
      if (!data.length) continue;
      const s = charts.main.addLineSeries({
        color: 'rgba(139,152,168,0.65)', lineWidth: 1, lineStyle: 2,
        title, priceLineVisible: false, lastValueVisible: false,
      });
      s.setData(data);
      addSeries(key, 'main', s);
    }
  }

  if (state.overlays.has('vwap')) {
    const data = lineData(times, w.vol.vwap20 || []);
    if (data.length) {
      const s = charts.main.addLineSeries({
        color: '#e3b341', lineWidth: 1, title: 'VWAP 20',
        priceLineVisible: false, lastValueVisible: false,
      });
      s.setData(data);
      addSeries('vwap', 'main', s);
    }
  }

  if (state.overlays.has('forecast')) drawForecast(payload, w);

  // --- lower pane ---
  drawLowerPane(times, w);

  // Only the main chart fits; the subscription above pushes the resulting
  // range to the lower pane.
  charts.main.timeScale().fitContent();
  updateLegend(null);
}

/** Project the damped-trend point and its 90% band past the last bar. */
function drawForecast(payload, w) {
  const f = payload.forecast;
  if (!f || !f.horizons || !f.horizons.length) return;
  const lastCandle = w.candles[w.candles.length - 1];
  if (!lastCandle) return;

  // Each line is anchored at the last real close so the projection reads
  // as a continuation rather than a detached floating segment.
  const anchor = { time: lastCandle.t, value: lastCandle.c };
  const mk = (field, color, style, title) => {
    const pts = [anchor].concat(
      f.horizons
        .filter((h) => h.target_date > lastCandle.t)
        .map((h) => ({ time: h.target_date, value: h[field] })),
    );
    if (pts.length < 2) return;
    const s = charts.main.addLineSeries({
      color, lineWidth: field === 'value' ? 2 : 1, lineStyle: style,
      title, priceLineVisible: false, lastValueVisible: false,
    });
    s.setData(pts);
    addSeries('fc_' + field, 'main', s);
  };
  mk('value', '#58a6ff', 2, 'forecast');
  mk('hi90', 'rgba(88,166,255,0.5)', 3, 'hi 90%');
  mk('lo90', 'rgba(88,166,255,0.5)', 3, 'lo 90%');
}

function drawLowerPane(times, w) {
  const pane = state.pane;

  if (pane === 'rsi') {
    const s = charts.lower.addLineSeries({ color: '#58a6ff', lineWidth: 1, title: 'RSI 14' });
    s.setData(lineData(times, w.ind.rsi14 || []));
    // 70/30 are the conventional bands; drawn as price lines so they
    // scale with the pane instead of needing their own series.
    for (const [v, color] of [[70, 'rgba(239,83,80,0.55)'], [30, 'rgba(38,166,154,0.55)']]) {
      s.createPriceLine({ price: v, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
    }
    charts.lower.priceScale('right').applyOptions({ autoScale: false });
    s.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    addSeries('rsi', 'lower', s);

  } else if (pane === 'macd') {
    const hist = charts.lower.addHistogramSeries({ title: 'MACD hist' });
    hist.setData(lineData(times, w.ind.macd_hist || []).map((p) => ({
      ...p, color: p.value >= 0 ? 'rgba(38,166,154,0.6)' : 'rgba(239,83,80,0.6)',
    })));
    addSeries('macd_hist', 'lower', hist);
    const line = charts.lower.addLineSeries({ color: '#58a6ff', lineWidth: 1, title: 'MACD' });
    line.setData(lineData(times, w.ind.macd || []));
    addSeries('macd', 'lower', line);
    const sig = charts.lower.addLineSeries({ color: '#d29922', lineWidth: 1, title: 'signal' });
    sig.setData(lineData(times, w.ind.macd_signal || []));
    addSeries('macd_signal', 'lower', sig);

  } else if (pane === 'rvol') {
    const s = charts.lower.addHistogramSeries({ title: 'Relative volume' });
    s.setData(lineData(times, w.vol.rvol || []).map((p) => ({
      ...p,
      // 2× its own 20-day baseline is the conventional "unusual volume"
      // threshold; colored so a spike is findable without reading the axis.
      color: p.value >= 2 ? 'rgba(239,83,80,0.75)'
        : p.value >= 1.5 ? 'rgba(210,153,34,0.7)'
        : 'rgba(88,166,255,0.45)',
    })));
    s.createPriceLine({ price: 1, color: 'rgba(139,152,168,0.6)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
    addSeries('rvol', 'lower', s);

  } else if (pane === 'obv') {
    const s = charts.lower.addLineSeries({ color: '#a371f7', lineWidth: 1, title: 'OBV' });
    s.setData(lineData(times, w.vol.obv || []));
    addSeries('obv', 'lower', s);
  }
}

function updateLegend(param) {
  const payload = state.current;
  if (!payload) return;
  const w = windowed(payload);
  let idx = w.candles.length - 1;
  if (param && param.time) {
    const found = w.candles.findIndex((c) => c.t === param.time);
    if (found >= 0) idx = found;
  }
  const c = w.candles[idx];
  if (!c) return;

  const parts = [
    `<span>${c.t}</span>`,
    `<span>O ${fmt.price(c.o)}  H ${fmt.price(c.h)}  L ${fmt.price(c.l)}  <strong>C ${fmt.price(c.c)}</strong></span>`,
    `<span>Vol ${fmt.compact(c.v)}</span>`,
  ];
  const rvol = (w.vol.rvol || [])[idx];
  if (rvol != null) parts.push(`<span>RVOL ${rvol.toFixed(2)}×</span>`);
  if (state.overlays.has('sma')) {
    for (const [key, color, label] of [['sma20', '#58a6ff', 'MA20'], ['sma50', '#d29922', 'MA50'], ['sma200', '#a371f7', 'MA200']]) {
      const v = (w.ind[key] || [])[idx];
      if (v != null) parts.push(`<span><i class="swatch" style="background:${color}"></i>${label} ${fmt.price(v)}</span>`);
    }
  }
  el('legend').innerHTML = parts.join('');
}

/* ---------------- symbol selection ---------------- */

async function selectSymbol(symbol) {
  state.currentSymbol = symbol;
  document.querySelectorAll('.sym-row').forEach((n) =>
    n.classList.toggle('is-active', n.dataset.symbol === symbol));

  let payload = state.cache.get(symbol);
  if (!payload) {
    try {
      payload = await fetch(`data/symbols/${encodeURIComponent(symbol)}.json?t=${Date.now()}`).then((r) => {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      });
      state.cache.set(symbol, payload);
    } catch (err) {
      el('legend').innerHTML = `<span class="down">Could not load ${esc(symbol)}.</span>`;
      return;
    }
  }

  state.current = payload;
  renderSymbolHead();
  drawChart();
  renderCards();

  const url = new URL(location);
  url.searchParams.set('symbol', symbol);
  history.replaceState(null, '', url);
}

function renderSymbolHead() {
  const p = state.current;
  const row = state.bySymbol.get(p.symbol) || {};
  el('sym-ticker').textContent = p.symbol;
  el('sym-name').textContent = p.name || '';
  const tier = (state.index.tiers || []).find((t) => t.key === p.tier);
  el('sym-tier').textContent = tier ? tier.label : (p.tier || '');
  el('sym-price').textContent = fmt.price(row.last);
  const change = el('sym-change');
  change.textContent = `${fmt.signedPct(row.change_1d)} today`;
  change.className = 'change ' + fmt.cls(row.change_1d);
}

/* ---------------- cards ---------------- */

function percentileBar(rank) {
  if (!rank) return '';
  const pct = Math.round(rank.percentile * 100);
  return `<div class="pbar">
    <div class="pbar-track">
      <div class="pbar-fill" style="opacity:.4"></div>
      <div class="pbar-mark" style="left:calc(${pct}% - 1px)"></div>
    </div>
    <div class="pbar-labels"><span>cheapest</span><span>${pct}th pctile of ${esc(rank.peer_group)} (n=${rank.peer_count})</span><span>priciest</span></div>
  </div>`;
}

function renderCards() {
  const p = state.current;
  const row = state.bySymbol.get(p.symbol) || {};

  // --- valuation ---
  const v = p.valuation;
  let html = '';
  if (v) {
    html += `<dl class="kv">
      <dt>Trailing P/E</dt><dd>${fmt.num(v.trailing_pe)}</dd>
      <dt>Forward P/E</dt><dd>${fmt.num(v.forward_pe)}</dd>
      <dt>Earnings yield</dt><dd>${fmt.pct(v.earnings_yield)}</dd>
      <dt>Price / book</dt><dd>${fmt.num(row.price_to_book)}</dd>
      <dt>EPS (trailing)</dt><dd>${fmt.num(row.trailing_eps)}</dd>
      <dt>Dividend yield</dt><dd>${fmt.pct(row.dividend_yield)}</dd>
      <dt>Market cap</dt><dd>${fmt.compact(row.market_cap)}</dd>
      <dt>Beta</dt><dd>${fmt.num(row.beta)}</dd>
    </dl>`;
    html += percentileBar(v.ranks && v.ranks.trailing_pe);
    html += `<p class="note">${esc(v.summary)}</p>`;
    for (const note of v.notes || []) html += `<p class="note faint">${esc(note)}</p>`;
  } else {
    html = '<p class="note">No valuation data for this symbol.</p>';
  }
  el('card-valuation').querySelector('.card-body').innerHTML = html;

  // --- volume ---
  const va = p.volume_analytics || {};
  const d = va.divergence || {};
  html = `<dl class="kv">
    <dt>Last session</dt><dd>${fmt.compact(row.volume)}</dd>
    <dt>20-day average</dt><dd>${fmt.compact(row.avg_volume_20d)}</dd>
    <dt>Relative volume</dt><dd>${fmt.ratio(row.rvol)}</dd>
    <dt>Volume trend (20/60)</dt><dd>${fmt.ratio(va.volume_trend)}</dd>
    <dt>Up/down volume</dt><dd>${fmt.ratio(va.up_down_ratio)}</dd>
    <dt>Dollar volume</dt><dd>$${fmt.compact(row.dollar_volume)}</dd>
  </dl>
  <p class="note"><span class="verdict verdict-${esc(d.verdict || 'quiet')}">${esc(d.verdict || '—')}</span></p>
  <p class="note">${esc(d.detail || '')} Over the last 20 sessions price moved ${fmt.signedPct(d.price_change)} while volume ran ${fmt.ratio(d.volume_ratio)} its 60-day baseline.</p>
  <p class="note faint">A descriptive label, not a backtested signal.</p>`;
  el('card-volume').querySelector('.card-body').innerHTML = html;

  // --- forecast ---
  const f = p.forecast;
  if (f && f.horizons && f.horizons.length) {
    html = '<dl class="kv">';
    for (const h of f.horizons) {
      const move = row.last ? (h.value / row.last - 1) : null;
      html += `<dt>${h.months}-month point</dt><dd>${fmt.price(h.value)} <span class="${fmt.cls(move)}">${fmt.signedPct(move)}</span></dd>`;
      html += `<dt class="faint">&nbsp;&nbsp;90% band</dt><dd class="faint">${fmt.price(h.lo90)} – ${fmt.price(h.hi90)}</dd>`;
    }
    html += '</dl>';
    html += `<p class="note">Damped-drift projection from <code>census_forecaster.markets.trend</code>${
      f.calibrated
        ? `, with a walk-forward-calibrated band multiplier of ${fmt.num(f.band_multiplier)}`
        : ', with the fallback normal multiplier — this series was too short to calibrate'
    }. Monthly volatility ${fmt.pct(f.monthly_vol)}, fitted on ${f.months_used} months.</p>`;
    html += `<p class="note faint">Equity prices are close to a random walk. The point is a damped trend, not a prediction of skill; the band is the honest content, and it is wide on purpose.</p>`;
  } else {
    html = `<p class="note">No forecast${f && f.error ? ` — ${esc(f.error)}` : ''}.</p>`;
  }
  el('card-forecast').querySelector('.card-body').innerHTML = html;

  // --- Hawaii macro ---
  const sigs = p.macro_signals || [];
  if (sigs.length) {
    html = '';
    for (const s of sigs) {
      html += `<div class="sig">
        <div class="sig-target">leads ${esc(s.target_label)}
          <span class="flag ${s.robust_to_2020 ? 'flag-robust' : 'flag-fragile'}">${s.robust_to_2020 ? 'robust' : 'COVID-dependent'}</span>
        </div>
        <div class="sig-meta">lead ${s.lead_months ?? '—'} mo · Granger p ${fmt.pval(s.granger_p)} · ${esc(s.transform)}</div>
      </div>`;
    }
    html += `<p class="note faint">From Census-Forecaster's pre-registered lead-lag screen (BH-FDR controlled). Granger causality is predictive precedence, not causation — confounders survive this test. Signals flagged COVID-dependent did not survive re-running the screen with 2020 excluded. The arrow runs prices → Hawaii economy, never the reverse.</p>`;
  } else {
    html = `<p class="note">This symbol is not in Census-Forecaster's pre-registered Hawaii screen, so there is no lead signal to report.</p>`;
  }
  el('card-macro').querySelector('.card-body').innerHTML = html;
}

/* ---------------- screen table ---------------- */

const SCREEN_COLS = [
  ['symbol', (r) => `<td class="sym sticky-col">${esc(r.symbol)}</td>`],
  ['last', (r) => `<td class="num">${fmt.price(r.last)}</td>`],
  ['change_1d', (r) => `<td class="num ${fmt.cls(r.change_1d)}">${fmt.signedPct(r.change_1d)}</td>`],
  ['change_1m', (r) => `<td class="num ${fmt.cls(r.change_1m)}">${fmt.signedPct(r.change_1m)}</td>`],
  ['change_1y', (r) => `<td class="num ${fmt.cls(r.change_1y)}">${fmt.signedPct(r.change_1y)}</td>`],
  ['trailing_pe', (r) => `<td class="num">${fmt.num(r.trailing_pe, 1)}</td>`],
  ['forward_pe', (r) => `<td class="num">${fmt.num(r.forward_pe, 1)}</td>`],
  ['pe_percentile', (r) => `<td class="num">${r.pe_percentile == null ? '—' : Math.round(r.pe_percentile * 100)}</td>`],
  ['earnings_yield', (r) => `<td class="num">${fmt.pct(r.earnings_yield, 1)}</td>`],
  ['volume', (r) => `<td class="num">${fmt.compact(r.volume)}</td>`],
  ['rvol', (r) => `<td class="num">${fmt.ratio(r.rvol)}</td>`],
  ['volume_trend', (r) => `<td class="num">${fmt.ratio(r.volume_trend)}</td>`],
  ['dollar_volume', (r) => `<td class="num">$${fmt.compact(r.dollar_volume)}</td>`],
  ['divergence', (r) => `<td><span class="verdict verdict-${esc(r.divergence || 'quiet')}">${esc(r.divergence || '—')}</span></td>`],
  ['rsi14', (r) => `<td class="num">${fmt.num(r.rsi14, 0)}</td>`],
  ['annualized_vol', (r) => `<td class="num">${fmt.pct(r.annualized_vol, 0)}</td>`],
  ['market_cap', (r) => `<td class="num">${fmt.compact(r.market_cap)}</td>`],
];

function renderScreen() {
  const { key, dir } = state.screenSort;
  const rows = state.rows.slice().sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;            // blanks always sink
    if (bv == null) return -1;
    if (typeof av === 'string') return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });

  el('screen-table').querySelector('tbody').innerHTML = rows.map((r) =>
    `<tr data-symbol="${esc(r.symbol)}">${SCREEN_COLS.map(([, render]) => render(r)).join('')}</tr>`
  ).join('');

  document.querySelectorAll('#screen-table th').forEach((th) => {
    th.classList.toggle('sorted', th.dataset.key === key);
    th.classList.toggle('asc', th.dataset.key === key && dir === 1);
  });

  el('screen-table').querySelectorAll('tbody tr').forEach((tr) => {
    tr.addEventListener('click', () => {
      selectSymbol(tr.dataset.symbol);
      switchView('chart');
    });
  });
}

/* ---------------- controls ---------------- */

function switchView(view) {
  document.querySelectorAll('.view-tab').forEach((b) => b.classList.toggle('is-active', b.dataset.view === view));
  el('view-chart').classList.toggle('is-active', view === 'chart');
  el('view-screen').classList.toggle('is-active', view === 'screen');
  if (view === 'chart' && charts.main) {
    // The panes were display:none while hidden, so they measured zero.
    charts.resize();
    charts.main.timeScale().fitContent();
  }
}

function wireControls() {
  document.querySelectorAll('.view-tab').forEach((b) =>
    b.addEventListener('click', () => switchView(b.dataset.view)));

  el('filter').addEventListener('input', (e) => { state.filter = e.target.value; renderSidebar(); });
  el('sort').addEventListener('change', (e) => { state.sortKey = e.target.value; renderSidebar(); });

  el('range-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.range = Number(b.dataset.range);
    el('range-buttons').querySelectorAll('button').forEach((x) => x.classList.toggle('is-active', x === b));
    drawChart();
  });

  el('overlay-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    const key = b.dataset.overlay;
    if (state.overlays.has(key)) state.overlays.delete(key); else state.overlays.add(key);
    b.classList.toggle('is-active', state.overlays.has(key));
    drawChart();
  });

  el('pane-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.pane = b.dataset.pane;
    el('pane-buttons').querySelectorAll('button').forEach((x) => x.classList.toggle('is-active', x === b));
    drawChart();
  });

  el('screen-table').querySelectorAll('th').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.key;
      // Text sorts A→Z first; numbers sort high→low first, which is what
      // you want from "show me the biggest movers".
      if (state.screenSort.key === key) state.screenSort.dir *= -1;
      else state.screenSort = { key, dir: key === 'symbol' || key === 'divergence' ? 1 : -1 };
      renderScreen();
    });
  });

  el('about-btn').addEventListener('click', () => {
    el('about-body').innerHTML = aboutHtml();
    el('about').showModal();
  });
}

function aboutHtml() {
  const m = state.meta || {};
  const mo = m.macro_overlay || {};
  const src = m.sources || {};
  return `
    <p>Generated <code>${esc(m.generated || '—')}</code> · ${m.symbols_ok ?? '—'} symbols ·
       ${m.forecasts_ok ?? '—'} forecasts.</p>
    <h3>Where the numbers come from</h3>
    <p><strong>Prices &amp; fundamentals</strong> — ${esc(src.prices || 'Yahoo Finance')}. Yahoo's endpoints are
       unofficial and undocumented: fields disappear, ETFs report aggregate multiples that are not
       comparable to a company's, and figures can be stale or simply wrong.</p>
    <p><strong>Forecasts</strong> — ${esc(src.forecasts || '')} Pinned at Census-Forecaster
       <code>${esc((m.forecaster_pin || '—').slice(0, 12))}</code>.</p>
    <p><strong>Hawaii lead signals</strong> — ${esc(src.macro_signals || '')}
       Screen generated ${esc(mo.generated || '—')}, FDR q=${esc(mo.q_fdr ?? '—')},
       ${esc(mo.candidates_tested ?? '—')} candidates tested.</p>
    <h3>How to read a P/E here</h3>
    <p>A multiple only means something against a comparison set, so every P/E is reported as a
       percentile inside a peer group and the group is always named. When a sector has too few
       members in this watchlist, the peer group falls back to the whole tracked universe — which
       is a much weaker comparison than the full market, and the label says so.</p>
    <h3>What this is not</h3>
    <p>${esc(m.disclaimer || 'Tracker context, not trading advice.')}</p>`;
}

boot();
