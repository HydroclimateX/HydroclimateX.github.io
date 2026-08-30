'use strict';

const map = L.map('map', { zoomControl: false });
const selectionPane = map.createPane('selectionPane');
selectionPane.style.zIndex = '650';
selectionPane.style.pointerEvents = 'none';
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
}).addTo(map);

const state = {
  config: null,
  bounds: null,
  corners: [],
  rectangle: null,
  period: '20',
  layer: 'risk',
  overlay: null,
  manifest: null,
  selecting: false,
  running: false,
};
const $ = id => document.getElementById(id);
const EARTH_RADIUS_KM = 6371.0088;
const SUPPORTED_PERIODS = Object.freeze([5, 10, 20, 50, 100]);
const LAYER_NAMES = Object.freeze(['dem', 'population', 'depth', 'velocity', 'hazard', 'risk']);
const STAT_NAMES = Object.freeze(['floodedAreaKm2', 'exposedPopulation', 'maximumDepthM', 'maximumVelocityMs']);
const POLL_INTERVAL_MS = 2000;
const POLL_DEADLINE_MS = 24 * 60 * 60 * 1000;
const MAX_TRANSIENT_ERRORS = 3;
const REQUEST_TIMEOUT_MS = 30000;

function asBounds(value) {
  if (!Array.isArray(value) || value.length !== 2 || !value.every(corner => Array.isArray(corner) && corner.length === 2)) {
    throw new Error('Invalid bounds');
  }
  const bounds = value.map(corner => corner.slice());
  if (bounds.some(corner => corner.some(coordinate => typeof coordinate !== 'number' || !Number.isFinite(coordinate)))) {
    throw new Error('Invalid bounds');
  }
  const [[south, west], [north, east]] = bounds;
  if (south < -90 || north > 90 || west < -180 || east > 180 || south >= north || west >= east) {
    throw new Error('Invalid bounds');
  }
  return bounds;
}

function rectangleAreaKm2(bounds) {
  const [[south, west], [north, east]] = bounds;
  const latitudeBand = Math.abs(Math.sin(north * Math.PI / 180) - Math.sin(south * Math.PI / 180));
  const longitudeBand = Math.abs(east - west) * Math.PI / 180;
  return EARTH_RADIUS_KM ** 2 * latitudeBand * longitudeBand;
}

function areaLabel(area) {
  return `${area.toLocaleString(undefined, { maximumFractionDigits: 2 })} km²`;
}

function boundsWithin(bounds, available) {
  if (!bounds || !available) return false;
  return bounds[0][0] >= available[0][0]
    && bounds[0][1] >= available[0][1]
    && bounds[1][0] <= available[1][0]
    && bounds[1][1] <= available[1][1];
}

function boundsMatch(actual, expected, tolerance = 1e-6) {
  if (!actual || !expected) return false;
  return actual.every((corner, cornerIndex) => corner.every((coordinate, coordinateIndex) => (
    Math.abs(coordinate - expected[cornerIndex][coordinateIndex]) <= tolerance
  )));
}

function geometryIsValid() {
  if (!state.config || !state.bounds) return false;
  const area = rectangleAreaKm2(state.bounds);
  return boundsWithin(state.bounds, state.config.availableBounds) && area > 0 && area <= state.config.maxAreaKm2;
}

function canRun() {
  return geometryIsValid() && !state.running;
}

function normalizeConfig(config) {
  try {
    if (!config || typeof config !== 'object' || config.schemaVersion !== 1) throw new Error('schema');
    const availableBounds = asBounds(config.availableBounds);
    const defaultBounds = asBounds(config.defaultBounds);
    if (!boundsWithin(defaultBounds, availableBounds)) throw new Error('default bounds');
    if (typeof config.maxAreaKm2 !== 'number' || !Number.isFinite(config.maxAreaKm2) || config.maxAreaKm2 <= 0) throw new Error('area');
    if (typeof config.gridSizeM !== 'number' || !Number.isFinite(config.gridSizeM) || config.gridSizeM <= 0) throw new Error('grid');
    if (!Array.isArray(config.returnPeriods)
      || config.returnPeriods.length !== SUPPORTED_PERIODS.length
      || new Set(config.returnPeriods).size !== SUPPORTED_PERIODS.length
      || config.returnPeriods.some(period => typeof period !== 'number' || !Number.isInteger(period) || !SUPPORTED_PERIODS.includes(period))) throw new Error('periods');
    if (typeof config.modelVersion !== 'string'
      || config.modelVersion.length === 0
      || config.modelVersion.length > 128
      || config.modelVersion.trim() !== config.modelVersion
      || !/^[\x20-\x7e]+$/.test(config.modelVersion)) throw new Error('model');
    return {
      ...config,
      availableBounds,
      defaultBounds,
      maxAreaKm2: config.maxAreaKm2,
      gridSizeM: config.gridSizeM,
      returnPeriods: SUPPORTED_PERIODS.slice(),
      modelVersion: config.modelVersion,
    };
  } catch (error) {
    throw new Error('Invalid LISFLOOD configuration');
  }
}

