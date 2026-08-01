/**
 * HydroclimateX Lab — Main JavaScript
 * Author: HydroclimateX Lab
 * Date: 2026-08-01
 *
 * Features:
 * - Navigation scroll shadow & mobile toggle
 * - Active section tracking on scroll
 * - Scroll-triggered animations (Intersection Observer)
 * - Google Scholar metrics (footer)
 * - Featured + full publication list with filtering
 * - WASP-Web iframe lazy-loading
 * - Hero background particle animation (Canvas)
 */

// ================================================================
// 1. Navigation
// ================================================================
(function initNav() {
  const nav = document.getElementById('nav');
  const toggle = document.getElementById('navToggle');
  const links = document.querySelector('.nav-links');

  // Scroll shadow
  let scrollTicking = false;
  window.addEventListener('scroll', () => {
    if (!scrollTicking) {
      requestAnimationFrame(() => {
        nav.classList.toggle('scrolled', window.scrollY > 10);
        scrollTicking = false;
      });
      scrollTicking = true;
    }
  });

  // Mobile toggle
  toggle.addEventListener('click', () => {
    links.classList.toggle('open');
    const spans = toggle.querySelectorAll('span');
    if (links.classList.contains('open')) {
      spans[0].style.transform = 'rotate(45deg) translate(5px, 5px)';
      spans[1].style.opacity = '0';
      spans[2].style.transform = 'rotate(-45deg) translate(5px, -5px)';
    } else {
      spans[0].style.transform = '';
      spans[1].style.opacity = '';
      spans[2].style.transform = '';
    }
  });

  // Close mobile menu on link click
  links.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => {
      links.classList.remove('open');
      const spans = toggle.querySelectorAll('span');
      spans[0].style.transform = '';
      spans[1].style.opacity = '';
      spans[2].style.transform = '';
    });
  });

  // Active section tracking via Intersection Observer
  const sections = document.querySelectorAll('header[id], section[id]');
  const navLinks = document.querySelectorAll('.nav-links a');

  const sectionObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          navLinks.forEach(link => {
            link.classList.toggle('active-section', link.getAttribute('href') === `#${id}`);
          });
        }
      });
    },
    { threshold: 0.3, rootMargin: '-80px 0px -40% 0px' }
  );

  sections.forEach(s => sectionObserver.observe(s));
})();

// ================================================================
// 2. Scroll Animations
// ================================================================
(function initAnimations() {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
        }
      });
    },
    { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
  );

  document.querySelectorAll('[data-animate]').forEach(el => observer.observe(el));
})();

// ================================================================
// 3. Google Scholar Metrics (footer)
// ================================================================
(async function loadScholarMetrics() {
  const citationsEl = document.getElementById('ftCitations');
  const hindexEl = document.getElementById('ftHindex');
  const i10El = document.getElementById('ftI10');

  try {
    const resp = await fetch(
      'https://scholar.google.com/citations?user=4iVouPYAAAAJ&hl=en',
      { signal: AbortSignal.timeout(8000) }
    );
    const html = await resp.text();

    const cMatch = html.match(/Cited by (\d[\d,]*)/);
    const hMatch = html.match(/h-index[:\s]*(\d+)/i);
    const iMatch = html.match(/i10-index[:\s]*(\d+)/i);

    if (cMatch) citationsEl.textContent = cMatch[1];
    if (hMatch) hindexEl.textContent = hMatch[1];
    if (iMatch) i10El.textContent = iMatch[1];
  } catch {
    // Keep fallback values from HTML
  }
})();

