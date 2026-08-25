(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const state = { csrf: '', period: '30d', start: '', end: '', countries: [] };

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    if (response.status === 401) { showLogin(); throw new Error('Authentication required'); }
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  }

  function showLogin() { byId('dashboardView').hidden = true; byId('authView').hidden = false; }
  function showDashboard() { byId('authView').hidden = true; byId('dashboardView').hidden = false; }
  function formatNumber(value) { return value == null ? 'Unavailable' : new Intl.NumberFormat('en').format(value); }
  function formatRate(value) { return value == null ? 'N/A' : new Intl.NumberFormat('en',{style:'percent',maximumFractionDigits:1}).format(value); }
  function periodQuery() {
    const params = new URLSearchParams({ period: state.period });
    if (state.period === 'custom') { params.set('start', state.start); params.set('end', state.end); }
    return params.toString();
  }

  function setSourceStatus(id, source, status, freshness) {
    const checked = freshness?.checked_at ? new Date(freshness.checked_at).toLocaleString('en-AU',{timeZone:'Asia/Hong_Kong'}) : 'unknown';
    const activity = freshness?.last_activity ? ` · last activity ${new Date(freshness.last_activity).toLocaleString('en-AU',{timeZone:'Asia/Hong_Kong'})}` : '';
    const element = byId(id); element.textContent = `${source}: ${status} · checked ${checked}${activity}`;
    element.classList.toggle('unavailable', status !== 'available');
  }

  async function loadDashboard() {
    byId('dashboardError').textContent = '';
    try {
      const query = periodQuery();
      const [summary, usage, website] = await Promise.all([
        api(`/api/v1/summary?${query}`), api(`/api/v1/wasp/countries?${query}`), api('/api/v1/website/windows'),
      ]);
      byId('kpiVisitors').textContent = formatNumber(summary.kpis.visitors);
      byId('kpiPageviews').textContent = formatNumber(summary.kpis.pageviews);
      byId('kpiRuns').textContent = formatNumber(summary.kpis.successful_runs);
      byId('kpiSuccessRate').textContent = formatRate(summary.kpis.success_rate);
      byId('kpiCountries').textContent = formatNumber(summary.kpis.countries);
      byId('collectedSince').textContent = `Data collected since ${new Date(summary.collected_since).toLocaleString('en-AU',{timeZone:'Asia/Hong_Kong'})} · Reporting timezone: Asia/Hong_Kong`;
      setSourceStatus('websiteStatus','Website',summary.sources.website,summary.source_freshness?.website);
      setSourceStatus('waspStatus','WASP',summary.sources.wasp,summary.source_freshness?.wasp);
      state.countries = usage.countries;
      renderMap(); renderCountryTable(); renderWebsiteTable(website.metrics || []);
      byId('csvLink').href = `/api/v1/wasp/export.csv?${query}`;
    } catch (error) { if (error.message !== 'Authentication required') byId('dashboardError').textContent = error.message; }
  }

  function renderMap() {
    if (!window.Plotly) { byId('usageMap').textContent = 'Map library unavailable. Country table remains available.'; return; }
    const metric = byId('mapMetric').value;
    const labels = {successful_runs:'Successful runs',failed_runs:'Failed runs',downloads:'Downloads',sessions:'Sessions'};
    const rows = state.countries.filter(row => row.country_code !== 'ZZ');
    Plotly.newPlot('usageMap',[{type:'choropleth',locationmode:'country names',locations:rows.map(r=>r.country),z:rows.map(r=>r[metric]),text:rows.map(r=>r.country),hovertemplate:`%{text}<br>${labels[metric]}: %{z}<extra></extra>`,colorscale:[[0,'#e5f2ef'],[1,'#0f766e']],marker:{line:{color:'#fff',width:.5}},colorbar:{title:labels[metric]}}],{geo:{showframe:false,showcoastlines:false,projection:{type:'natural earth'},bgcolor:'#f9fbfa'},paper_bgcolor:'#f9fbfa',margin:{l:0,r:0,t:10,b:0}},{responsive:true,displayModeBar:false});
    const map = byId('usageMap'); map.removeAllListeners?.('plotly_click');
    map.on('plotly_click', event => { const name = event.points[0]?.location; const row = state.countries.find(item=>item.country===name); if(row) void selectCountry(row.country_code); });
  }

  function renderCountryTable() {
    const body = byId('countryRows'); body.replaceChildren();
    state.countries.forEach(row => {
      const tr = document.createElement('tr'); tr.dataset.country = row.country_code; tr.tabIndex = 0;
      [row.country,row.successful_runs,row.failed_runs,row.downloads,row.sessions,row.last_activity || '—'].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.appendChild(td);});
      const choose=()=>selectCountry(row.country_code); tr.addEventListener('click',choose); tr.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();void choose();}}); body.appendChild(tr);
    });
  }

  async function selectCountry(code) {
    try {
      const row = await api(`/api/v1/wasp/countries/${encodeURIComponent(code)}?${periodQuery()}`);
      const panel=byId('countryDetail'); panel.replaceChildren();
      const eyebrow=document.createElement('p');eyebrow.className='eyebrow';eyebrow.textContent='Country detail';
      const title=document.createElement('h3');title.textContent=row.country;
      const list=document.createElement('dl');
      [['Successful runs',row.successful_runs],['Failed runs',row.failed_runs],['Downloads',row.downloads],['Sessions',row.sessions],['Last activity',row.last_activity||'—']].forEach(([label,value])=>{const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;list.append(dt,dd);});
      panel.append(eyebrow,title,list);
    } catch(error){byId('dashboardError').textContent=error.message;}
  }

  function renderWebsiteTable(rows) {
    const body=byId('websiteRows');body.replaceChildren();rows.forEach(row=>{const tr=document.createElement('tr');[row.metric,row.days_30,row.months_12,row.all_time].forEach((value,index)=>{const td=document.createElement('td');td.textContent=index===0?value:formatNumber(value);tr.appendChild(td);});body.appendChild(tr);});
  }

  async function restoreSession() {
    try { const session=await api('/auth/session');state.csrf=session.csrf_token;byId('adminEmail').textContent=session.email;showDashboard();await loadDashboard(); }
    catch (_) { showLogin(); }
  }

  byId('loginForm').addEventListener('submit',async event=>{event.preventDefault();byId('loginError').textContent='';try{const session=await api('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:byId('email').value,password:byId('password').value})});state.csrf=session.csrf_token;byId('adminEmail').textContent=session.email;byId('password').value='';showDashboard();await loadDashboard();}catch(error){byId('loginError').textContent=error.message;}});
  byId('logoutButton').addEventListener('click',async()=>{try{await api('/auth/logout',{method:'POST',headers:{'X-CSRF-Token':state.csrf}});}finally{state.csrf='';showLogin();}});
  byId('periodSelect').addEventListener('change',event=>{state.period=event.target.value;byId('customDates').hidden=state.period!=='custom';if(state.period!=='custom')void loadDashboard();});
  byId('applyDates').addEventListener('click',()=>{state.start=byId('startDate').value;state.end=byId('endDate').value;if(state.start&&state.end)void loadDashboard();});
  byId('mapMetric').addEventListener('change',renderMap);
  byId('previewReport').addEventListener('click',async()=>{const month=byId('reportMonth').value;if(!month)return;try{const report=await api(`/api/v1/reports/${month}`);byId('reportPreview').textContent=JSON.stringify(report,null,2);}catch(error){byId('reportPreview').textContent=error.message;}});
  byId('sendReport').addEventListener('click',async()=>{const month=byId('reportMonth').value;if(!month)return;try{const result=await api(`/api/v1/reports/${month}/send`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify({force:false})});byId('reportPreview').textContent=JSON.stringify(result,null,2);}catch(error){byId('reportPreview').textContent=error.message;}});
  void restoreSession();
})();
