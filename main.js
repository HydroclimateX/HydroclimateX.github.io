/**
 * HydroclimateX Lab — Main JavaScript
 *
 * Features:
 * - Mobile menu toggle
 * - Google Scholar metrics (facts bar + footer year)
 * - Publication list: featured cards + filterable full list
 */

// ================================================================
// 1. Mobile menu
// ================================================================
(function () {
  const header = document.querySelector('.site-header');
  const btn = document.querySelector('.menu-button');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const open = header.classList.toggle('open');
    btn.setAttribute('aria-expanded', open);
  });
})();

// ================================================================
// 2. Footer year
// ================================================================
(function () {
  const el = document.getElementById('year');
  if (el) el.textContent = new Date().getFullYear();
})();

// ================================================================
// 3. Google Scholar metrics (from data/scholar-stats.json)
// ================================================================
(async function () {
  const metricsEl = document.getElementById('scholar-metrics');
  const updatedEl = document.getElementById('scholar-updated');
  try {
    const resp = await fetch('data/scholar-stats.json', { signal: AbortSignal.timeout(8000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const s = await resp.json();
    if (metricsEl && Number.isFinite(s.citations) && Number.isFinite(s.hIndex)) {
      metricsEl.textContent = `${s.citations} citations · h-index ${s.hIndex} · i10-index ${s.i10Index}`;
    }
    window.__scholarUpdatedAt = s.updatedAt || null;
    if (updatedEl) {
      updatedEl.textContent = s.updatedAt
        ? `Synced ${fmtDate(s.updatedAt)}`
        : 'Synced daily from Google Scholar';
    }
  } catch (_) { /* keep the HTML fallback */ }
})();

// ================================================================
// 4. Publication data
// ================================================================
// Curated fallback list, used when data/scholar-publications.json is
// unavailable (offline or before the first sync).
const FALLBACK_PUBLICATIONS = [
  { year: 2025, title: 'Spectral transformation of covariates improves seasonal flood forecasting', authors: 'Jiang, Z., Merz, B. & Sharma, A.', venue: 'Geophysical Research Letters', type: 'article', tag: 'extreme' },
  { year: 2025, title: 'Spectrally transformed CMIP6 decadal projections improve rainfall forecasts', authors: 'Jiang, Z., Choudhury, D. & Sharma, A.', venue: 'Journal of Hydrology', type: 'article', tag: 'drought' },
  { year: 2025, title: 'Decadal drought prediction via spectral transformation of sea-surface temperatures', authors: 'Jiang, Z. & Sharma, A.', venue: 'Journal of Hydrology X', type: 'article', tag: 'drought' },
  { year: 2023, title: 'Derived drought indices and future drought change under climate scenarios', authors: 'Jiang, Z., Johnson, F. & Sharma, A.', venue: "Earth's Future", type: 'article', tag: 'drought' },
  { year: 2023, title: 'Frequency-domain quantile mapping: a signal processing approach to correct systematic bias', authors: 'Jiang, Z. & Johnson, F.', venue: 'Monthly Weather Review', type: 'article', tag: 'bias' },
  { year: 2022, title: 'Investigating the linkage between extreme rainstorms and concurrent synoptic features', authors: 'Jiang, Z. et al.', venue: 'Journal of Hydrometeorology', type: 'article', tag: 'extreme' },
  { year: 2022, title: 'Correcting systematic bias in climate model simulations in the time-frequency domain', authors: 'Jiang, Z. & Johnson, F.', venue: 'Geophysical Research Letters', type: 'article', tag: 'bias' },
  { year: 2021, title: 'WASP: a wavelet-based tool to modulate variance in predictors for improved prediction', authors: 'Jiang, Z., Sharma, A. & Johnson, F.', venue: 'Environmental Modelling & Software', type: 'article', tag: 'method' },
  { year: 2020, title: 'Refining predictor spectral representation using wavelet theory for improved natural system modelling', authors: 'Jiang, Z., Sharma, A. & Johnson, F.', venue: 'Water Resources Research', type: 'article', tag: 'method' },
  { year: 2020, title: 'Using a regional climate model to develop index-based drought insurance for sovereign disaster risk transfer', authors: 'Rashid, M.M., Jiang, Z. et al.', venue: 'Agricultural and Forest Meteorology', type: 'article', tag: 'agri' },
  { year: 2019, title: 'Future changes in rice yields over the Mekong River Delta due to climate change — alarming or alerting?', authors: 'Jiang, Z. et al.', venue: 'Theoretical and Applied Climatology', type: 'article', tag: 'agri' },
  { year: 2019, title: 'Assessing the sensitivity of hydro-climatological change detection methods to model uncertainty and bias', authors: 'Jiang, Z. et al.', venue: 'Water Resources Management', type: 'article', tag: 'method' },
].sort((a, b) => (b.year || 0) - (a.year || 0));

let PUBLICATIONS = FALLBACK_PUBLICATIONS;

// Group papers into research directions using title/venue keywords.
function tagPublication(p) {
  const t = `${p.title} ${p.venue}`.toLowerCase();
  if (/\b(flood|flooding|rainstorm|storm surge|precipitation|extreme|compound)\b/.test(t)) return 'extreme';
  if (/\b(drought|aridity|arid)\b/.test(t)) return 'drought';
  if (/\b(bias|quantile mapping|post-process|downscal|cmip)\b/.test(t)) return 'bias';
  if (/\b(wavelet|spectral|wasp|predictor|ensemble|modell?ing|forecast|prediction)\b/.test(t)) return 'method';
  if (/\b(rice|crop|yield|agricultur|soybean|millet|wheat|farming)\b/.test(t)) return 'agri';
  return '';
}

// Escape text from the external data feed before injecting into innerHTML.
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return 'unknown date';
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

// ================================================================
// 5. Render publication list
// ================================================================
(function () {
  const list = document.getElementById('publication-list');
  const updatedEl = document.getElementById('publication-updated');
  if (!list) return;

  function syncLabel() {
    return window.__scholarUpdatedAt ? `Last synced: ${fmtDate(window.__scholarUpdatedAt)}` : '';
  }

  function render(filter) {
    const pubs = filter === 'all' ? PUBLICATIONS : PUBLICATIONS.filter(p => p.tag === filter);
    list.innerHTML = pubs.map(p => `
      <article class="publication" data-tag="${escapeHtml(p.tag)}">
        <div class="meta">
          <strong>${escapeHtml(p.year || 'n/a')}</strong><br>
          <span>${escapeHtml(p.type || '—')}</span>
        </div>
        <div>
          <h3>${escapeHtml(p.title)}</h3>
          <p>${escapeHtml(p.authors)}${p.venue ? ` <span class="pub-venue">· ${escapeHtml(p.venue)}</span>` : ''}</p>
        </div>
      </article>
    `).join('');
    if (updatedEl) {
      updatedEl.textContent = `Showing ${pubs.length} of ${PUBLICATIONS.length} publications${syncLabel() ? ' · ' + syncLabel() : ''}`;
    }
  }

  render('all');

  // Filter buttons
  document.querySelectorAll('.filters button').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filters button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      render(btn.dataset.filter);
    });
  });

  // Swap in the full Google Scholar list when data/scholar-publications.json
  // is available; otherwise keep the curated fallback list.
  (async () => {
    try {
      const resp = await fetch('data/scholar-publications.json', { signal: AbortSignal.timeout(8000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!Array.isArray(data) || data.length === 0) throw new Error('empty list');
      PUBLICATIONS = data
        .sort((a, b) => (b.year || 0) - (a.year || 0))
        .map(p => ({ ...p, tag: tagPublication(p) }));
      const active = document.querySelector('.filters button.active');
      render(active ? active.dataset.filter : 'all');
    } catch (_) { /* keep curated fallback list */ }
  })();
})();
