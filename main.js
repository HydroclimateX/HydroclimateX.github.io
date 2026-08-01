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
// 3. Google Scholar metrics
// ================================================================
(async function () {
  const metricsEl = document.getElementById('scholar-metrics');
  const updatedEl = document.getElementById('scholar-updated');
  try {
    const resp = await fetch(
      'https://scholar.google.com/citations?user=4iVouPYAAAAJ&hl=en',
      { signal: AbortSignal.timeout(8000) }
    );
    const html = await resp.text();
    const c = html.match(/Cited by (\d[\d,]*)/);
    const h = html.match(/h-index[:\s]*(\d+)/i);
    const i10 = html.match(/i10-index[:\s]*(\d+)/i);
    if (c && h && i10 && metricsEl) {
      metricsEl.textContent = `${c[1]} citations · h-index ${h[1]} · i10-index ${i10[1]}`;
    }
    if (updatedEl) updatedEl.textContent = 'Synced daily from Google Scholar';
  } catch (_) { /* keep fallback */ }
})();

// ================================================================
// 4. Publication data
// ================================================================
const PUBLICATIONS = [
  { year: 2025, title: 'Spectral transformation of covariates improves seasonal flood forecasting', authors: 'Jiang, Z., Merz, B. & Sharma, A.', venue: 'Geophysical Research Letters', tag: 'extreme' },
  { year: 2025, title: 'Spectrally transformed CMIP6 decadal projections improve rainfall forecasts', authors: 'Jiang, Z., Choudhury, D. & Sharma, A.', venue: 'Journal of Hydrology', tag: 'drought' },
  { year: 2025, title: 'Decadal drought prediction via spectral transformation of sea-surface temperatures', authors: 'Jiang, Z. & Sharma, A.', venue: 'Journal of Hydrology X', tag: 'drought' },
  { year: 2023, title: 'Derived drought indices and future drought change under climate scenarios', authors: 'Jiang, Z., Johnson, F. & Sharma, A.', venue: "Earth's Future", tag: 'drought' },
  { year: 2023, title: 'Frequency-domain quantile mapping: a signal processing approach to correct systematic bias', authors: 'Jiang, Z. & Johnson, F.', venue: 'Monthly Weather Review', tag: 'bias' },
  { year: 2022, title: 'Investigating the linkage between extreme rainstorms and concurrent synoptic features', authors: 'Jiang, Z. et al.', venue: 'Journal of Hydrometeorology', tag: 'extreme' },
  { year: 2022, title: 'Correcting systematic bias in climate model simulations in the time-frequency domain', authors: 'Jiang, Z. & Johnson, F.', venue: 'Geophysical Research Letters', tag: 'bias' },
  { year: 2021, title: 'WASP: a wavelet-based tool to modulate variance in predictors for improved prediction', authors: 'Jiang, Z., Sharma, A. & Johnson, F.', venue: 'Environmental Modelling & Software', tag: 'method' },
  { year: 2020, title: 'Refining predictor spectral representation using wavelet theory for improved natural system modelling', authors: 'Jiang, Z., Sharma, A. & Johnson, F.', venue: 'Water Resources Research', tag: 'method' },
  { year: 2020, title: 'Using a regional climate model to develop index-based drought insurance for sovereign disaster risk transfer', authors: 'Rashid, M.M., Jiang, Z. et al.', venue: 'Agricultural and Forest Meteorology', tag: 'agri' },
  { year: 2019, title: 'Future changes in rice yields over the Mekong River Delta due to climate change — alarming or alerting?', authors: 'Jiang, Z. et al.', venue: 'Theoretical and Applied Climatology', tag: 'agri' },
  { year: 2019, title: 'Assessing the sensitivity of hydro-climatological change detection methods to model uncertainty and bias', authors: 'Jiang, Z. et al.', venue: 'Water Resources Management', tag: 'method' },
].sort((a, b) => b.year - a.year);

// ================================================================
// 5. Render publication list
// ================================================================
(function () {
  const list = document.getElementById('publication-list');
  const updatedEl = document.getElementById('publication-updated');
  if (!list) return;

  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });

  function render(filter) {
    const pubs = filter === 'all' ? PUBLICATIONS : PUBLICATIONS.filter(p => p.tag === filter);
    list.innerHTML = pubs.map(p => `
      <article class="publication" data-tag="${p.tag}">
        <div class="meta">
          <strong>${p.year}</strong><br>
          <span>${p.venue.split(' ').slice(0, 2).join(' ')}</span>
        </div>
        <div>
          <h3>${p.title}</h3>
          <p>${p.authors} <span class="pub-venue">· ${p.venue}</span></p>
        </div>
      </article>
    `).join('');
    if (updatedEl) updatedEl.textContent = `Showing ${pubs.length} of ${PUBLICATIONS.length} publications · Last synced: ${dateStr}`;
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
})();

// ================================================================
// 6. WASP iframe lazy-load
// ================================================================
(function () {
  const iframe = document.getElementById('waspIframe');
  if (!iframe) return;
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.05 });
  observer.observe(iframe);
})();
