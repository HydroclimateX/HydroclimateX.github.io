'use strict';

const map = L.map('map', { zoomControl: false });
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
}).addTo(map);

const state = { manifest: null, period: '20', layer: 'risk', overlay: null };
const $ = id => document.getElementById(id);

function layerUrl() {
  if (state.layer === 'dem' || state.layer === 'population') return state.manifest.baseLayers[state.layer];
  return state.manifest.scenarios[state.period].layers[state.layer];
}

function render() {
  if (!state.manifest) return;
  if (state.overlay) map.removeLayer(state.overlay);
  state.overlay = L.imageOverlay(layerUrl(), state.manifest.bounds, {
    opacity: Number($('opacity').value) / 100,
    interactive: false,
  }).addTo(map);
  const stats = state.manifest.scenarios[state.period].stats;
  $('floodedArea').textContent = `${Number(stats.floodedAreaKm2).toLocaleString()} km²`;
  $('exposedPopulation').textContent = Math.round(stats.exposedPopulation).toLocaleString();
  $('maximumDepth').textContent = `${Number(stats.maximumDepthM).toFixed(2)} m`;
  $('status').textContent = `${state.period}-year event · cache ${new Date(state.manifest.generatedAt).toLocaleDateString()}`;
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

document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', () => {
  document.querySelector('[data-period].active')?.classList.remove('active');
  button.classList.add('active');
  state.period = button.dataset.period;
  render();
}));

document.querySelectorAll('input[name="layer"]').forEach(input => input.addEventListener('change', () => {
  state.layer = input.value;
  render();
}));

$('opacity').addEventListener('input', () => state.overlay?.setOpacity(Number($('opacity').value) / 100));

fetch('/results/manifest.json', { cache: 'no-store' })
  .then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then(manifest => {
    if (manifest.schemaVersion !== 1 || !manifest.scenarios?.['20']) throw new Error('Unsupported result manifest');
    state.manifest = manifest;
    map.fitBounds(manifest.bounds, { padding: [20, 20] });
    render();
  })
  .catch(error => {
    console.error(error);
    $('status').textContent = 'Results unavailable';
    $('error').hidden = false;
    map.setView([32.12, 118.95], 11);
  });