// ================================================================
// 4. Featured Publications
// ================================================================
(function renderFeaturedPubs() {
  const featured = [
    {
      journal: 'Geophysical Research Letters',
      year: 2025,
      title: 'Spectral transformation of covariates improves seasonal flood forecasting',
      authors: 'Jiang, Z., Merz, B. & Sharma, A.',
      desc: 'Demonstrates that spectrally transforming climate covariates using wavelet theory significantly improves seasonal flood prediction skill.',
      tag: 'extreme',
    },
    {
      journal: 'Water Resources Research',
      year: 2020,
      title: 'Refining predictor spectral representation using wavelet theory for improved natural system modelling',
      authors: 'Jiang, Z., Sharma, A. & Johnson, F.',
      desc: 'Foundational WASP paper — introduces the wavelet-based variance modulation framework.',
      tag: 'method',
    },
    {
      journal: 'Monthly Weather Review',
      year: 2023,
      title: 'Frequency-domain quantile mapping: A signal processing approach to correct systematic bias',
      authors: 'Jiang, Z. & Johnson, F.',
      desc: 'Introduces WQM — quantile mapping in the frequency domain for NWP postprocessing.',
      tag: 'bias',
    },
    {
      journal: "Earth's Future",
      year: 2023,
      title: 'Derived drought indices and future drought change under climate scenarios',
      authors: 'Jiang, Z., Johnson, F. & Sharma, A.',
      desc: 'Develops new drought indices and projects drought changes under emission scenarios.',
      tag: 'drought',
    },
    {
      journal: 'Journal of Hydrology',
      year: 2025,
      title: 'Spectrally transformed CMIP6 decadal projections improve rainfall forecasts',
      authors: 'Jiang, Z., Choudhury, D. & Sharma, A.',
      desc: 'Applies WASP to CMIP6 decadal predictions for improved rainfall forecast accuracy.',
      tag: 'drought',
    },
    {
      journal: 'Environmental Modelling & Software',
      year: 2021,
      title: 'WASP: A wavelet-based tool to modulate variance in predictors',
      authors: 'Jiang, Z., Sharma, A. & Johnson, F.',
      desc: 'The WASP R package release — software architecture, API, and multi-catchment validation.',
      tag: 'method',
    },
  ];

  const container = document.getElementById('featuredPubs');
  container.innerHTML = featured.map(pub => `
    <div class="pub-card" data-tag="${pub.tag}">
      <span class="pub-card-journal">${pub.journal}</span>
      <span class="pub-card-year">${pub.year}</span>
      <h4 class="pub-card-title">${pub.title}</h4>
      <p class="pub-card-authors">${pub.authors}</p>
      <p class="pub-card-desc">${pub.desc}</p>
    </div>
  `).join('');
})();