function sameOriginPath(value, pattern) {
  if (typeof value !== 'string' || !value.startsWith('/')) return false;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin
      && url.pathname === value
      && !url.search
      && !url.hash
      && pattern.test(url.pathname);
  } catch (error) {
    return false;
  }
}

function isJobId(value) {
  return typeof value === 'string' && /^[0-9a-f]{20}$/.test(value);
}

function isStatusUrl(value, jobId) {
  return isJobId(jobId) && sameOriginPath(value, new RegExp(`^/api/lisflood/jobs/${jobId}$`));
}

function isManifestUrl(value, jobId) {
  return isJobId(jobId) && sameOriginPath(value, new RegExp(`^/results/${jobId}/manifest\\.json$`));
}

function setRectangle(bounds) {
  if (state.rectangle) {
    map.removeLayer(state.rectangle);
    state.rectangle = null;
  }
  if (bounds) {
    state.rectangle = L.rectangle(bounds, {
      color: '#126a78',
      weight: 2,
      fillOpacity: 0.08,
      interactive: false,
      pane: 'selectionPane',
    }).addTo(map);
  }
}

function updateSelectionDisplay() {
  if (!state.bounds) {
    $('selectedArea').textContent = state.selecting
      ? 'Select the first corner on the map.'
      : 'No study area selected.';
    return;
  }
  const area = rectangleAreaKm2(state.bounds);
  if (area <= 0) $('selectedArea').textContent = 'Select two different corners for a positive-area rectangle.';
  else if (area > state.config.maxAreaKm2) $('selectedArea').textContent = `Approx. ${areaLabel(area)} selected (maximum ${state.config.maxAreaKm2.toLocaleString()} km²)`;
  else if (!boundsWithin(state.bounds, state.config.availableBounds)) $('selectedArea').textContent = `Approx. ${areaLabel(area)} selected (must lie within the available model area)`;
  else $('selectedArea').textContent = `Approx. ${areaLabel(area)} selected`;
}

function updateControls() {
  $('selectArea').disabled = !state.config || state.running || state.selecting;
  $('resetArea').disabled = !state.config || state.running;
  $('runSimulation').disabled = !canRun();
  document.querySelectorAll('[data-period], input[name="layer"], #opacity').forEach(control => {
    control.disabled = !state.config || state.running;
  });
}

function clearResult() {
  if (state.overlay) {
    map.removeLayer(state.overlay);
    state.overlay = null;
  }
  state.manifest = null;
  $('floodedArea').textContent = '—';
  $('exposedPopulation').textContent = '—';
  $('maximumDepth').textContent = '—';
  $('maximumVelocity').textContent = '—';
  $('legend').hidden = true;
  $('resultMeta').textContent = '';
  $('results').hidden = true;
}

function render() {
  if (!state.manifest) return;
  const manifest = state.manifest;
  if (state.overlay) map.removeLayer(state.overlay);
  state.overlay = L.imageOverlay(manifest.layers[state.layer], manifest.bounds, {
    opacity: Number($('opacity').value) / 100,
    interactive: false,
  }).addTo(map);

  const stats = manifest.stats;
  $('floodedArea').textContent = `${stats.floodedAreaKm2.toLocaleString()} km²`;
  $('exposedPopulation').textContent = Math.round(stats.exposedPopulation).toLocaleString();
  $('maximumDepth').textContent = `${stats.maximumDepthM.toFixed(2)} m`;
  $('maximumVelocity').textContent = `${stats.maximumVelocityMs.toFixed(2)} m/s`;
  const returnedPeriod = manifest.returnPeriod;
  const generatedAt = manifest.generatedAt ? new Date(manifest.generatedAt) : null;
  const generatedLabel = generatedAt && !Number.isNaN(generatedAt.getTime())
    ? ` · ${generatedAt.toLocaleDateString()}`
    : '';
  $('resultMeta').textContent = `${returnedPeriod}-year · ${state.config.modelVersion}${generatedLabel}`;
  $('results').hidden = false;
  renderLegend(manifest, state.layer);
}

function formatLegendValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number >= 100 ? number.toLocaleString() : String(parseFloat(number.toFixed(2)));
}

function renderLegend(manifest, layer) {
  const legend = $('legend');
  const meta = manifest.legends && manifest.legends[layer];
  if (!meta || !Array.isArray(meta.colors)) {
    legend.hidden = true;
    return;
  }
  $('legendTitle').textContent = meta.title;
  const body = $('legendBody');
  body.replaceChildren();
  if (meta.type === 'classes') {
    (meta.labels || []).forEach((label, index) => {
      const span = document.createElement('span');
      const swatch = document.createElement('i');
      swatch.style.background = meta.colors[index].slice(0, 7); // solid #rrggbb, like the original legend
      const text = document.createElement('b');
      text.textContent = label;
      span.append(swatch, text);
      body.append(span);
    });
  } else {
    const bar = document.createElement('div');
    bar.className = 'colorbar';
    bar.style.background = `linear-gradient(90deg, ${meta.colors.join(', ')})`;
    const range = document.createElement('div');
    range.className = 'colorbar-range';
    range.textContent = `${formatLegendValue(meta.min)} – ${formatLegendValue(meta.max)} ${meta.unit || ''}`;
    body.append(bar, range);
  }
  legend.hidden = false;
}

function setBounds(bounds, status) {
  state.bounds = asBounds(bounds);
  setRectangle(state.bounds);
  updateSelectionDisplay();
  if (status) $('status').textContent = status;
  updateControls();
}

function applyEffectiveBounds(bounds) {
  try {
    const effective = asBounds(bounds);
    setBounds(effective, `Study area snapped to model grid · approx. ${areaLabel(rectangleAreaKm2(effective))}`);
    return effective;
  } catch (error) {
    throw new Error('Invalid simulation result');
  }
}

async function responseError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    // The status text below is safer than exposing a non-JSON response body.
  }
  return payload && typeof payload.error === 'string' ? payload.error : `Request failed (HTTP ${response.status})`;
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const timedOut = () => {
    const timeoutError = new Error('Request timed out');
    timeoutError.transient = true;
    return timeoutError;
  };
  try {
    let response;
    try {
      response = await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
      if (error.name === 'AbortError' || controller.signal.aborted) {
        throw timedOut();
      }
      const networkError = new Error('Network request failed');
      networkError.transient = true;
      throw networkError;
    }
    if (!response.ok) {
      const message = await responseError(response);
      if (controller.signal.aborted) throw timedOut();
      const requestError = new Error(message);
      requestError.transient = response.status >= 500;
      throw requestError;
    }
    try {
      const payload = await response.json();
      if (controller.signal.aborted) throw timedOut();
      return payload;
    } catch (error) {
      if (error.name === 'AbortError' || controller.signal.aborted) throw timedOut();
      throw new Error('The service returned invalid JSON');
    }
  } finally {
    clearTimeout(timeoutId);
  }
}

function firstCorner(event) {
  if (!state.selecting) return;
  state.corners = [{ lat: event.latlng.lat, lng: event.latlng.lng }];
  $('selectedArea').textContent = 'First corner selected. Select the opposite corner.';
  $('status').textContent = 'Select the opposite corner on the map.';
  map.once('click', secondCorner);
}

function secondCorner(event) {
  if (!state.selecting || state.corners.length !== 1) return;
  const first = state.corners[0];
  const second = event.latlng;
  const bounds = [
    [Math.min(first.lat, second.lat), Math.min(first.lng, second.lng)],
    [Math.max(first.lat, second.lat), Math.max(first.lng, second.lng)],
  ];
  state.corners = [];
  state.selecting = false;
  try {
    setBounds(bounds, `Study area selected · approx. ${areaLabel(rectangleAreaKm2(bounds))}`);
  } catch (error) {
    state.bounds = null;
    setRectangle(null);
    $('selectedArea').textContent = 'Select two different corners for a positive-area rectangle.';
    $('status').textContent = 'Study area must have positive area.';
    updateControls();
  }
}

function startSelection() {
  if (!state.config || state.running) return;
  clearResult();
  setRectangle(null);
  state.bounds = null;
  state.corners = [];
  state.selecting = true;
  $('selectedArea').textContent = 'Select the first corner on the map.';
  $('status').textContent = 'Select the first corner on the map.';
  updateControls();
  map.once('click', firstCorner);
}

