/* Market Desk — dashboard front end.
 *
 * Chart surface: KLineChart v9 (Apache-2.0) — the open-source library whose
 * interface tracks TradingView's: one container holding the candle pane plus
 * indicator sub-panes, interactive drawing overlays (trend lines, channels,
 * fibs) with magnet snapping, and per-pane tooltips.
 *
 * Reads only the committed JSON in data/. No API calls at view time, so the
 * page keeps working when a data source breaks and every number on screen
 * traces back to one refresh run.
 */
'use strict';

const KLC = window.klinecharts;

const state = {
  index: null,
  meta: null,
  rows: [],
  bySymbol: new Map(),
  current: null,           // loaded symbol payload
  currentSymbol: null,
  cache: new Map(),
  timeframe: 'D',          // D | W | M
  range: 252,              // daily sessions; 0 = max
  mainInds: new Set(['MA']),
  subInds: new Set(['VOL', 'RSI']),
  forecastOn: false,
  magnet: false,
  activeDrawTool: '',
  sortKey: 'symbol',
  screenSort: { key: 'symbol', dir: 1 },
  factorSort: { key: 'mom_rank', dir: -1 },
  filter: '',
  notes: [],
  activeNote: null,
  portfolio: null,
  history: {},
  calendar: null,
  benchmark: null,
  historyProvenance: {},
  askReady: false,
  askBusy: false,
};

const chartState = {
  chart: null,
  subPanes: new Map(),     // indicator name -> paneId
  fanId: null,             // forecast overlay id
  drawnIds: [],            // user drawings, for the eraser
};

// Sessions per bar for each timeframe — converts the daily range buttons.
const TF_SESSIONS = { D: 1, W: 5, M: 21 };
// Future bars per forecast-horizon month.
const TF_BARS_PER_MONTH = { D: 21, W: 4.33, M: 1 };

/* ---------------- formatting ---------------- */