// ================================================================
// 5. Full Publication List + Filtering
// ================================================================
(function renderPubList() {
  const allPubs = [
    { year: 2025, title: 'Spectral transformation of covariates improves seasonal flood forecasting', authors: 'Jiang, Z., Merz, B. & Sharma, A.', venue: 'Geophysical Research Letters', tag: 'extreme' },
    { year: 2025, title: 'Spectrally transformed CMIP6 decadal projections improve rainfall forecasts', authors: 'Jiang, Z., Choudhury, D. & Sharma, A.', venue: 'Journal of Hydrology', tag: 'drought' },
    { year: 2025, title: 'Decadal drought prediction via spectral transformation of sea-surface temperatures', authors: 'Jiang, Z. & Sharma, A.', venue: 'Journal of Hydrology X', tag: 'drought' },
    { year: 2023, title: 'Derived drought indices and future drought change under climate scenarios', authors: 'Jiang, Z., Johnson, F. & Sharma, A.', venue: "Earth's Future", tag: 'drought' },
    { year: 2023, title: 'Frequency-domain quantile mapping: A signal processing approach to correct systematic bias', authors: 'Jiang, Z. & Johnson, F.', venue: 'Monthly Weather Review', tag: 'bias' },
    { year: 2022, title: 'Investigating the linkage between extreme rainstorms and concurrent synoptic features', authors: 'Jiang, Z. et al.', venue: 'Journal of Hydrometeorology', tag: 'extreme' },
    { year: 2022, title: 'Correcting systematic bias in climate model simulations in the time-frequency domain', authors: 'Jiang, Z. & Johnson, F.', venue: 'Geophysical Research Letters', tag: 'bias' },
    { year: 2021, title: 'WASP: A wavelet-based tool to modulate variance in predictors', authors: 'Jiang, Z., Sharma, A. & Johnson, F.', venue: 'Environmental Modelling & Software', tag: 'method' },
    { year: 2020, title: 'Refining predictor spectral representation using wavelet theory', authors: 'Jiang, Z., Sharma, A. & Johnson, F.', venue: 'Water Resources Research', tag: 'method' },
    { year: 2020, title: 'Using a regional climate model to develop index-based drought insurance', authors: 'Rashid, M.M., Jiang, Z. et al.', venue: 'Agricultural and Forest Meteorology', tag: 'drought' },
    { year: 2019, title: 'Future changes in rice yields over the Mekong River Delta due to climate change', authors: 'Jiang, Z. et al.', venue: 'Theoretical and Applied Climatology', tag: 'drought' },
    { year: 2019, title: 'Assessing the sensitivity of hydro-climatological change detection methods', authors: 'Jiang, Z. et al.', venue: 'Water Resources Management', tag: 'method' },
  ].sort((a, b) => b.year - a.year);

  const pubList = document.getElementById('pubList');
  const pubCount = document.getElementById('pubCount');
  const pubUpdated = document.getElementById('pubUpdated');

  pubCount.textContent = `${allPubs.length} publications`;
  pubUpdated.textContent = `Synced: ${new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}`;

  function render(filter) {
    const filtered = filter === 'all' ? allPubs : allPubs.filter(p => p.tag === filter);
    pubList.innerHTML = filtered.map(p => `
      <li class="pub-list-item" data-tag="${p.tag}">
        <span class="pub-year-tag">${p.year}</span>
        <span class="pub-title">${p.title}</span><br>
        <span class="pub-authors">${p.authors}</span><br>
        <span class="pub-venue">${p.venue}</span>
      </li>
    `).join('');
  }

  render('all');

  // Filter buttons
  const filters = document.querySelectorAll('.pub-filter');
  const featuredCards = document.querySelectorAll('.pub-card');

  filters.forEach(btn => {
    btn.addEventListener('click', () => {
      filters.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const filter = btn.dataset.filter;
      render(filter);

      featuredCards.forEach(card => {
        card.style.display = (filter === 'all' || card.dataset.tag === filter) ? '' : 'none';
      });
    });
  });
})();

// ================================================================
// 6. WASP-Web iFrame Lazy Load
// ================================================================
(function initWaspIframe() {
  const iframe = document.getElementById('waspIframe');
  const fallback = document.getElementById('waspFallback');
  if (!iframe) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        // Already has src in HTML; fallback on error
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.05 });

  observer.observe(document.getElementById('waspDemo'));

  iframe.addEventListener('error', () => {
    iframe.style.display = 'none';
    fallback.style.display = 'flex';
  });
})();

// ================================================================
// 7. Hero Particle Animation (Canvas)
// ================================================================
(function initHeroParticles() {
  const container = document.getElementById('heroParticles');
  if (!container) return;

  const canvas = document.createElement('canvas');
  container.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  let particles = [];
  const PARTICLE_COUNT = 45;
  const CONNECTION_DIST = 110;
  const BLUE_HEX = '#0B5EA7';

  function resize() {
    const rect = container.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
  }

  function createParticles() {
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        radius: Math.random() * 2 + 1,
        opacity: Math.random() * 0.4 + 0.15,
      });
    }
  }

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    particles.forEach(p => {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0) p.x = canvas.width;
      if (p.x > canvas.width) p.x = 0;
      if (p.y < 0) p.y = canvas.height;
      if (p.y > canvas.height) p.y = 0;

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fillStyle = `${BLUE_HEX}${Math.round(p.opacity * 255).toString(16).padStart(2, '0')}`;
      ctx.fill();
    });

    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < CONNECTION_DIST) {
          const alpha = (1 - dist / CONNECTION_DIST) * 0.12;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `${BLUE_HEX}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`;
          ctx.lineWidth = 0.7;
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(animate);
  }

  resize();
  createParticles();
  animate();
  window.addEventListener('resize', () => { resize(); createParticles(); });
})();