function resetArea() {
  if (!state.config || state.running) return;
  map.off('click', firstCorner);
  map.off('click', secondCorner);
  state.corners = [];
  state.selecting = false;
  clearResult();
  setBounds(state.config.defaultBounds, 'Ready');
  map.fitBounds(state.config.defaultBounds, { padding: [20, 20] });
  $('error').hidden = true;
  updateControls();
}

function normalizeStats(stats) {
  if (!stats || typeof stats !== 'object' || Array.isArray(stats)
    || Object.keys(stats).sort().join(',') !== STAT_NAMES.slice().sort().join(',')) throw new Error('stats');
  return Object.fromEntries(STAT_NAMES.map(name => {
    const value = stats[name];
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('stat');
    return [name, value];
  }));
}

function prepareManifest(manifest, jobId, expectedPeriod, expectedBounds, availableBounds) {
  try {
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest) || manifest.schemaVersion !== 1) throw new Error('schema');
    const bounds = asBounds(manifest.bounds);
    const returnPeriod = manifest.returnPeriod;
    if (typeof returnPeriod !== 'number' || !Number.isInteger(returnPeriod)
      || !SUPPORTED_PERIODS.includes(returnPeriod) || returnPeriod !== expectedPeriod) throw new Error('period');
    if (!boundsMatch(bounds, expectedBounds) || !boundsWithin(bounds, availableBounds)) throw new Error('bounds');
    if (!manifest.layers || typeof manifest.layers !== 'object' || Array.isArray(manifest.layers)
      || Object.keys(manifest.layers).sort().join(',') !== LAYER_NAMES.slice().sort().join(',')) throw new Error('layers');
    const layers = Object.fromEntries(LAYER_NAMES.map(name => {
      const path = manifest.layers[name];
      if (!sameOriginPath(path, new RegExp(`^/results/${jobId}/${name}\\.png$`))) throw new Error('layer');
      return [name, path];
    }));
    const populationBreaks = manifest.populationBreaks;
    if (!Array.isArray(populationBreaks) || populationBreaks.length === 0
      || populationBreaks.some(value => typeof value !== 'number' || !Number.isFinite(value))) throw new Error('breaks');
    return {
      ...manifest,
      bounds,
      returnPeriod,
      layers,
      stats: normalizeStats(manifest.stats),
      populationBreaks: populationBreaks.slice(),
    };
  } catch (error) {
    throw new Error('Invalid simulation result');
  }
}

async function pollJob(statusUrl, jobId, expectedPeriod, expectedBounds, availableBounds, snappedNotice = '', cached = false) {
  const deadline = Date.now() + POLL_DEADLINE_MS;
  let transientErrors = 0;
  let waitMs = POLL_INTERVAL_MS;
  while (Date.now() < deadline) {
    let job;
    try {
      job = await fetchJson(statusUrl, { cache: 'no-store' });
      transientErrors = 0;
      waitMs = POLL_INTERVAL_MS;
    } catch (error) {
      if (!error.transient) throw error;
      transientErrors += 1;
      if (transientErrors > MAX_TRANSIENT_ERRORS) throw new Error('Simulation status unavailable');
      $('status').textContent = 'Waiting for simulation status…';
      const remaining = deadline - Date.now();
      if (remaining > 0) await new Promise(resolve => setTimeout(resolve, Math.min(waitMs, remaining)));
      waitMs = Math.min(POLL_INTERVAL_MS * 2 ** transientErrors, 10000);
      continue;
    }
    if (!job || typeof job !== 'object') throw new Error('Invalid simulation status');
    if (job.status === 'failed') throw new Error('Simulation failed');
    if (!isStatusUrl(job.statusUrl, jobId)) throw new Error('Invalid simulation status');
    if (job.effectiveBounds) {
      let statusBounds;
      try {
        statusBounds = asBounds(job.effectiveBounds);
      } catch (error) {
        throw new Error('Invalid simulation result');
      }
      if (!boundsMatch(statusBounds, expectedBounds)) throw new Error('Invalid simulation result');
      applyEffectiveBounds(statusBounds);
      snappedNotice = `snapped area ${areaLabel(rectangleAreaKm2(statusBounds))}`;
    }
    if (job.status === 'completed') {
      if (!isManifestUrl(job.manifestUrl, jobId)) throw new Error('Invalid simulation result');
      $('status').textContent = 'Preparing map…';
      let manifest;
      try {
        manifest = await fetchJson(job.manifestUrl, { cache: 'no-store' });
      } catch (error) {
        if (error instanceof Error && error.message === 'Request timed out') throw error;
        throw new Error('Invalid simulation result');
      }
      state.manifest = prepareManifest(manifest, jobId, expectedPeriod, expectedBounds, availableBounds);
      map.fitBounds(state.manifest.bounds, { padding: [20, 20] });
      render();
      $('status').textContent = cached ? 'Cached result loaded' : 'Result ready';
      return;
    }
    if (job.status !== 'queued' && job.status !== 'running') throw new Error('The service returned an unknown job status');
    const label = job.status === 'running' ? 'Simulation running…' : 'Simulation queued…';
    $('status').textContent = snappedNotice ? `${label} · ${snappedNotice}` : label;
    const remaining = deadline - Date.now();
    if (remaining > 0) await new Promise(resolve => setTimeout(resolve, Math.min(POLL_INTERVAL_MS, remaining)));
  }
  throw new Error('Simulation timed out');
}