const fmt = {
  price(v) {
    if (v == null) return '—';
    const digits = Math.abs(v) >= 1 ? 2 : 4;
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
  score(v) { return v == null ? '—' : v.toFixed(2); },
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
    const [index, meta, notes, portfolio, history, calendar, benchmark] = await Promise.all([
      fetch('data/index.json?t=' + Date.now()).then((r) => r.json()),
      fetch('data/meta.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
      fetch('data/notes.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
      fetch('data/portfolio.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
      fetch('data/history.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
      fetch('data/calendar.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
      fetch('data/benchmark.json?t=' + Date.now()).then((r) => r.json()).catch(() => null),
    ]);
    state.portfolio = (portfolio && portfolio.positions) ? portfolio : null;
    state.history = (history && history.series) || {};
    state.historyProvenance = (history && history.provenance) || {};
    state.calendar = calendar || null;
    state.benchmark = (benchmark && benchmark.available) ? benchmark : null;
    state.index = index;
    state.meta = meta;
    state.notes = (notes && notes.notes) || [];
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
  registerChartExtensions();
  buildChart();
  renderSidebar();
  renderScreen();
  renderFactors();
  renderNotes();
  renderPortfolio();
  renderTiming();
  wireControls();
  probeAsk();

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

/* ---------------- chart: custom indicators + forecast overlay ---------------- */

function registerChartExtensions() {
  // Relative volume, mirroring the pipeline's definition: today's volume
  // against the average of the PRIOR 20 bars — the current bar never
  // inflates its own baseline.
  KLC.registerIndicator({
    name: 'MDRVOL',
    shortName: 'RVOL(20)',
    figures: [{
      key: 'rvol', title: 'RVOL: ', type: 'bar', baseValue: 0,
      styles: (data) => {
        const v = data.current && data.current.rvol;
        if (v == null) return { color: 'rgba(88,166,255,0.45)' };
        return {
          color: v >= 2 ? 'rgba(239,83,80,0.8)'
            : v >= 1.5 ? 'rgba(210,153,34,0.75)'
            : 'rgba(88,166,255,0.45)',
        };
      },
    }],
    calc: (list) => {
      const win = 20;
      return list.map((bar, i) => {
        if (i < win) return {};
        let sum = 0;
        for (let j = i - win; j < i; j++) sum += list[j].volume || 0;
        const base = sum / win;
        return base > 0 ? { rvol: (bar.volume || 0) / base } : {};
      });
    },
  });

  // Rolling 20-bar VWAP from the typical price — the daily-bar proxy, as in
  // the pipeline (true VWAP needs tick data).
  KLC.registerIndicator({
    name: 'MDVWAP',
    shortName: 'VWAP(20)',
    figures: [{ key: 'vwap', title: 'VWAP: ', type: 'line', styles: () => ({ color: '#e3b341' }) }],
    calc: (list) => {
      const win = 20;
      return list.map((_, i) => {
        if (i < win - 1) return {};
        let pv = 0, vol = 0;
        for (let j = i - win + 1; j <= i; j++) {
          const b = list[j];
          const typical = (b.high + b.low + b.close) / 3;
          pv += typical * (b.volume || 0);
          vol += b.volume || 0;
        }
        return vol > 0 ? { vwap: pv / vol } : {};
      });
    },
  });

  // The damped-trend forecast fan: point path plus the 90% band, drawn past
  // the last bar. Points use dataIndex — KLineChart extrapolates dataIndex
  // beyond the data range linearly (verified), where future *timestamps*
  // clamp to the last bar and misplace.
  KLC.registerOverlay({
    name: 'forecastFan',
    totalStep: 0,
    lock: true,
    createPointFigures: ({ overlay, coordinates, yAxis }) => {
      if (coordinates.length < 2 || !overlay.extendData) return [];
      const { lo, hi } = overlay.extendData;
      const figures = [];
      const toY = (v) => (yAxis ? yAxis.convertToPixel(v) : null);

      // 90% band polygon: anchor -> hi path -> back along lo path.
      const anchor = coordinates[0];
      const hiPts = [anchor];
      const loPts = [];
      for (let i = 1; i < coordinates.length; i++) {
        const x = coordinates[i].x;
        const yH = toY(hi[i - 1]);
        const yL = toY(lo[i - 1]);
        if (yH == null || yL == null) return figures;
        hiPts.push({ x, y: yH });
        loPts.push({ x, y: yL });
      }
      figures.push({
        type: 'polygon',
        attrs: { coordinates: hiPts.concat(loPts.reverse(), [anchor]) },
        styles: { style: 'fill', color: 'rgba(88,166,255,0.10)' },
        ignoreEvent: true,
      });
      figures.push({
        type: 'line',
        attrs: { coordinates },
        styles: { color: '#58a6ff', size: 2, style: 'dashed', dashedValue: [5, 4] },
        ignoreEvent: true,
      });
      return figures;
    },
  });
}

function chartStyles() {
  return {
    grid: {
      horizontal: { color: '#1e242d' },
      vertical: { color: '#1e242d' },
    },
    candle: {
      bar: {
        upColor: '#26a69a', downColor: '#ef5350', noChangeColor: '#8b98a8',
        upBorderColor: '#26a69a', downBorderColor: '#ef5350', noChangeBorderColor: '#8b98a8',
        upWickColor: '#26a69a', downWickColor: '#ef5350', noChangeWickColor: '#8b98a8',
      },
      priceMark: {
        high: { color: '#8b98a8' },
        low: { color: '#8b98a8' },
        last: {
          upColor: '#26a69a', downColor: '#ef5350', noChangeColor: '#8b98a8',
          text: { size: 11 },
        },
      },
      tooltip: {
        text: { size: 11, color: '#8b98a8' },
      },
    },
    indicator: {
      tooltip: { text: { size: 11, color: '#8b98a8' } },
      lastValueMark: { show: false },
    },
    xAxis: {
      axisLine: { color: '#262d38' },
      tickText: { color: '#5f6b7a', size: 11 },
      tickLine: { color: '#262d38' },
    },
    yAxis: {
      axisLine: { color: '#262d38' },
      tickText: { color: '#5f6b7a', size: 11 },
      tickLine: { color: '#262d38' },
    },
    separator: { color: '#262d38' },
    crosshair: {
      horizontal: {
        line: { color: '#5f6b7a' },
        text: { backgroundColor: '#262d38', size: 11 },
      },
      vertical: {
        line: { color: '#5f6b7a' },
        text: { backgroundColor: '#262d38', size: 11 },
      },
    },
    overlay: {
      point: { color: '#58a6ff', borderColor: 'rgba(88,166,255,0.35)' },
      line: { color: '#58a6ff' },
      polygon: { color: 'rgba(88,166,255,0.18)' },
    },
  };
}

function buildChart() {
  chartState.chart = KLC.init('chart', { styles: chartStyles() });
  syncIndicators();

  const box = el('chart');
  if (window.ResizeObserver) {
    new ResizeObserver(() => chartState.chart && chartState.chart.resize()).observe(box);
  }
}

/** Fold daily candles to the active timeframe. */
function aggregateBars(candles, tf) {
  const daily = candles.map((c) => ({
    timestamp: Date.parse(c.t + 'T00:00:00Z'),
    open: c.o, high: c.h, low: c.l, close: c.c, volume: c.v,
  }));
  if (tf === 'D') return daily;

  const keyOf = (ts) => {
    const d = new Date(ts);
    if (tf === 'M') return `${d.getUTCFullYear()}-${d.getUTCMonth()}`;
    // ISO-ish week bucket, Monday-based
    const monday = new Date(ts - ((d.getUTCDay() + 6) % 7) * 86400e3);
    return `${monday.getUTCFullYear()}-${monday.getUTCMonth()}-${monday.getUTCDate()}`;
  };

  const out = [];
  let bucket = null, bucketKey = null;
  for (const bar of daily) {
    const key = keyOf(bar.timestamp);
    if (key !== bucketKey) {
      if (bucket) out.push(bucket);
      bucket = { ...bar };
      bucketKey = key;
    } else {
      bucket.high = Math.max(bucket.high, bar.high);
      bucket.low = Math.min(bucket.low, bar.low);
      bucket.close = bar.close;
      bucket.volume += bar.volume;
      bucket.timestamp = bar.timestamp;   // stamp the bucket at its last session
    }
  }
  if (bucket) out.push(bucket);
  return out;
}

function loadChartData() {
  const payload = state.current;
  if (!payload || !chartState.chart) return;
  const chart = chartState.chart;

  chart.applyNewData(aggregateBars(payload.candles, state.timeframe));
  drawForecastFan();          // dataIndex-anchored, so it must follow every data swap
  applyRange();
}

function applyRange() {
  const chart = chartState.chart;
  const total = chart.getDataList().length;
  if (!total) return;
  const perBar = TF_SESSIONS[state.timeframe];
  const bars = state.range > 0
    ? Math.max(10, Math.min(Math.round(state.range / perBar), total))
    : total;
  const plotWidth = Math.max(el('chart').clientWidth - 70, 120);
  // Breathing room on the right. With the forecast fan on, pad enough that
  // the FIRST horizon stays on screen: solving rightPad = h·barSpace + 30
  // with barSpace = (plot − rightPad) / bars gives the closed form below.
  let rightPad = 60;
  const f = state.current && state.current.forecast;
  if (state.forecastOn && f && f.horizons && f.horizons.length) {
    const h = f.horizons[0].months * TF_BARS_PER_MONTH[state.timeframe];
    rightPad = Math.min((h * plotWidth / bars + 30) / (1 + h / bars), plotWidth * 0.45);
  }
  chart.setBarSpace(Math.min(Math.max((plotWidth - rightPad) / bars, 0.8), 26));
  chart.setOffsetRightDistance(rightPad);
  chart.scrollToRealTime();
}

function drawForecastFan() {
  const chart = chartState.chart;
  if (chartState.fanId) {
    chart.removeOverlay(chartState.fanId);
    chartState.fanId = null;
  }
  if (!state.forecastOn) return;

  const payload = state.current;
  const f = payload && payload.forecast;
  if (!f || !f.horizons || !f.horizons.length) return;
  const data = chart.getDataList();
  if (!data.length) return;

  const lastIdx = data.length - 1;
  const lastClose = data[lastIdx].close;
  const perMonth = TF_BARS_PER_MONTH[state.timeframe];

  const points = [{ dataIndex: lastIdx, value: lastClose }];
  const lo = [], hi = [];
  for (const h of f.horizons) {
    points.push({ dataIndex: lastIdx + Math.max(1, Math.round(h.months * perMonth)), value: h.value });
    lo.push(h.lo90);
    hi.push(h.hi90);
  }
  chartState.fanId = chart.createOverlay({
    name: 'forecastFan',
    points,
    extendData: { lo, hi },
  });
}

/** Reconcile the chart's indicator panes with the toggle state. */
function syncIndicators() {
  const chart = chartState.chart;

  // main-pane overlays (stacked on the candles)
  for (const name of ['MA', 'EMA', 'BOLL', 'MDVWAP']) {
    const want = state.mainInds.has(name);
    const have = chartState.subPanes.has('main:' + name);
    if (want && !have) {
      const spec = name === 'MA' ? { name: 'MA', calcParams: [20, 50, 200] }
        : name === 'EMA' ? { name: 'EMA', calcParams: [12, 26] }
        : { name };
      chart.createIndicator(spec, true, { id: 'candle_pane' });
      chartState.subPanes.set('main:' + name, 'candle_pane');
    } else if (!want && have) {
      chart.removeIndicator('candle_pane', name);
      chartState.subPanes.delete('main:' + name);
    }
  }

  // sub-panes
  for (const name of ['VOL', 'MDRVOL', 'RSI', 'MACD', 'OBV', 'KDJ']) {
    const want = state.subInds.has(name);
    const paneId = chartState.subPanes.get('sub:' + name);
    if (want && !paneId) {
      // Match the pipeline's parameters: RSI-14 (the number the screen table
      // reports), one 20-bar volume MA instead of the default three.
      const spec = name === 'RSI' ? { name: 'RSI', calcParams: [14] }
        : name === 'VOL' ? { name: 'VOL', calcParams: [20] }
        : name;
      const id = chart.createIndicator(spec, false);
      chartState.subPanes.set('sub:' + name, id);
    } else if (!want && paneId) {
      chart.removeIndicator(paneId, name);
      chartState.subPanes.delete('sub:' + name);
    }
  }
}

/* ---------------- drawing rail ---------------- */

function wireDrawRail() {
  const rail = el('draw-rail');
  rail.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || !b.hasAttribute('data-draw')) return;
    const tool = b.dataset.draw;
    state.activeDrawTool = tool;
    rail.querySelectorAll('button[data-draw]').forEach((x) =>
      x.classList.toggle('is-active', x === b));
    if (tool) startDrawing(tool);
  });

  el('magnet-btn').addEventListener('click', () => {
    state.magnet = !state.magnet;
    el('magnet-btn').classList.toggle('is-active', state.magnet);
  });

  el('erase-btn').addEventListener('click', () => {
    for (const id of chartState.drawnIds) chartState.chart.removeOverlay(id);
    chartState.drawnIds = [];
  });
}

function startDrawing(tool) {
  const id = chartState.chart.createOverlay({
    name: tool,
    mode: state.magnet ? 'weak_magnet' : 'normal',
    onDrawEnd: () => {
      // Drop back to the pointer once the shape is placed — TradingView's
      // behavior, and it stops every later click from starting a new one.
      state.activeDrawTool = '';
      el('draw-rail').querySelectorAll('button[data-draw]').forEach((x) =>
        x.classList.toggle('is-active', x.dataset.draw === ''));
      return false;
    },
    onRightClick: (e) => {
      chartState.chart.removeOverlay(e.overlay.id);
      chartState.drawnIds = chartState.drawnIds.filter((x) => x !== e.overlay.id);
      return true;
    },
  });
  if (id) chartState.drawnIds.push(id);
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
      if (av == null) return 1;
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
      showBanner(`Could not load ${symbol}.`);
      return;
    }
  }

  state.current = payload;
  renderSymbolHead();
  loadChartData();
  renderCards();
  renderTimingCard();

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
  el('ask-symbol').textContent = p.symbol;
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

function scoreBar(label, value, formatted) {
  const pct = value == null ? 0 : Math.round(value * 100);
  return `<div class="fbar">
    <span class="lab">${esc(label)}</span>
    <div class="fbar-track">${value == null ? '' : `<div class="fbar-fill" style="width:${pct}%"></div>`}</div>
    <span class="val">${esc(formatted)}</span>
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

  // --- factors ---
  const fac = p.factors;
  if (fac && !fac.is_fund && (fac.momentum.rank != null || fac.value.score != null)) {
    const m = fac.momentum;
    html = scoreBar('Momentum', m.rank, fmt.score(m.rank))
      + scoreBar('Value', fac.value.score, fmt.score(fac.value.score))
      + scoreBar('Quality', fac.quality.score, fmt.score(fac.quality.score));
    html += `<dl class="kv" style="margin-top:10px">
      <dt>12-1 formation</dt><dd class="${fmt.cls(m.mom_12_1)}">${fmt.signedPct(m.mom_12_1, 1)}</dd>
      <dt>Skipped month (reversal window)</dt><dd class="${fmt.cls(m.ret_1m)}">${fmt.signedPct(m.ret_1m, 1)}</dd>
      <dt>EV/EBITDA</dt><dd>${fmt.num(fac.value.ev_ebitda, 1)}</dd>
      <dt>FCF yield</dt><dd>${fmt.pct(fac.value.fcf_yield, 1)}</dd>
      <dt>ROE / ROA</dt><dd>${fmt.pct(fac.quality.roe, 1)} / ${fmt.pct(fac.quality.roa, 1)}</dd>
      <dt>Debt / equity</dt><dd>${fmt.num(fac.quality.debt_to_equity)}</dd>
    </dl>`;
    if (fac.value_trap) html += `<p class="note"><span class="flag flag-trap">value trap</span></p>`;
    if (fac.reversal_tension) html += `<p class="note"><span class="flag flag-rev">reversal tension</span></p>`;
    for (const note of fac.notes || []) html += `<p class="note faint">${esc(note)}</p>`;
    html += `<p class="note faint">Ranks are within the ${fac.universe_n}-company tracked universe, not the market.</p>`;
  } else if (fac && fac.is_fund) {
    const m = fac.momentum;
    html = `<dl class="kv">
      <dt>12-1 formation return</dt><dd class="${fmt.cls(m.mom_12_1)}">${fmt.signedPct(m.mom_12_1, 1)}</dd>
      <dt>Skipped month</dt><dd class="${fmt.cls(m.ret_1m)}">${fmt.signedPct(m.ret_1m, 1)}</dd>
    </dl>`;
    for (const note of fac.notes || []) html += `<p class="note faint">${esc(note)}</p>`;
  } else {
    html = '<p class="note">No factor data for this symbol.</p>';
  }
  el('card-factors').querySelector('.card-body').innerHTML = html;

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
    html += `<p class="note faint">Equity prices are close to a random walk. The point is a damped trend, not a prediction of skill; the band is the honest content, and it is wide on purpose. Toggle "Forecast" above the chart to draw it.</p>`;
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

function sortRows(rows, key, dir) {
  return rows.slice().sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });
}

function renderScreen() {
  const { key, dir } = state.screenSort;
  const rows = sortRows(state.rows, key, dir);

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

/* ---------------- factors table ---------------- */

const FACTOR_COLS = [
  ['symbol', (r) => `<td class="sym sticky-col">${esc(r.symbol)}</td>`],
  ['mom_12_1', (r) => `<td class="num ${fmt.cls(r.mom_12_1)}">${fmt.signedPct(r.mom_12_1, 1)}</td>`],
  ['mom_6_1', (r) => `<td class="num ${fmt.cls(r.mom_6_1)}">${fmt.signedPct(r.mom_6_1, 1)}</td>`],
  ['ret_1m', (r) => `<td class="num faint">${fmt.signedPct(r.ret_1m, 1)}</td>`],
  ['mom_rank', (r) => `<td class="num faint">${fmt.score(r.mom_rank)}</td>`],
  ['bench_mom_rank', (r) => `<td class="num">${fmt.score(r.bench_mom_rank)}</td>`],
  ['ev_ebitda', (r) => `<td class="num">${fmt.num(r.ev_ebitda, 1)}</td>`],
  ['fcf_yield', (r) => `<td class="num">${fmt.pct(r.fcf_yield, 1)}</td>`],
  ['earnings_yield', (r) => `<td class="num">${fmt.pct(r.earnings_yield, 1)}</td>`],
  ['value_score', (r) => `<td class="num faint">${fmt.score(r.value_score)}</td>`],
  ['bench_value_score', (r) => `<td class="num" title="${esc(r.bench_value_population || '')}${r.bench_value_n ? ' (n=' + r.bench_value_n + ')' : ''}">${fmt.score(r.bench_value_score)}</td>`],
  ['roe', (r) => `<td class="num">${fmt.pct(r.roe, 1)}</td>`],
  ['roa', (r) => `<td class="num">${fmt.pct(r.roa, 1)}</td>`],
  ['op_margin', (r) => `<td class="num">${fmt.pct(r.op_margin, 1)}</td>`],
  ['debt_to_equity', (r) => `<td class="num">${fmt.num(r.debt_to_equity)}</td>`],
  ['quality_score', (r) => `<td class="num">${fmt.score(r.quality_score)}</td>`],
  ['flags', (r) => {
    const flags = [];
    if (r.value_trap) flags.push('<span class="flag flag-trap">trap</span>');
    if (r.reversal_tension) flags.push('<span class="flag flag-rev">reversal</span>');
    if (r.quote_type === 'ETF') flags.push('<span class="flag flag-fund">fund</span>');
    return `<td>${flags.join(' ')}</td>`;
  }],
];

function renderFactors() {
  const caveat = (state.meta && state.meta.factor_caveat) || '';
  el('factor-caveat').textContent = caveat;

  const b = state.benchmark;
  el('bench-note').innerHTML = b
    ? `Dimmed columns are watchlist-relative (n=${(state.rows.filter((r) => r.mom_rank != null)).length}); `
      + `the <strong>S&amp;P</strong> columns rank against ${b.n} ${esc(b.index)} constituents as of ${esc(b.as_of)} — `
      + 'momentum against the whole index, value <em>within each name\'s own sector</em>, '
      + 'because raw multiples are dominated by industry effects. Hover a value cell for its peer group and size.'
    : 'Benchmark cross-section unavailable — all percentiles below are watchlist-relative.';

  const rows = state.rows;
  const { key, dir } = state.factorSort;
  const companies = sortRows(rows.filter((r) => r.mom_rank != null), key, dir);
  const funds = sortRows(rows.filter((r) => r.mom_rank == null), 'mom_12_1', -1);

  el('factor-table').querySelector('tbody').innerHTML =
    companies.concat(funds).map((r) =>
      `<tr data-symbol="${esc(r.symbol)}">${FACTOR_COLS.map(([, render]) => render(r)).join('')}</tr>`
    ).join('');

  document.querySelectorAll('#factor-table th[data-key]').forEach((th) => {
    th.classList.toggle('sorted', th.dataset.key === key);
    th.classList.toggle('asc', th.dataset.key === key && dir === 1);
  });

  el('factor-table').querySelectorAll('tbody tr').forEach((tr) => {
    tr.addEventListener('click', () => {
      selectSymbol(tr.dataset.symbol);
      switchView('chart');
    });
  });
}

/* ---------------- minimal markdown ----------------
 * Claude's answers and the committed notes are markdown. Rather than ship a
 * parser, this renders the small subset those two actually use. Everything is
 * HTML-escaped BEFORE any markup is introduced, so note and model text can
 * never inject markup into the page.
 */

function markdown(src) {
  const lines = esc(src).split('\n');
  const out = [];
  let inList = null;      // 'ul' | 'ol' | null

  const closeList = () => { if (inList) { out.push(`</${inList}>`); inList = null; } };
  const inline = (t) => t
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);

    if (heading) {
      closeList();
      const level = Math.min(heading[1].length, 3);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (bullet) {
      if (inList !== 'ul') { closeList(); out.push('<ul>'); inList = 'ul'; }
      out.push(`<li>${inline(bullet[1])}</li>`);
    } else if (numbered) {
      if (inList !== 'ol') { closeList(); out.push('<ol>'); inList = 'ol'; }
      out.push(`<li>${inline(numbered[1])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join('');
}

/* ---------------- ask Claude ---------------- */

/** Detect the local companion server. The published static site has none. */
async function probeAsk() {
  const status = el('ask-status');
  const answer = el('ask-answer');
  let health = null;

  try {
    health = await fetch('api/health', { cache: 'no-store' }).then((r) => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  } catch (err) {
    health = null;              // no companion server answering at all
  }

  state.askReady = !!(health && health.claude);
  setAskEnabled(state.askReady);

  if (state.askReady) {
    status.textContent = `ready · ${health.model || 'claude'}`;
    status.className = 'ask-status is-ready';
    answer.hidden = true;
    return;
  }

  status.className = 'ask-status is-off';
  answer.hidden = false;

  // Two genuinely different failures with two different fixes. Telling
  // someone to start a server that is already running wastes their time.
  if (!health) {
    status.textContent = 'local server not running';
    answer.innerHTML =
      '<p>Questions are answered by a companion server on your own machine. The published ' +
      'site is static and world-readable, so it has nowhere to keep a credential — anything ' +
      'it could call, anyone could call. Start the server with:</p>' +
      '<p><code>python scripts/desk_server.py</code></p>' +
      '<p class="ask-foot">It serves this same dashboard at <code>127.0.0.1:8793</code> and ' +
      'answers through the Claude Code CLI, so questions bill against your existing ' +
      'subscription rather than a separate API key.</p>';
  } else {
    status.textContent = 'Claude not authenticated';
    answer.innerHTML =
      '<p>The companion server is running, but it cannot reach Claude:</p>' +
      `<p class="err">${esc(health.reason || 'unknown reason')}</p>` +
      '<p>Run <code>claude setup-token</code>, then restart the server with the token in ' +
      'the environment:</p>' +
      '<p><code>export CLAUDE_CODE_OAUTH_TOKEN=&lt;token&gt;</code><br>' +
      '<code>python scripts/desk_server.py</code></p>' +
      '<p class="ask-foot">That is the same secret the daily analysis notes need as a ' +
      'repository secret, so one token turns on both.</p>';
  }
}

function setAskEnabled(on) {
  el('ask-input').disabled = !on;
  el('ask-send').disabled = !on || state.askBusy;
  el('ask-presets').querySelectorAll('button').forEach((b) => { b.disabled = !on || state.askBusy; });
}

async function askClaude(question) {
  if (!state.askReady || state.askBusy || !question.trim()) return;
  state.askBusy = true;
  setAskEnabled(true);

  const answer = el('ask-answer');
  answer.hidden = false;
  answer.innerHTML = '<p class="thinking">Thinking…</p>';

  try {
    const res = await fetch('api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, symbol: state.currentSymbol }),
    });
    const data = await res.json();
    if (data.answer) {
      answer.innerHTML = markdown(data.answer) +
        `<p class="ask-foot">${esc(data.model || 'claude')} · answered from the committed ` +
        `payloads for ${esc(data.symbol || 'the board')}. Analysis, not advice.</p>`;
    } else {
      answer.innerHTML = `<p class="err">${esc(data.error || 'No answer returned.')}</p>`;
    }
  } catch (err) {
    answer.innerHTML = `<p class="err">Could not reach the local server.</p>`;
  } finally {
    state.askBusy = false;
    setAskEnabled(state.askReady);
  }
}

/* ---------------- portfolio ---------------- */

/** One factor tilt bar, drawn against a marked 0.5 midpoint. */
function tiltBar(label, weighted, equal, lowLabel, highLabel) {
  if (weighted == null) {
    return `<div class="tilt">
      <div class="tilt-head"><span class="tilt-name">${esc(label)}</span>
      <span class="tilt-val faint">no score</span></div>
      <div class="tilt-track"><div class="tilt-mid"></div></div>
      <div class="tilt-foot"><span>no holding carried this metric</span></div>
    </div>`;
  }
  const pct = Math.round(weighted * 100);
  const from = Math.min(50, pct);
  const width = Math.abs(pct - 50);
  const cls = weighted >= 0.5 ? 'pos' : 'neg';
  const eq = equal == null ? '' :
    `<div class="tilt-eq" style="left:calc(${Math.round(equal * 100)}% - 1px)" title="equal-weighted: ${equal.toFixed(2)}"></div>`;
  const gap = equal == null ? '' :
    ` · sizing moves it ${weighted >= equal ? '+' : ''}${(weighted - equal).toFixed(2)} vs equal-weight`;
  return `<div class="tilt">
    <div class="tilt-head">
      <span class="tilt-name">${esc(label)}</span>
      <span class="tilt-val">${weighted.toFixed(2)}</span>
    </div>
    <div class="tilt-track">
      <div class="tilt-fill ${cls}" style="left:${from}%;width:${width}%"></div>
      <div class="tilt-mid"></div>
      ${eq}
    </div>
    <div class="tilt-foot">
      <span>${esc(lowLabel)}</span>
      <span>${esc(highLabel)}</span>
    </div>
    <div class="tilt-foot"><span class="faint">0.5 = middle of the board${gap}</span></div>
  </div>`;
}

function renderPortfolio() {
  const pf = state.portfolio;
  const empty = el('pf-empty');
  const content = el('pf-content');

  if (!pf) {
    content.hidden = true;
    empty.hidden = false;
    empty.innerHTML =
      'No portfolio configured. Add positions to <code>config/holdings.yml</code> ' +
      '(tickers and percent weights) and re-run <code>python scripts/refresh.py</code>.';
    return;
  }
  empty.hidden = true;
  content.hidden = false;

  const universeN = (state.rows.find((r) => r.mom_rank != null) || {});
  const n = state.meta && state.meta.symbols_ok;
  const bench = state.benchmark;
  el('pf-stamp').textContent = (pf.population === 'S&P 500' && bench)
    ? `${pf.n_positions} positions as of ${pf.as_of || '—'} · ranked against `
      + `${bench.n} S&P 500 constituents — momentum index-wide, value and quality `
      + `within each holding's own sector.`
    : `${pf.n_positions} positions as of ${pf.as_of || '—'} · ranked against the `
      + `tracked cross-section (${n || '—'} symbols). Watchlist-relative, not `
      + `market factor exposures.`;

  el('pf-tilts').innerHTML =
    tiltBar('Momentum', pf.tilts.momentum, pf.tilts.momentum_equal, 'laggards', 'leaders') +
    tiltBar('Value', pf.tilts.value, pf.tilts.value_equal, 'expensive', 'cheap') +
    tiltBar('Quality', pf.tilts.quality, pf.tilts.quality_equal, 'weaker', 'stronger');

  el('pf-tilt-note').textContent =
    'The blue marker is where the same holdings would sit equally weighted — ' +
    'the gap from the bar is what your position sizing is doing.';

  const c = pf.concentration || {};
  el('pf-concentration').innerHTML = `
    <dt>Positions</dt><dd>${pf.n_positions}</dd>
    <dt>Effective positions</dt><dd>${fmt.num(c.effective_positions, 1)}</dd>
    <dt>Largest holding</dt><dd>${fmt.pct(c.top_weight, 0)}</dd>
    <dt>HHI</dt><dd>${fmt.num(c.hhi, 3)}</dd>
    <dt>Cash</dt><dd>${fmt.pct(pf.cash_weight, 0)}</dd>
    <dt>Equity sleeve</dt><dd>${fmt.pct(pf.equity_weight, 0)}</dd>`;

  const sectors = Object.entries(pf.sector_weights || {}).sort((a, b) => b[1] - a[1]);
  const maxW = sectors.length ? sectors[0][1] : 1;
  el('pf-sectors').innerHTML = sectors.map(([name, w]) =>
    `<div class="sector-row">
      <span class="lab">${esc(name)}</span>
      <div class="sector-bar"><span style="width:${Math.round((w / maxW) * 100)}%"></span></div>
      <span class="val">${fmt.pct(w, 0)}</span>
    </div>`).join('');

  el('pf-table').querySelector('tbody').innerHTML = (pf.positions || []).map((p) => {
    const flags = [];
    if (p.value_trap) flags.push('<span class="flag flag-trap">trap</span>');
    if (p.reversal_tension) flags.push('<span class="flag flag-rev">reversal</span>');
    if (!p.scored) flags.push('<span class="flag flag-fund">unscored</span>');
    const score = (v) => v == null
      ? '<span class="faint" title="no usable metric">—</span>'
      : v.toFixed(2);
    return `<tr data-symbol="${esc(p.symbol)}">
      <td class="sym sticky-col">${esc(p.symbol)}</td>
      <td class="num">${fmt.pct(p.weight, 1)}</td>
      <td class="num faint">${fmt.pct(p.account_weight, 1)}</td>
      <td class="name" title="${esc(p.value_peer_group ? 'value/quality peers: ' + p.value_peer_group : '')}">${esc(p.sector || '—')}</td>
      <td class="num">${score(p.momentum_rank)}</td>
      <td class="num">${score(p.value_score)}</td>
      <td class="num">${score(p.quality_score)}</td>
      <td>${flags.join(' ')}</td>
    </tr>`;
  }).join('');

  el('pf-table').querySelectorAll('tbody tr').forEach((tr) => {
    tr.addEventListener('click', () => {
      selectSymbol(tr.dataset.symbol);
      switchView('chart');
    });
  });

  el('pf-notes').innerHTML = (pf.notes || []).map((t) => `<p>${esc(t)}</p>`).join('');
  renderCrashRisk(pf.crash_risk);
  renderDrift();
}

/* ---------------- momentum crash risk ---------------- */

function renderCrashRisk(cr) {
  const body = el('pf-crash').querySelector('.card-body');
  if (!cr || !cr.state) {
    body.innerHTML = '<p class="note">No benchmark history available.</p>';
    return;
  }
  const s = cr.state;
  const e = cr.evidence;

  let html = `<p class="note">
    <span class="exposure exposure-${esc(cr.exposure)}">${esc(cr.exposure)} exposure</span>
  </p>
  <dl class="kv">
    <dt>Market state</dt><dd>${esc(s.label)}</dd>
    <dt>Drawdown from peak</dt><dd>${fmt.pct(s.drawdown, 1)}</dd>
    <dt>Trailing 24-month</dt><dd>${fmt.signedPct(s.trailing_return, 1)}</dd>
    <dt>Volatility</dt><dd>${esc(s.vol_regime)}</dd>
    <dt>Your momentum tilt</dt><dd>${fmt.score(cr.momentum_tilt)}</dd>
  </dl>
  <p class="note">${esc(s.detail)}</p>`;

  if (e && e.buckets && e.buckets.length) {
    html += `<table class="ev-table">
      <thead><tr><th>Market state</th><th>Obs</th><th>1-mo spread</th></tr></thead>
      <tbody>${e.buckets.map((b) => `
        <tr class="${b.conclusive ? '' : 'thin'}">
          <td class="lab">${esc(b.label)}</td>
          <td class="ev-n">${b.n}</td>
          <td class="${fmt.cls(b.mean_spread)}">${fmt.signedPct(b.mean_spread, 2)}</td>
        </tr>`).join('')}</tbody>
    </table>
    <p class="note faint">Forward 1-month return of the top momentum tercile minus
    the bottom, on this watchlist, grouped by how far the market was off its peak.
    Dimmed rows fall below the sample size needed to read them as a result.</p>`;
  }

  for (const note of cr.notes || []) html += `<p class="note faint">${esc(note)}</p>`;
  if (e && e.caveat) html += `<p class="note faint">${esc(e.caveat)}</p>`;
  body.innerHTML = html;
}

/* ---------------- factor drift ---------------- */

/** Sparkline over a 0-1 factor series, with the 0.5 midpoint drawn in. */
function sparkline(points, key, color) {
  const vals = points.filter((p) => p[key] != null);
  if (vals.length < 2) return null;

  const W = 100, H = 30, pad = 2;
  const n = vals.length;
  const x = (i) => pad + (i / (n - 1)) * (W - pad * 2);
  // Factor scores are already 0-1 percentiles, so the axis is fixed rather
  // than auto-scaled: a flat line at 0.9 should look different from a flat
  // line at 0.1, which auto-scaling would render identically.
  const y = (v) => pad + (1 - v) * (H - pad * 2);

  const path = vals.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join('');
  const mid = y(0.5).toFixed(1);
  const last = vals[n - 1];

  // Mark where forward-accumulating data starts, when the series mixes
  // reconstructed and live points.
  const firstLive = vals.findIndex((p) => p.s === 'live');
  const liveMark = firstLive > 0
    ? `<line x1="${x(firstLive).toFixed(1)}" y1="${pad}" x2="${x(firstLive).toFixed(1)}" y2="${H - pad}" stroke="var(--text-faint)" stroke-width="1" stroke-dasharray="2 2" opacity="0.5"/>`
    : '';

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
    <line x1="${pad}" y1="${mid}" x2="${W - pad}" y2="${mid}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3 3"/>
    ${liveMark}
    <path d="${path}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
    <circle cx="${x(n - 1).toFixed(1)}" cy="${y(last[key]).toFixed(1)}" r="2" fill="${color}"/>
  </svg>`;
}

function driftCell(label, points, key, color) {
  const vals = points.filter((p) => p[key] != null);
  const spark = sparkline(points, key, color);
  if (!spark) {
    const why = key === 'm'
      ? 'not enough history'
      : 'starts accumulating from the first live run';
    return `<div class="drift-cell">
      <div class="drift-head"><span>${esc(label)}</span><span class="drift-delta flat">—</span></div>
      <div class="drift-none">${esc(why)}</div>
    </div>`;
  }
  const change = vals[vals.length - 1][key] - vals[0][key];
  const cls = Math.abs(change) < 0.05 ? 'flat' : change > 0 ? 'up' : 'down';
  const sign = change > 0 ? '+' : '';
  return `<div class="drift-cell" title="${vals.length} samples, ${esc(vals[0].d)} → ${esc(vals[vals.length - 1].d)}">
    <div class="drift-head">
      <span>${esc(label)} ${vals[vals.length - 1][key].toFixed(2)}</span>
      <span class="drift-delta ${cls}">${sign}${change.toFixed(2)}</span>
    </div>
    ${spark}
  </div>`;
}

function renderDrift() {
  const host = el('pf-drift');
  const pf = state.portfolio;
  if (!host || !pf) return;

  // A single live point cannot be drawn, so "accumulating" is only the
  // right message once some symbol has at least two.
  const anyLive = Object.values(state.history).some((pts) =>
    pts.filter((p) => p.s === 'live' && (p.v != null || p.q != null)).length >= 2);
  el('pf-drift-lede').innerHTML = anyLive
    ? 'Momentum rank is reconstructed from price history; value and quality accumulate forward from the first run, so their series start short. The dashed vertical marks where live snapshots begin. Dashed horizontal is the 0.5 midpoint.'
    : 'Momentum rank is reconstructed from price history — the 12-1 window at any date uses only bars up to that date. Value and quality have no published history and accumulate forward from today, so they will fill in over coming sessions.';

  host.innerHTML = (pf.positions || []).map((p) => {
    const pts = state.history[p.symbol] || [];
    return `<div class="drift-row" data-symbol="${esc(p.symbol)}">
      <div class="drift-sym">${esc(p.symbol)}<small>${fmt.pct(p.weight, 0)}</small></div>
      ${driftCell('Momentum', pts, 'm', '#58a6ff')}
      ${driftCell('Value', pts, 'v', '#26a69a')}
      ${driftCell('Quality', pts, 'q', '#a371f7')}
    </div>`;
  }).join('');

  host.querySelectorAll('.drift-row').forEach((row) => {
    row.addEventListener('click', () => {
      selectSymbol(row.dataset.symbol);
      switchView('chart');
    });
  });
}

/* ---------------- timing ---------------- */

const VERDICT_CLASS = {
  confirmed: 'verdict-confirmed2',
  weak: 'verdict-weak2',
};

function renderTiming() {
  // --- catalyst calendar ---
  const events = (state.calendar && state.calendar.events) || [];
  el('cal-table').querySelector('tbody').innerHTML = events.length
    ? events.map((e) => {
        const soon = e.days_until != null && e.days_until <= 14;
        const ampCls = e.amplification == null ? ''
          : e.amplification >= 2 ? 'amp-strong'
          : e.amplification < 1.25 ? 'amp-weak' : '';
        return `<tr data-symbol="${esc(e.symbol)}">
          <td class="sym sticky-col">${esc(e.symbol)}</td>
          <td>${esc(e.date)}</td>
          <td class="num ${soon ? 'cal-soon' : ''}">${e.days_until == null ? '—' : e.days_until + 'd'}</td>
          <td class="num">${fmt.pct(e.median_move, 1)}</td>
          <td class="num faint">${fmt.pct(e.baseline_move, 1)}</td>
          <td class="num ${ampCls}">${e.amplification == null ? '—' : e.amplification.toFixed(1) + '×'}</td>
          <td class="num faint">${e.n_events || '—'}</td>
          <td class="name">${esc(e.tier || '')}</td>
        </tr>`;
      }).join('')
    : '<tr><td colspan="8" class="empty">No scheduled earnings dates available.</td></tr>';

  // --- volatility regimes ---
  // Read straight from the index rows, so the table is complete on load.
  // Confirmed rows sort first: an unvalidated label is context, not signal,
  // and should not head the list.
  const order = { confirmed: 0, weak: 1, 'no separation': 2 };
  const vrows = state.rows
    .filter((r) => r.regime)
    .sort((a, b) => (order[a.regime_verdict] ?? 3) - (order[b.regime_verdict] ?? 3)
                 || (b.regime_separation ?? 0) - (a.regime_separation ?? 0));

  el('vol-table').querySelector('tbody').innerHTML = vrows.length
    ? vrows.map((r) => {
        const unvalidated = r.regime_verdict !== 'confirmed';
        const vcls = VERDICT_CLASS[r.regime_verdict] || 'verdict-none2';
        return `<tr data-symbol="${esc(r.symbol)}" class="${unvalidated ? 'unvalidated' : ''}">
          <td class="sym sticky-col">${esc(r.symbol)}</td>
          <td><span class="regime regime-${esc(r.regime)}">${esc(r.regime)}</span></td>
          <td class="num">${r.regime_percentile == null ? '—' : Math.round(r.regime_percentile * 100)}</td>
          <td class="num">${fmt.pct(r.regime_ann_vol, 0)}</td>
          <td class="num">${r.expected_week_pct == null ? '—' : '±' + fmt.pct(r.expected_week_pct, 1)}</td>
          <td class="num">${r.regime_separation == null ? '—' : r.regime_separation.toFixed(2) + '×'}</td>
          <td><span class="verdict ${vcls}">${esc(r.regime_verdict || '—')}</span></td>
        </tr>`;
      }).join('')
    : '<tr><td colspan="7" class="empty">No regime data yet — run the refresh.</td></tr>';

  for (const id of ['cal-table', 'vol-table']) {
    el(id).querySelectorAll('tbody tr[data-symbol]').forEach((tr) => {
      tr.addEventListener('click', () => {
        selectSymbol(tr.dataset.symbol);
        switchView('chart');
      });
    });
  }
}

function renderTimingCard() {
  const p = state.current;
  const body = el('card-timing').querySelector('.card-body');
  const t = p && p.timing;
  if (!t) { body.innerHTML = '<p class="note">No timing data.</p>'; return; }

  const reg = t.regime || {};
  const v = t.validation || {};
  const r = t.expected_range || {};
  const c = t.catalysts || {};

  let html = `<p class="note">
    <span class="regime regime-${esc(reg.label || 'unknown')}">${esc(reg.label || '—')}</span>
    ${reg.percentile == null ? '' : `<span class="faint"> · ${Math.round(reg.percentile * 100)}th percentile of its own year</span>`}
  </p>
  <p class="note">${esc(reg.detail || '')}</p>
  <dl class="kv">
    <dt>Annualized vol</dt><dd>${fmt.pct(reg.annualized_vol, 0)}</dd>
    <dt>Expected ±1 day</dt><dd>${r['1d'] ? '±' + fmt.pct(r['1d'].pct, 1) : '—'}</dd>
    <dt>Expected ±1 week</dt><dd>${r['1w'] ? '±' + fmt.pct(r['1w'].pct, 1) : '—'}</dd>
    <dt>Expected ±1 month</dt><dd>${r['1m'] ? '±' + fmt.pct(r['1m'].pct, 1) : '—'}</dd>
  </dl>`;

  if (v.verdict === 'confirmed') {
    html += `<p class="note">Regime labels are <strong>validated</strong> on this series: turbulent stretches were followed by moves ${v.separation ? v.separation.toFixed(2) + '×' : ''} the size of quiet ones across ${v.n} walk-forward observations.</p>`;
  } else if (v.verdict === 'weak') {
    html += `<p class="note faint">Regime labels separate future moves only weakly here (${v.separation ? v.separation.toFixed(2) + '×' : '—'}). Read the label as description, not signal.</p>`;
  } else {
    html += `<p class="note faint">Regime labels show <strong>no measured predictive content</strong> on this series (${v.separation ? v.separation.toFixed(2) + '×' : '—'} separation). The label describes current volatility; it does not forecast future moves for this name.</p>`;
  }

  html += `<p class="note">${esc(c.summary || '')}</p>`;
  if (c.ex_dividend) html += `<p class="note faint">Ex-dividend ${esc(c.ex_dividend)}.</p>`;
  html += `<p class="note faint">Magnitude only — none of this indicates direction.</p>`;
  body.innerHTML = html;
}

/* ---------------- analysis notes ---------------- */

function renderNotes() {
  const index = el('notes-index');
  const body = el('note-body');

  if (!state.notes.length) {
    index.innerHTML = '';
    body.innerHTML =
      '<h1>No notes yet</h1>' +
      '<p>The daily refresh writes one note per trading session into <code>analysis/</code>, ' +
      'but only when a <code>CLAUDE_CODE_OAUTH_TOKEN</code> secret is set on the repository. ' +
      'Until then the workflow publishes the dashboard and skips the note.</p>' +
      '<p>To turn them on: run <code>claude setup-token</code>, add the value as a repository ' +
      'secret named <code>CLAUDE_CODE_OAUTH_TOKEN</code>, and set <code>TOKEN_ISSUED</code> in ' +
      '<code>.github/workflows/refresh.yml</code> to today.</p>';
    return;
  }

  index.innerHTML = state.notes.map((n) =>
    `<button data-date="${esc(n.date)}">${esc(n.date)}</button>`).join('');
  index.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => showNote(b.dataset.date));
  });
  showNote(state.activeNote || state.notes[0].date);
}

function showNote(date) {
  const note = state.notes.find((n) => n.date === date);
  if (!note) return;
  state.activeNote = date;
  el('note-body').innerHTML = markdown(note.body);
  el('notes-index').querySelectorAll('button').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.date === date));
}

/* ---------------- controls ---------------- */

function switchView(view) {
  document.querySelectorAll('.view-tab').forEach((b) => b.classList.toggle('is-active', b.dataset.view === view));
  for (const id of ['chart', 'screen', 'factors', 'portfolio', 'timing', 'analysis']) {
    el('view-' + id).classList.toggle('is-active', id === view);
  }
  if (view === 'chart' && chartState.chart) {
    chartState.chart.resize();
  }
}

function wireControls() {
  document.querySelectorAll('.view-tab').forEach((b) =>
    b.addEventListener('click', () => switchView(b.dataset.view)));

  el('filter').addEventListener('input', (e) => { state.filter = e.target.value; renderSidebar(); });
  el('sort').addEventListener('change', (e) => { state.sortKey = e.target.value; renderSidebar(); });

  el('tf-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.timeframe = b.dataset.tf;
    el('tf-buttons').querySelectorAll('button').forEach((x) => x.classList.toggle('is-active', x === b));
    loadChartData();
  });

  el('range-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.range = Number(b.dataset.range);
    el('range-buttons').querySelectorAll('button').forEach((x) => x.classList.toggle('is-active', x === b));
    applyRange();
  });

  el('main-ind-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    const key = b.dataset.ind;
    if (key === 'forecast') {
      state.forecastOn = !state.forecastOn;
      b.classList.toggle('is-active', state.forecastOn);
      drawForecastFan();
      applyRange();
      return;
    }
    if (state.mainInds.has(key)) state.mainInds.delete(key); else state.mainInds.add(key);
    b.classList.toggle('is-active', state.mainInds.has(key));
    syncIndicators();
  });

  el('sub-ind-buttons').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    const key = b.dataset.ind;
    if (state.subInds.has(key)) state.subInds.delete(key); else state.subInds.add(key);
    b.classList.toggle('is-active', state.subInds.has(key));
    syncIndicators();
  });

  wireDrawRail();

  el('screen-table').querySelectorAll('th').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.key;
      if (!key) return;
      if (state.screenSort.key === key) state.screenSort.dir *= -1;
      else state.screenSort = { key, dir: key === 'symbol' || key === 'divergence' ? 1 : -1 };
      renderScreen();
    });
  });

  el('factor-table').querySelectorAll('th[data-key]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.key;
      if (state.factorSort.key === key) state.factorSort.dir *= -1;
      else state.factorSort = { key, dir: key === 'symbol' ? 1 : -1 };
      renderFactors();
    });
  });

  el('ask-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = el('ask-input');
    askClaude(input.value);
  });

  el('ask-input').addEventListener('keydown', (e) => {
    // Enter sends; Shift+Enter is a newline — the convention for a chat box.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      askClaude(e.target.value);
    }
  });

  el('ask-presets').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.disabled) return;
    el('ask-input').value = b.dataset.q;
    askClaude(b.dataset.q);
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
    <p><strong>Charting</strong> — KLineChart v9 (Apache-2.0), vendored. Drawing tools live on the
       left rail; right-click a drawing to delete it.</p>
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
    <h3>Factors</h3>
    <p>${esc(m.factor_caveat || '')}</p>
    <h3>What this is not</h3>
    <p>${esc(m.disclaimer || 'Tracker context, not trading advice.')}</p>`;
}

boot();
