'use strict';

const map = L.map('map', { zoomControl: false });
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

function asBounds(value) {
  if (!Array.isArray(value) || value.length !== 2 || !value.every(corner => Array.isArray(corner) && corner.length === 2)) return null;
  const bounds = value.map(corner => corner.map(Number));
  if (bounds.some(corner => corner.some(coordinate => !Number.isFinite(coordinate)))) return null;
  const [[south, west], [north, east]] = bounds;
  if (south > north || west > east) return null;
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

function geometryIsValid() {
  if (!state.config || !state.bounds) return false;
  const available = asBounds(state.config.availableBounds);
  const area = rectangleAreaKm2(state.bounds);
  return boundsWithin(state.bounds, available) && area > 0 && area <= Number(state.config.maxAreaKm2);
}

function canRun() {
  return geometryIsValid() && !state.running;
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
  $('selectedArea').textContent = `Approx. ${areaLabel(area)} selected`;
  if (!geometryIsValid()) {
    $('selectedArea').textContent += ' (outside the available model area or too large)';
  }
}

function updateControls() {
  $('selectArea').disabled = !state.config || state.running || state.selecting;
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
  $('legend').hidden = true;
}

function layerUrl() {
  return state.manifest.layers[state.layer];
}

function render() {
  if (!state.manifest) return;
  const manifest = state.manifest;
  if (state.overlay) map.removeLayer(state.overlay);
  state.overlay = L.imageOverlay(layerUrl(), manifest.bounds, {
    opacity: Number($('opacity').value) / 100,
    interactive: false,
  }).addTo(map);

  const stats = manifest.stats;
  $('floodedArea').textContent = `${Number(stats.floodedAreaKm2).toLocaleString()} km²`;
  $('exposedPopulation').textContent = Math.round(Number(stats.exposedPopulation)).toLocaleString();
  $('maximumDepth').textContent = `${Number(stats.maximumDepthM).toFixed(2)} m`;
  const returnedPeriod = Number(manifest.returnPeriod);
  const generatedAt = manifest.generatedAt ? new Date(manifest.generatedAt) : null;
  const generatedLabel = generatedAt && !Number.isNaN(generatedAt.getTime())
    ? ` · ${generatedAt.toLocaleDateString()}`
    : '';
  $('status').textContent = `${returnedPeriod}-year event ready${generatedLabel}`;

  const classified = state.layer === 'risk' || state.layer === 'hazard';
  $('legend').hidden = !classified;
  if (classified) {
    $('legendTitle').textContent = state.layer === 'risk' ? 'Population risk' : 'Flood hazard';
    const labels = state.layer === 'risk'
      ? ['Low', 'Moderate', 'High', 'Extreme']
      : ['Low (<0.75)', 'Moderate (0.75–1.25)', 'High (1.25–2.5)', 'Extreme (≥2.5)'];
    document.querySelectorAll('#legend b').forEach((label, index) => { label.textContent = labels[index]; });
  }
}

function setBounds(bounds, status) {
  state.bounds = asBounds(bounds);
  if (!state.bounds) throw new Error('The service returned invalid study bounds');
  setRectangle(state.bounds);
  updateSelectionDisplay();
  if (status) $('status').textContent = status;
  updateControls();
}

function applyEffectiveBounds(bounds) {
  const effective = asBounds(bounds);
  if (!effective) throw new Error('The service returned invalid effective bounds');
  setBounds(effective, `Study area snapped to model grid · approx. ${areaLabel(rectangleAreaKm2(effective))}`);
  return `snapped area ${areaLabel(rectangleAreaKm2(effective))}`;
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

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await responseError(response));
  try {
    return await response.json();
  } catch (error) {
    throw new Error('The service returned invalid JSON');
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
  setBounds(bounds, `Study area selected · approx. ${areaLabel(rectangleAreaKm2(bounds))}`);
  updateControls();
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

function prepareManifest(manifest, manifestUrl) {
  if (!manifest || typeof manifest !== 'object' || !manifest.layers || !manifest.stats || !asBounds(manifest.bounds)) {
    throw new Error('The service returned an unsupported result manifest');
  }
  const base = new URL(manifestUrl, window.location.href);
  const layers = Object.fromEntries(Object.entries(manifest.layers).map(([name, path]) => [
    name,
    new URL(path, base).toString(),
  ]));
  return { ...manifest, layers };
}

async function pollJob(jobId, snappedNotice = '') {
  while (true) {
    const job = await fetchJson(`/api/lisflood/jobs/${jobId}`, { cache: 'no-store' });
    if (job.effectiveBounds) snappedNotice = applyEffectiveBounds(job.effectiveBounds);
    if (job.status === 'failed') throw new Error(job.error || 'Simulation failed');
    if (job.status === 'completed') {
      if (!job.manifestUrl) throw new Error('Completed job has no result manifest');
      const manifest = await fetchJson(job.manifestUrl, { cache: 'no-store' });
      state.manifest = prepareManifest(manifest, job.manifestUrl);
      map.fitBounds(state.manifest.bounds, { padding: [20, 20] });
      render();
      return;
    }
    if (job.status !== 'queued' && job.status !== 'running') throw new Error('The service returned an unknown job status');
    const label = job.status === 'running' ? 'Simulation running…' : 'Simulation queued…';
    $('status').textContent = snappedNotice ? `${label} · ${snappedNotice}` : label;
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

async function runSimulation() {
  if (!canRun()) return;
  clearResult();
  state.running = true;
  updateControls();
  $('error').hidden = true;
  $('status').textContent = 'Submitting simulation…';
  try {
    const response = await fetch('/api/lisflood/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bounds: state.bounds, returnPeriod: Number(state.period) }),
    });
    if (!response.ok) throw new Error(await responseError(response));
    const job = await response.json();
    if (!job || typeof job.jobId !== 'string') throw new Error('The service returned no job identifier');
    const snappedNotice = job.effectiveBounds ? applyEffectiveBounds(job.effectiveBounds) : '';
    await pollJob(job.jobId, snappedNotice);
  } catch (error) {
    console.error(error);
    $('status').textContent = error instanceof Error ? error.message : 'Simulation failed';
    $('error').textContent = 'Simulation unavailable. Please try again later.';
    $('error').hidden = false;
  } finally {
    state.running = false;
    updateControls();
  }
}

document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', () => {
  document.querySelector('[data-period].active')?.classList.remove('active');
  button.classList.add('active');
  state.period = button.dataset.period;
  clearResult();
  $('status').textContent = `Ready to run ${state.period}-year event`;
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
$('runSimulation').addEventListener('click', runSimulation);

updateControls();
map.setView([32.12, 118.95], 11);

fetchJson('/api/lisflood/config', { cache: 'no-store' })
  .then(config => {
    const available = asBounds(config.availableBounds);
    const defaultBounds = asBounds(config.defaultBounds);
    if (!available || !defaultBounds || !Number.isFinite(Number(config.maxAreaKm2))) {
      throw new Error('The service returned invalid study-area configuration');
    }
    state.config = config;
    state.bounds = defaultBounds;
    state.corners = [];
    setRectangle(state.bounds);
    updateSelectionDisplay();
    document.querySelector('[data-period="20"]')?.classList.add('active');
    map.fitBounds(config.defaultBounds || config.availableBounds, { padding: [20, 20] });
    $('status').textContent = `Default study area loaded · approx. ${areaLabel(rectangleAreaKm2(state.bounds))}`;
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
