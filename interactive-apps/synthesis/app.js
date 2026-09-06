(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SynthesisDemo = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function number(value, name) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) throw new Error(`${name} must be finite`);
    return parsed;
  }

  function inputs(params) {
    const n = number(params.n, 'Observations');
    const seed = number(params.seed, 'Seed');
    const noise = number(params.noise, 'Noise');
    if (!Number.isInteger(n) || n < 100 || n > 5000) throw new Error('Observations must be an integer from 100 to 5000');
    if (!Number.isSafeInteger(seed)) throw new Error('Seed must be an integer');
    if (noise < 0) throw new Error('Noise must be non-negative');
    return {n, seed, noise};
  }

  function rng(seed) {
    let state = seed >>> 0;
    return function () {
      state += 0x6d2b79f5;
      let value = state;
      value = Math.imul(value ^ value >>> 15, value | 1);
      value ^= value + Math.imul(value ^ value >>> 7, value | 61);
      return ((value ^ value >>> 14) >>> 0) / 4294967296;
    };
  }

  function normal(random) {
    const u = Math.max(random(), Number.EPSILON);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * random());
  }

  function addNoise(values, scale, random) {
    return scale ? values.map(value => value + scale * normal(random)) : values;
  }

  function ar1(params, common) {
    const warmup = 500;
    const random = rng(common.seed);
    const values = Array(common.n + warmup).fill(0);
    // Match synthesis::data.gen.ar1: its 500-point warm-up is independently initialized.
    for (let index = 0; index < warmup; index += 1) values[index] = normal(random);
    for (let index = warmup; index < values.length; index += 1) values[index] = 0.9 * values[index - 1] + 0.866 * normal(random);
    const response = addNoise(values.slice(warmup), common.noise, random);
    const columns = {index: Array.from({length: common.n}, (_, index) => index + 1), x: response};
    for (let lag = 1; lag <= 9; lag += 1) columns[`lag${lag}`] = values.slice(warmup - lag, warmup - lag + common.n);
    return {model: 'ar1', columns};
  }

  function logistic(params, common) {
    const r = number(params.r, 'r');
    const start = number(params.start, 'Start');
    if (r < 0 || r > 4) throw new Error('r must be between 0 and 4');
    if (start < 0 || start > 1) throw new Error('Start must be between 0 and 1');
    const warmup = 500;
    const values = Array(common.n + warmup).fill(0);
    values[0] = start;
    for (let index = 1; index < values.length; index += 1) values[index] = r * values[index - 1] * (1 - values[index - 1]);
    const x = addNoise(values.slice(warmup), common.noise, rng(common.seed));
    return {model: 'logistic', columns: {index: Array.from({length: common.n}, (_, index) => index + 1), x}};
  }

  function lorenz(params, common) {
    const sigma = number(params.sigma, 'sigma');
    const beta = number(params.beta, 'beta');
    const rho = number(params.rho, 'rho');
    if (sigma <= 0 || beta <= 0 || rho <= 0) throw new Error('Lorenz parameters must be positive');
    const time = Array.from({length: common.n}, (_, index) => 50 * index / (common.n - 1));
    const x = Array(common.n); const y = Array(common.n); const z = Array(common.n);
    [x[0], y[0], z[0]] = [-13, -14, 47];
    const derivative = ([a, b, c]) => [sigma * (b - a), rho * a - b - a * c, a * b - beta * c];
    const plus = (state, step, scale) => state.map((value, index) => value + scale * step[index]);
    for (let index = 1; index < common.n; index += 1) {
      const interval = time[index] - time[index - 1];
      const substeps = Math.ceil(interval / 0.01);
      const h = interval / substeps;
      let state = [x[index - 1], y[index - 1], z[index - 1]];
      for (let step = 0; step < substeps; step += 1) {
        const k1 = derivative(state);
        const k2 = derivative(plus(state, k1, h / 2));
        const k3 = derivative(plus(state, k2, h / 2));
        const k4 = derivative(plus(state, k3, h));
        state = state.map((value, axis) => value + h * (k1[axis] + 2 * k2[axis] + 2 * k3[axis] + k4[axis]) / 6);
        if (!state.every(Number.isFinite)) throw new Error('Lorenz system became unstable; adjust the parameters');
      }
      [x[index], y[index], z[index]] = state;
    }
    const random = rng(common.seed);
    return {model: 'lorenz', columns: {time, x: addNoise(x, common.noise, random), y: addNoise(y, common.noise, random), z: addNoise(z, common.noise, random)}};
  }

  function generate(model, params) {
    const common = inputs(params);
    if (model === 'ar1') return ar1(params, common);
    if (model === 'logistic') return logistic(params, common);
    if (model === 'lorenz') return lorenz(params, common);
    throw new Error('Unknown model');
  }

  function summary(values) {
    if (!Array.isArray(values) || !values.length || !values.every(Number.isFinite)) throw new Error('Invalid series');
    const mean = values.reduce((total, value) => total + value, 0) / values.length;
    return {
      mean,
      standardDeviation: Math.sqrt(values.reduce((total, value) => total + (value - mean) ** 2, 0) / Math.max(1, values.length - 1)),
      minimum: Math.min(...values),
      maximum: Math.max(...values),
    };
  }

  function toCsv(result) {
    const names = Object.keys(result.columns);
    const rows = [names.join(',')];
    for (let index = 0; index < result.columns[names[0]].length; index += 1) rows.push(names.map(name => result.columns[name][index]).join(','));
    return `${rows.join('\n')}\n`;
  }

  function parameterMarkup(model) {
    if (model === 'logistic') return '<label for="r">Growth rate r<input id="r" type="number" min="0" max="4" step="0.01" value="4"></label><label for="start">Starting value<input id="start" type="number" min="0" max="1" step="0.01" value="0.2"></label>';
    if (model === 'lorenz') return '<label for="sigma">Sigma<input id="sigma" type="number" min="0.01" step="0.1" value="10"></label><label for="beta">Beta<input id="beta" type="number" min="0.01" step="0.01" value="2.6666666667"></label><label for="rho">Rho<input id="rho" type="number" min="0.01" step="0.1" value="28"></label>';
    return '<p class="intro">Package coefficients: 0.9 persistence, 0.866 innovation scale and nine lagged predictors.</p>';
  }

  function plot(result) {
    const xAxis = result.columns.index || result.columns.time;
    const series = result.model === 'lorenz' ? ['x', 'y', 'z'] : ['x'];
    Plotly.react('timeSeries', series.map((name, index) => ({x: xAxis, y: result.columns[name], name, type: 'scatter', mode: 'lines', line: {width: 1.5, color: ['#0b5ea7', '#c4922e', '#147d64'][index]}})), {
      margin: {l: 50, r: 15, t: 15, b: 45}, xaxis: {title: result.columns.time ? 'Time' : 'Index'}, yaxis: {title: 'Value'}, paper_bgcolor: '#fff', plot_bgcolor: '#fff', legend: {orientation: 'h', y: 1.08},
    }, {responsive: true, displaylogo: false});
    let phase;
    if (result.model === 'ar1') phase = {x: result.columns.lag1, y: result.columns.x, title: 'Lag relationship', xTitle: 'x[t-1]', yTitle: 'x[t]', mode: 'markers'};
    else if (result.model === 'logistic') phase = {x: result.columns.x.slice(0, -1), y: result.columns.x.slice(1), title: 'Logistic return map', xTitle: 'x[t]', yTitle: 'x[t+1]', mode: 'markers'};
    else phase = {x: result.columns.x, y: result.columns.y, title: 'Lorenz attractor', xTitle: 'x', yTitle: 'y', mode: 'lines'};
    document.getElementById('phaseTitle').textContent = phase.title;
    Plotly.react('phasePlot', [{x: phase.x, y: phase.y, type: 'scatter', mode: phase.mode, marker: {size: 4, color: '#0b5ea7'}, line: {width: 1, color: '#0b5ea7'}}], {
      margin: {l: 50, r: 15, t: 15, b: 45}, xaxis: {title: phase.xTitle}, yaxis: {title: phase.yTitle}, paper_bgcolor: '#fff', plot_bgcolor: '#fff',
    }, {responsive: true, displaylogo: false});
  }

  function renderMetrics(values) {
    const labels = [['Mean', values.mean], ['Standard deviation', values.standardDeviation], ['Minimum', values.minimum], ['Maximum', values.maximum]];
    document.getElementById('metrics').replaceChildren(...labels.map(([label, value]) => {
      const tile = document.createElement('div'); tile.className = 'metric';
      const name = document.createElement('span'); name.textContent = label;
      const number = document.createElement('strong'); number.textContent = value.toLocaleString(undefined, {maximumFractionDigits: 3});
      tile.append(name, number); return tile;
    }));
  }

  function browserInit() {
    const model = document.getElementById('model');
    const parameters = document.getElementById('modelParameters');
    const status = document.getElementById('status');
    const downloadButton = document.getElementById('download');
    let current = null;
    const showParameters = () => { parameters.innerHTML = parameterMarkup(model.value); };
    const run = () => {
      try {
        const params = {n: document.getElementById('samples').value, seed: document.getElementById('seed').value, noise: document.getElementById('noise').value};
        parameters.querySelectorAll('input').forEach(input => { params[input.id] = input.value; });
        current = generate(model.value, params);
        plot(current); renderMetrics(summary(current.columns.x));
        downloadButton.disabled = false;
        status.textContent = `Generated ${current.columns.x.length.toLocaleString()} observations locally.`;
      } catch (error) { status.textContent = error.message; }
    };
    model.addEventListener('change', () => { showParameters(); run(); });
    document.getElementById('generate').addEventListener('click', run);
    downloadButton.addEventListener('click', () => {
      if (!current) return;
      const url = URL.createObjectURL(new Blob([toCsv(current)], {type: 'text/csv;charset=utf-8'}));
      const link = document.createElement('a'); link.href = url; link.download = `synthesis-${current.model}.csv`; link.click(); URL.revokeObjectURL(url);
    });
    showParameters(); run();
  }

  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', browserInit);
  return {generate, summary, toCsv};
}));