async function runSimulation() {
  if (!geometryIsValid()) return;
  if (state.running) return;
  let jobId = null;
  clearResult();
  state.running = true;
  updateControls();
  $('error').hidden = true;
  $('status').textContent = 'Simulation queued…';
  try {
    const requestedPeriod = Number(state.period);
    const availableBounds = state.config.availableBounds;
    const job = await fetchJson('/api/lisflood/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bounds: state.bounds, returnPeriod: Number(state.period) }),
    });
    if (!job || typeof job !== 'object' || !isJobId(job.jobId)) {
      throw new Error('Invalid simulation job');
    }
    jobId = job.jobId;
    if (!isStatusUrl(job.statusUrl, jobId)) throw new Error('Invalid simulation job');
    if (job.status === 'failed') throw new Error('Simulation failed');
    if (!job.effectiveBounds) throw new Error('Invalid simulation result');
    const effectiveBounds = applyEffectiveBounds(job.effectiveBounds);
    const snappedNotice = `snapped area ${areaLabel(rectangleAreaKm2(effectiveBounds))}`;
    const cached = job.status === 'completed';
    await pollJob(job.statusUrl, job.jobId, requestedPeriod, effectiveBounds, availableBounds, snappedNotice, cached);
  } catch (error) {
    console.error(error);
    $('status').textContent = 'Simulation failed';
    $('error').textContent = jobId
      ? `Simulation failed. Retry or contact the administrator with job ${jobId}.`
      : 'Simulation failed. Please retry or contact the administrator.';
    $('error').hidden = false;
  } finally {
    state.running = false;
    updateControls();
  }
}

function updatePeriodButtons() {
  document.querySelectorAll('[data-period]').forEach(button => {
    const active = button.dataset.period === state.period;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', () => {
  state.period = button.dataset.period;
  updatePeriodButtons();
  clearResult();
  $('status').textContent = 'Ready';
  updateControls();
}));

document.querySelectorAll('input[name="layer"]').forEach(input => input.addEventListener('change', () => {
  state.layer = input.value;
  if (state.manifest) render();
}));

$('opacity').addEventListener('input', () => {
  if (state.manifest) render();
});

$('selectArea').addEventListener('click', startSelection);
$('resetArea').addEventListener('click', resetArea);
$('runSimulation').addEventListener('click', runSimulation);

const aboutModal = $('aboutModal');
$('aboutLayers').addEventListener('click', () => aboutModal.showModal());
$('closeAbout').addEventListener('click', () => aboutModal.close());
aboutModal.addEventListener('click', (event) => {
  if (event.target === aboutModal) aboutModal.close();
});

updateControls();
map.setView([32.12, 118.95], 11);

fetchJson('/api/lisflood/config', { cache: 'no-store' })
  .then(config => {
    state.config = normalizeConfig(config);
    state.bounds = state.config.defaultBounds;
    state.corners = [];
    setRectangle(state.bounds);
    updateSelectionDisplay();
    updatePeriodButtons();
    map.fitBounds(state.config.defaultBounds, { padding: [20, 20] });
    $('status').textContent = 'Ready';
    $('error').hidden = true;
    updateControls();
  })
  .catch(error => {
    console.error(error);
    $('status').textContent = 'Study area unavailable';
    $('error').textContent = 'Study area unavailable. Please try again later.';
    $('error').hidden = false;
    updateControls();
  });
