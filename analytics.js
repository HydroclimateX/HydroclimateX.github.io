(() => {
  'use strict';

  const telemetryOrigin = 'https://telemetry.hydroclimatex.com';
  const configUrl = 'https://telemetry.hydroclimatex.com/config.json';
  const allowedDomains = 'hydroclimatex.com,www.hydroclimatex.com';

  fetch(configUrl, { credentials: 'omit', cache: 'no-store' })
    .then(response => {
      if (!response.ok) throw new Error('telemetry configuration unavailable');
      return response.json();
    })
    .then(config => {
      if (!config.websiteId) return;
      const script = document.createElement('script');
      script.defer = true;
      script.src = `${telemetryOrigin}/script.js`;
      script.dataset.websiteId = config.websiteId;
      script.dataset.domains = allowedDomains;
      script.setAttribute('data-domains', allowedDomains);
      document.head.appendChild(script);
    })
    .catch(() => { /* analytics must never affect the public site */ });

  function eventForLink(anchor) {
    const url = new URL(anchor.href, window.location.href);
    const path = url.pathname.toLowerCase();
    if (anchor.hasAttribute('download') || /\.(csv|zip|pdf|r|py|m)(?:$|\?)/i.test(path)) return 'file_download';
    if (url.hostname === 'wasp.hydroclimatex.com') return 'wasp_launch';
    if (url.hostname === 'github.com' || url.hostname.endsWith('.github.com')) return 'github_click';
    if (anchor.closest('#publications') || url.hostname.includes('scholar.google.')) return 'publication_click';
    return null;
  }

  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href]');
    if (!anchor) return;
    const name = eventForLink(anchor);
    if (name && window.umami && typeof window.umami.track === 'function') {
      window.umami.track(name);
    }
  }, { capture: true });
})();
