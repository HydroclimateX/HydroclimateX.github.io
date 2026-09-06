(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WqmDemo = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const COLUMN_NAMES = ['date', 'observed', 'raw', 'corrected', 'r1', 'r2', 'r3', 'r4', 'r5'];

  function validateDemo(data) {
    if (!data || data.schemaVersion !== 1 || !Array.isArray(data.stations) || data.stations.length !== 2) {
      throw new Error('Invalid WQM demo data');
    }
    data.stations.forEach(station => {
      if (!station || typeof station.id !== 'string' || !station.validation) throw new Error('Invalid WQM station');
      const lengths = COLUMN_NAMES.map(name => {
        const values = station.validation[name];
        if (!Array.isArray(values)) throw new Error(`Missing WQM column: ${name}`);
        return values.length;
      });
      if (!lengths[0] || !lengths.every(length => length === lengths[0])) throw new Error('Mismatched WQM columns');
      COLUMN_NAMES.slice(1).forEach(name => {
        if (!station.validation[name].every(value => typeof value === 'number' && Number.isFinite(value) && value >= 0)) {
          throw new Error(`Invalid WQM values: ${name}`);
        }
      });
    });
    return data;
  }

  function score(observed, forecast, threshold) {
    if (!Array.isArray(observed) || observed.length !== forecast.length || !observed.length) throw new Error('Invalid metric data');
    const errors = forecast.map((value, index) => value - observed[index]);
    return {
      rmse: Math.sqrt(errors.reduce((total, value) => total + value * value, 0) / errors.length),
      bias: errors.reduce((total, value) => total + value, 0) / errors.length,
      wetFrequency: forecast.filter(value => value >= threshold).length / forecast.length,
    };
  }

  function metrics(observed, raw, corrected, threshold = 0.1) {
    return {raw: score(observed, raw, threshold), corrected: score(observed, corrected, threshold)};
  }

  function csvCell(value) {
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(station) {
    validateDemo({schemaVersion: 1, stations: [station, station]});
    const columns = station.validation;
    const rows = [COLUMN_NAMES.join(',')];
    columns.date.forEach((_, index) => rows.push(COLUMN_NAMES.map(name => csvCell(columns[name][index])).join(',')));
    return `${rows.join('\n')}\n`;
  }

  function format(value, digits = 2) {
    return Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  }

  function renderMetrics(station) {
    const values = metrics(station.validation.observed, station.validation.raw, station.validation.corrected);
    const observedWet = score(station.validation.observed, station.validation.observed, 0.1).wetFrequency;
    const items = [
      ['Raw RMSE', values.raw.rmse, `WQM ${format(values.corrected.rmse)}`],
      ['Raw mean bias', values.raw.bias, `WQM ${format(values.corrected.bias)}`],
      ['Observed wet days', observedWet * 100, `Raw ${format(values.raw.wetFrequency * 100)}%`],
      ['WQM wet days', values.corrected.wetFrequency * 100, `${format(station.validation.date.length, 0)} validation points`],
    ];
    document.getElementById('metrics').replaceChildren(...items.map(([label, value, detail]) => {
      const tile = document.createElement('div');
      tile.className = 'metric';
      const name = document.createElement('span');
      name.textContent = label;
      const number = document.createElement('strong');
      number.textContent = `${format(value)}${label.includes('days') ? '%' : ''}`;
      const small = document.createElement('small');
      small.textContent = detail;
      tile.append(name, number, small);
      return tile;
    }));
  }

  function renderChart(station, member) {
    const data = station.validation;
    const traces = [
      ['Observed', data.observed, '#0f172a', 2],
      ['Raw forecast', data.raw, '#dc2626', 1],
      ['WQM corrected', data.corrected, '#0b5ea7', 2],
      [`Ensemble ${member.slice(1)}`, data[member], '#c4922e', 1],
    ].map(([name, y, color, width]) => ({x: data.date, y, name, type: 'scatter', mode: 'lines', line: {color, width}}));
    Plotly.react('timeSeries', traces, {
      margin: {l: 55, r: 18, t: 15, b: 50},
      paper_bgcolor: '#fff', plot_bgcolor: '#fff', hovermode: 'x unified',
      xaxis: {title: 'Date', rangeslider: {visible: true, thickness: .08}},
      yaxis: {title: 'Precipitation'},
      legend: {orientation: 'h', y: 1.08},
    }, {responsive: true, displaylogo: false});
  }

  function download(station) {
    const url = URL.createObjectURL(new Blob([toCsv(station)], {type: 'text/csv;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = `wqm-demo-${station.id}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function init() {
    const status = document.getElementById('status');
    try {
      const response = await fetch('demo.json', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = validateDemo(await response.json());
      const stationSelect = document.getElementById('station');
      const memberSelect = document.getElementById('member');
      const downloadButton = document.getElementById('download');
      data.stations.forEach(station => stationSelect.add(new Option(station.id, station.id)));
      const current = () => data.stations.find(station => station.id === stationSelect.value);
      const render = () => { renderMetrics(current()); renderChart(current(), memberSelect.value); };
      stationSelect.addEventListener('change', render);
      memberSelect.addEventListener('change', () => renderChart(current(), memberSelect.value));
      downloadButton.addEventListener('click', () => download(current()));
      stationSelect.disabled = memberSelect.disabled = downloadButton.disabled = false;
      render();
      status.textContent = `Ready · WQM ${data.package.version} · QDM reference run`;
    } catch (error) {
      status.textContent = 'Reference results are unavailable. Please reload the page.';
    }
  }

  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', init);
  return {validateDemo, metrics, toCsv};
}));
