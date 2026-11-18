const USERS = [
  {id:"alex.martinez", name:"Alex Martinez", initials:"AM"},
  {id:"lena.morris", name:"Lena Morris", initials:"LM"},
  {id:"kendra.hall", name:"Kendra Hall", initials:"KH"},
  {id:"tasha.singh", name:"Tasha Singh", initials:"TS"}
];

const savedUser = localStorage.getItem("resourcebridge-demo-user") || USERS[0].id;
const state = {
  view:"home",
  currentUser: USERS.find(u=>u.id===savedUser) || USERS[0],
  health:null,
  freshness:null,
  external:null,
  resources:[],
  corrections:[],
  posts:[],
  discovery:[],
  query:"",
  zipCode:"",
  searchLocation:null,
  searchLoading:false,
  searchError:"",
  selectedResourceId:null,
  selectedTab:"overview",
  resourceHistory:[],
  librarySearch:"",
  networkSearch:"",
  networkFilter:"All",
  modal:null,
  commentTarget:null,
  freshnessRunning:false
};

const FIELD_LABELS = {
  phone:"Phone",
  hours:"Hours",
  address:"Address",
  postal_code:"ZIP code",
  website:"Website",
  languages:"Languages",
  eligibility:"Eligibility",
  external_status:"Program status"
};

const ISSUE_TO_FIELD = {
  "Phone number":"phone",
  "Hours":"hours",
  "Address":"address",
  "ZIP code":"postal_code",
  "Website":"website",
  "Languages":"languages",
  "Eligibility requirements":"eligibility",
  "Program status":"external_status"
};

const FLAG_TO_FIELD = {
  "Phone number":"phone",
  "Phone":"phone",
  "Hours":"hours",
  "Address":"address",
  "ZIP code":"postal_code",
  "Postal code":"postal_code",
  "Website":"website",
  "Languages":"languages",
  "Eligibility requirements":"eligibility",
  "Eligibility":"eligibility",
  "Program status":"external_status"
};

function fieldAttention(resource, field){
  const flags=(resource?.flags||[]).filter(f=>FLAG_TO_FIELD[f.field]===field);
  const proposed=state.corrections.filter(c=>Number(c.resource_id)===Number(resource?.id) && c.status!=="verified" && ISSUE_TO_FIELD[c.issueType]===field);
  return {flags, proposed, active:Boolean(flags.length||proposed.length)};
}

function intakeAttention(resource){
  const flags=(resource?.flags||[]).filter(f=>f.field==="Availability / waitlist");
  return {flags, active:Boolean(flags.length)};
}

const $ = (sel, root=document) => root.querySelector(sel);
const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];
const esc = (value="") => String(value).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const clean = (value="") => String(value ?? "").trim();

async function api(path, options={}){
  const init={...options, headers:{"Content-Type":"application/json", ...(options.headers||{})}};
  const response=await fetch(path, init);
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function fmt(value, dateOnly=false){
  if(!value) return "Not yet";
  const d=new Date(value);
  if(Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("en-US", dateOnly?{month:"short",day:"numeric",year:"numeric"}:{month:"short",day:"numeric",year:"numeric",hour:"numeric",minute:"2-digit"});
}
function short(value, n=155){
  const text=clean(value);
  return text.length>n ? `${text.slice(0,n-1)}…` : text;
}
function initials(name){ return clean(name).split(/\s+/).map(x=>x[0]).join("").slice(0,2).toUpperCase() || "CH"; }
function sourceLabel(source){
  return ({front_door:"Front Door Illinois", local_csv:"Local housing seed"})[source] || (source?source.replaceAll("_"," "):"External source");
}
function sourceTone(source){ return source==="front_door"?"blue":source==="local_csv"?"gray":"purple"; }
function safeLink(url){
  const value=clean(url);
  if(!/^https?:\/\//i.test(value)) return "";
  return value;
}
function currentUser(){ return state.currentUser; }
function resourceById(id){ return state.resources.find(r=>Number(r.id)===Number(id)) || null; }
function resourceForKey(key){ return state.resources.find(r=>`${r.external_source}:${r.external_id}`===key) || null; }
function selectedResource(){ return resourceById(state.selectedResourceId); }
function effective(resource, field){ return resource?.effective?.[field] || resource?.[field] || ""; }
function hasOverride(resource, field){ return Boolean(resource?.overrides?.[field]); }
function resourcePayload(resource){
  return {
    external_source:resource.external_source,
    external_id:resource.external_id,
    organization:resource.organization,
    service_name:resource.service_name||"",
    category:resource.category||"",
    phone:resource.phone||"",
    website:resource.website||"",
    source_url:resource.source_url||resource.website||"",
    address:resource.address||"",
    postal_code:resource.postal_code||"",
    latitude:resource.latitude??null,
    longitude:resource.longitude??null,
    neighborhood:resource.neighborhood||"",
    hours:resource.hours||"",
    languages:resource.languages||"",
    service_area:resource.service_area||"",
    services:resource.services||"",
    eligibility:resource.eligibility||"",
    application_process:resource.application_process||"",
    external_status:resource.external_status||"",
    external_assured_date:resource.external_assured_date||""
  };
}
function activityTime(item){ return new Date(item.timestamp || item.created_at || 0).getTime() || 0; }
function toast(message, tone="success"){
  const root=$("#toast-root");
  if(!root) return;
  const el=document.createElement("div");
  el.className=`toast ${tone}`;
  el.textContent=message;
  root.appendChild(el);
  requestAnimationFrame(()=>el.classList.add("show"));
  setTimeout(()=>{el.classList.remove("show"); setTimeout(()=>el.remove(),220);},2800);
}

async function refreshCore(){
  const uid=encodeURIComponent(currentUser().id);
  const [health, resources, corrections, posts, freshness, external] = await Promise.allSettled([
    api("/api/health"),
    api("/api/resources"),
    api(`/api/corrections?user=${uid}`),
    api(`/api/network/posts?user=${uid}`),
    api("/api/freshness/status"),
    api("/api/external/status")
  ]);
  if(health.status==="fulfilled") state.health=health.value;
  if(resources.status==="fulfilled") state.resources=resources.value.resources||[];
  if(corrections.status==="fulfilled") state.corrections=corrections.value.corrections||[];
  if(posts.status==="fulfilled") state.posts=posts.value.posts||[];
  if(freshness.status==="fulfilled") state.freshness=freshness.value;
  if(external.status==="fulfilled") state.external=external.value.front_door||null;
}

async function refreshResourceHistory(resourceId){
  if(!resourceId){state.resourceHistory=[];return;}
  try{ state.resourceHistory=(await api(`/api/history?resource_id=${resourceId}`)).history||[]; }
  catch{ state.resourceHistory=[]; }
}

async function runSearch(query=state.query, zip=state.zipCode){
  state.query=clean(query);
  state.zipCode=clean(zip);
  if(state.zipCode && !/^\d{5}$/.test(state.zipCode)){
    state.searchError="Enter a 5-digit ZIP code."; state.discovery=[]; render(); return;
  }
  if(!state.query && !state.zipCode){ state.discovery=[]; state.searchError=""; state.searchLocation=null; render(); return; }
  state.searchLoading=true; state.searchError=""; render();
  try{
    const data=await api(`/api/discovery/search?q=${encodeURIComponent(state.query)}&zip=${encodeURIComponent(state.zipCode)}&city=Chicago&limit=18`);
    state.discovery=data.results||[];
    state.external=data.external||state.external;
    state.searchLocation=data.location||null;
    if(data.location?.zip_code) state.zipCode=data.location.zip_code;
    await refreshCore();
  }catch(error){
    state.searchError=error.message;
    state.discovery=[];
  }finally{
    state.searchLoading=false;
    render();
  }
}


async function openResource(resourceId){
  state.selectedResourceId=Number(resourceId);
  state.selectedTab="overview";
  await refreshCore();
  await refreshResourceHistory(resourceId);
  state.view="resource";
  render();
}

function nav(){
  const items=[
    ["home","⌂","Home"],
    ["find","⌕","Find resources"],
    ["library","▤","Resource library"],
    ["network","✦","CHW Network"],
    ["about","◎","How it works"]
  ];
  return `<aside class="sidebar">
    <div class="brand"><div class="brand-mark">R</div><div><strong>ResourceBridge</strong><span>CHW resource knowledge</span></div></div>
    <nav>${items.map(([id,icon,label])=>`<button class="nav-item ${state.view===id||state.view==="resource"&&id==="find"?"active":""}" data-view="${id}"><span>${icon}</span>${label}</button>`).join("")}</nav>
    <div class="sidebar-principle"><span class="mini-label">Design principle</span><strong>Automation finds uncertainty.<br>CHWs establish trust.</strong></div>
    <div class="sidebar-footer"><span class="status-dot"></span><div><strong>MVP demo</strong><small>No real client data</small></div></div>
  </aside>`;
}

function externalUnavailable(){
  const error=clean(state.external?.error).toLowerCase();
  return state.external?.available===false && error && !error.includes("has not been downloaded yet");
}
function topbar(){
  const unavailable=externalUnavailable();
  return `<header class="topbar">
    <div class="mobile-brand">ResourceBridge</div>
    <div class="topbar-spacer"></div>
    <div class="source-mini ${unavailable?"warning":""}"><span class="status-dot"></span>${unavailable?"External source unavailable":state.external?.available?"Sources connected":"External source loads on search"}</div>
    <label class="identity-switch"><span>Demo CHW</span><select id="identity-select">${USERS.map(u=>`<option value="${u.id}" ${u.id===currentUser().id?"selected":""}>${esc(u.name)}</option>`).join("")}</select></label>
    <span class="avatar">${currentUser().initials}</span>
  </header>`;
}

function shell(content){
  return `<div class="app-shell">${nav()}<main class="main">${topbar()}<div class="page">${content}</div></main></div>${renderModal()}`;
}

function metric(value,label,detail=""){
  return `<div class="metric"><strong>${value}</strong><span>${label}</span>${detail?`<small>${detail}</small>`:""}</div>`;
}

function homeView(){
  const stats=state.health?.stats||{};
  const recent=[...state.corrections,...state.posts].sort((a,b)=>activityTime(b)-activityTime(a)).slice(0,4);
  return shell(`
    <section class="hero">
      <div class="hero-copy">
        <span class="eyebrow">Built for community health workers</span>
        <h1>Find the right resource.<br><span>Keep what your team learns.</span></h1>
        <p>ResourceBridge combines external resource discovery with the practical knowledge CHWs build in the field—without letting automation silently decide what is true.</p>
        <form class="hero-search" id="home-search-form"><input id="home-search" placeholder="Describe what someone needs…" aria-label="Describe a resource need" /><input id="home-zip" class="zip-input" inputmode="numeric" maxlength="5" placeholder="ZIP (optional)" aria-label="ZIP code" /><button class="primary" type="submit">Find resources</button></form>
        <div class="example-row"><span>Try:</span>${["emergency housing for a family","Spanish-speaking food support","help with utilities"].map(q=>`<button data-example-search="${esc(q)}">${esc(q)}</button>`).join("")}</div>
      </div>
      <div class="hero-trust-card">
        <div class="trust-orbit"><div class="orbit-center">RB</div><span class="orbit one">External<br>sources</span><span class="orbit two">CHW<br>knowledge</span><span class="orbit three">Freshness<br>checks</span><span class="orbit four">Community<br>verification</span></div>
        <p><strong>One resource record, four layers of context.</strong> ResourceBridge keeps source data, human knowledge, automated flags, and verified corrections visibly separate.</p>
      </div>
    </section>
    <section class="metrics-grid">
      ${metric(stats.resources||0,"resources remembered","grows as CHWs search")}
      ${metric(stats.network_posts||0,"CHW knowledge posts","practical field context")}
      ${metric(stats.flags||0,"open freshness flags","shown only when relevant")}
      ${metric(stats.verified_corrections||0,"community-verified updates",`${state.health?.consensus_threshold||3} confirmations required`)}
    </section>
    <section class="section-block">
      <div class="section-heading"><div><span class="eyebrow">The product loop</span><h2>Designed around what CHWs actually need to do</h2></div><button class="text-button" data-view="about">See why it works →</button></div>
      <div class="flow-grid">
        ${flowStep("01","Discover","Search ResourceBridge memory and an external resource catalog together.","⌕")}
        ${flowStep("02","Remember","Newly encountered resources enter the shared internal library automatically.","▤")}
        ${flowStep("03","Watch","Deterministic checks flag strong signs that stored information may be stale.","◌")}
        ${flowStep("04","Verify","CHWs discuss evidence; community consensus creates a trusted ResourceBridge override.","✓")}
      </div>
    </section>
    <section class="two-col">
      <div class="panel">
        <div class="panel-head"><div><span class="eyebrow">Recent community knowledge</span><h2>What CHWs are learning</h2></div><button class="text-button" data-view="network">Open network →</button></div>
        ${recent.length?recent.map(compactActivity).join(""):`<div class="empty"><strong>No shared knowledge yet.</strong><p>Search a resource and add the first practical note or correction.</p></div>`}
      </div>
      <div class="panel trust-panel">
        <span class="eyebrow">Trust is explicit</span><h2>Every piece of information tells you what it is.</h2>
        ${trustLegend()}
      </div>
    </section>`);
}

function flowStep(number,title,text,icon){ return `<article class="flow-step"><div class="flow-icon">${icon}</div><span>${number}</span><h3>${title}</h3><p>${text}</p></article>`; }
function trustLegend(){
  return `<div class="legend-list">
    <div><span class="legend-dot source"></span><p><strong>Source data</strong><small>Imported from a directory or seed dataset.</small></p></div>
    <div><span class="legend-dot knowledge"></span><p><strong>CHW knowledge</strong><small>Practical experience; useful context, not official truth.</small></p></div>
    <div><span class="legend-dot flag"></span><p><strong>Needs verification</strong><small>A deterministic freshness signal says “take another look.”</small></p></div>
    <div><span class="legend-dot verified"></span><p><strong>Community verified</strong><small>A structured correction reached the configured CHW consensus threshold.</small></p></div>
  </div>`;
}

function findView(){
  return shell(`
    <section class="page-heading">
      <div><span class="eyebrow">Unified resource discovery</span><h1>Find community resources</h1><p>Search the shared ResourceBridge library and the available external catalog at the same time.</p></div>
      <button class="secondary" data-run-freshness ${state.freshnessRunning?"disabled":""}>${state.freshnessRunning?"Checking…":"Run freshness check"}</button>
    </section>
    <section class="search-panel">
      <form id="resource-search-form"><div class="search-controls"><div class="search-box"><span>⌕</span><input id="resource-search" value="${esc(state.query)}" placeholder="e.g., emergency housing for a mother with children" /></div><label class="zip-search"><span>ZIP code</span><input id="resource-zip" inputmode="numeric" maxlength="5" value="${esc(state.zipCode)}" placeholder="60637" /></label><button class="primary" type="submit">Search</button></div></form>
      <div class="search-explain"><span>Searches:</span><b>ResourceBridge memory</b><i>+</i><b>local seed</b><i>+</i><b>Front Door Illinois when available</b><i>·</i><b>ZIP prioritizes nearby matches</b></div>
      <div class="example-row">${["emergency housing","food pantry Spanish","utility assistance","mental health support"].map(q=>`<button data-example-search="${esc(q)}">${esc(q)}</button>`).join("")}</div>
    </section>
    <div class="status-strip">
      <div><span class="status-dot ${state.external?.available===false?"amber":""}"></span><strong>${externalStatus()}</strong></div>
      <div><span>Freshness:</span> ${freshnessText()}</div>
    </div>
    ${state.searchError?`<div class="alert danger"><strong>Search unavailable.</strong> ${esc(state.searchError)}</div>`:""}
    ${searchResults()}`);
}

function externalStatus(){
  if(!state.external) return "External source loads on search";
  if(state.external.available===false){
    const error=clean(state.external.error).toLowerCase();
    if(error.includes("has not been downloaded yet")) return "Front Door will load when you search";
    return "External source unavailable — internal library still works";
  }
  const access=typeof state.external.access==="string"?state.external.access:(state.external.access?.mode||"sample");
  return `Front Door ${access||"sample"}${state.external.services?` · ${state.external.services} services cached`:""}`;
}
function freshnessText(){
  if(!state.freshness?.last_run) return "not run yet";
  return `${fmt(state.freshness.last_run)} · ${state.freshness.unresolved_flags||0} unresolved`;
}

function searchResults(){
  if(state.searchLoading) return `<div class="results-loading">${[1,2,3].map(()=>`<div class="skeleton-card"></div>`).join("")}</div>`;
  if(!state.query && !state.zipCode) return `<section class="empty-state-large"><div class="empty-icon">⌕</div><h2>Start with the need, location, or both.</h2><p>Describe the support needed and optionally add a ZIP code. ResourceBridge searches new external resources and what your CHW team has already learned.</p></section>`;
  if(!state.discovery.length) return `<section class="empty-state-large"><div class="empty-icon">∅</div><h2>No matching resources found.</h2><p>Try broader language or remove an eligibility detail. A match is never treated as an eligibility guarantee.</p></section>`;
  const locationNote=state.zipCode?`ZIP ${esc(state.zipCode)} prioritized${state.searchLocation?.resolved?" · distances are approximate from ZIP centroid":" · exact ZIP matches prioritized when distance is unavailable"}`:"Results may combine source data with saved CHW knowledge.";
  return `<section class="results-section"><div class="results-head"><div><span class="eyebrow">${state.discovery.length} results</span><h2>Best available matches</h2></div><div class="match-note">${locationNote}</div></div><div class="resource-grid">${state.discovery.map(resourceResultCard).join("")}</div></section>`;
}

function decorateDiscovery(item){
  const stored=resourceById(item.resource_id);
  return {...item, stored};
}
function resourceResultCard(raw){
  const r=decorateDiscovery(raw); const stored=r.stored; const counts=stored?.counts||r.counts||{}; const flags=stored?.flags||r.flags||[];
  const known=r.was_known || (stored && !r.new_to_resourcebridge);
  const source=sourceLabel(r.external_source);
  const service=r.service_name||r.category||"Community service";
  return `<article class="resource-card ${flags.length?"flagged":""}">
    <div class="resource-card-top"><span class="pill ${sourceTone(r.external_source)}">${esc(source)}</span>${r.new_to_resourcebridge?`<span class="pill green">New to ResourceBridge</span>`:known?`<span class="pill purple">In shared library</span>`:""}</div>
    <h3>${esc(r.organization)}</h3>
    <p class="service-line">${esc(service)}${r.neighborhood?` · ${esc(r.neighborhood)}`:""}</p>
    ${(r.postal_code||r.distance_miles!=null)?`<div class="location-row">${r.postal_code?`<span>ZIP ${esc(r.postal_code)}</span>`:""}${r.zip_match?`<span class="nearby exact">Same ZIP</span>`:r.distance_miles!=null&&state.zipCode?`<span class="nearby">~${Number(r.distance_miles).toFixed(1)} mi from ${esc(state.zipCode)}</span>`:""}</div>`:""}
    <p class="resource-desc">${esc(short(r.services||r.eligibility||"Review program details and confirm current eligibility."))}</p>
    ${flags.length?`<div class="inline-warning">⚠ ${flags.length} detail${flags.length===1?"":"s"} may need verification</div>`:""}
    <div class="memory-row">
      <span>${counts.notes||0} CHW note${counts.notes===1?"":"s"}</span><span>${counts.corrections||0} correction${counts.corrections===1?"":"s"}</span>${counts.verified?`<span class="verified-text">✓ ${counts.verified} verified</span>`:""}
    </div>
    ${r.knowledge_match?`<div class="why-match">✦ Also matched knowledge previously shared by CHWs</div>`:""}
    <button class="card-action" data-open-resource="${r.resource_id}">Review resource <span>→</span></button>
  </article>`;
}

function libraryView(){
  const q=state.librarySearch.toLowerCase().trim();
  const list=state.resources.filter(r=>!q || [r.organization,r.service_name,r.category,r.services,r.eligibility,r.languages,r.neighborhood,r.postal_code].join(" ").toLowerCase().includes(q));
  return shell(`
    <section class="page-heading"><div><span class="eyebrow">ResourceBridge memory</span><h1>Shared resource library</h1><p>Every resource your team encounters is remembered here with its accumulated notes, corrections, and trust history.</p></div><button class="primary" data-view="find">+ Discover resources</button></section>
    <div class="library-toolbar"><div class="search-box small"><span>⌕</span><input id="library-search" value="${esc(state.librarySearch)}" placeholder="Search remembered resources" /></div><span>${list.length} of ${state.resources.length} remembered</span></div>
    ${list.length?`<div class="library-list">${list.map(libraryRow).join("")}</div>`:`<section class="empty-state-large"><div class="empty-icon">▤</div><h2>Your shared library is still empty.</h2><p>Resources enter ResourceBridge automatically when they appear in a CHW search.</p><button class="primary" data-view="find">Find the first resource</button></section>`}`);
}

function libraryRow(r){
  const counts=r.counts||{}; const flags=r.flags||[]; const overrides=Object.keys(r.overrides||{}).length;
  return `<article class="library-row" data-open-resource="${r.id}">
    <div class="library-main"><div class="source-square ${sourceTone(r.external_source)}">${r.external_source==="front_door"?"FD":"RB"}</div><div><h3>${esc(r.organization)}</h3><p>${esc(r.service_name||r.category||"Community resource")} · ${esc(r.neighborhood||r.address||"Location not stated")}</p></div></div>
    <div class="library-signals">${flags.length?`<span class="signal warning">⚠ Verify ${flags.length}</span>`:""}${overrides?`<span class="signal verified">✓ ${overrides} verified field${overrides===1?"":"s"}</span>`:""}<span class="signal">${counts.notes||0} notes</span><span class="signal">${counts.corrections||0} corrections</span></div>
    <span class="row-arrow">→</span>
  </article>`;
}

function resourceView(){
  const r=selectedResource();
  if(!r) return shell(`<section class="empty-state-large"><h2>Resource not found.</h2><button class="primary" data-view="library">Return to library</button></section>`);
  const flags=r.flags||[]; const overrides=r.overrides||{}; const counts=r.counts||{};
  const resourceCorrections=state.corrections.filter(c=>Number(c.resource_id)===Number(r.id));
  const resourcePosts=state.posts.filter(p=>Number(p.resource_id)===Number(r.id));
  return shell(`
    <button class="back-link" data-view="find">← Back to resource search</button>
    <section class="resource-hero">
      <div class="resource-title"><div class="source-square large ${sourceTone(r.external_source)}">${r.external_source==="front_door"?"FD":"RB"}</div><div><div class="resource-kicker"><span class="pill ${sourceTone(r.external_source)}">${esc(sourceLabel(r.external_source))}</span>${Object.keys(overrides).length?`<span class="pill green">✓ Community verified</span>`:""}${flags.length?`<span class="pill amber">⚠ Needs verification</span>`:""}</div><h1>${esc(r.organization)}</h1><p>${esc(r.service_name||r.category||"Community resource")}${r.neighborhood?` · ${esc(r.neighborhood)}`:""}</p></div></div>
      <div class="resource-actions"><button class="secondary" data-add-note="${r.id}">+ Add CHW knowledge</button><button class="primary" data-suggest-correction="${r.id}">Suggest correction</button></div>
    </section>
    ${flags.length?`<div class="alert warning"><div><strong>Some information may be outdated.</strong><p>ResourceBridge detected ${flags.length} strong freshness signal${flags.length===1?"":"s"}. Review the evidence before relying on the affected field.</p></div><button class="text-button" data-resource-tab="freshness">Review flags →</button></div>`:""}
    <section class="trust-summary">
      <div><span>ResourceBridge memory</span><strong>${fmt(r.first_seen,true)}</strong><small>First encountered</small></div>
      <div><span>CHW knowledge</span><strong>${counts.notes||0}</strong><small>Practical notes/questions</small></div>
      <div><span>Corrections</span><strong>${counts.corrections||0}</strong><small>${counts.verified||0} community verified</small></div>
      <div><span>Freshness</span><strong>${flags.length?`${flags.length} flag${flags.length===1?"":"s"}`:"Clear"}</strong><small>${r.last_external_checked?`Checked ${fmt(r.last_external_checked,true)}`:"Not checked yet"}</small></div>
    </section>
    <div class="tabs">${[["overview","Overview"],["knowledge",`CHW Knowledge (${resourcePosts.length})`],["corrections",`Corrections (${resourceCorrections.length})`],["freshness",`Freshness & history${flags.length?` (${flags.length})`:""}`]].map(([id,label])=>`<button class="${state.selectedTab===id?"active":""}" data-resource-tab="${id}">${label}</button>`).join("")}</div>
    ${resourceTabContent(r, resourcePosts, resourceCorrections)}
  `);
}

function resourceTabContent(r, posts, corrections){
  if(state.selectedTab==="knowledge") return knowledgeTab(r,posts);
  if(state.selectedTab==="corrections") return correctionsTab(r,corrections);
  if(state.selectedTab==="freshness") return freshnessTab(r);
  return overviewTab(r);
}

function overviewTab(r){
  const fields=["phone","hours","address","postal_code","website","languages","eligibility","external_status"];
  const intake=intakeAttention(r);
  return `<div class="detail-grid">
    <section class="panel detail-panel"><div class="panel-head"><div><span class="eyebrow">Current ResourceBridge view</span><h2>Resource information</h2></div><span class="pill gray">Source + verified overlays</span></div>
      <div class="field-list">${fields.map(field=>fieldRow(r,field)).join("")}</div>
      ${r.application_process?`<div class="long-field ${intake.active?"attention-long-field":""}"><span>Application / intake</span>${intake.active?`<small class="field-attention-cue">⚠ Availability or intake information needs verification</small>`:""}<p>${esc(r.application_process)}</p></div>`:""}
      ${r.services?`<div class="long-field"><span>Services</span><p>${esc(r.services)}</p></div>`:""}
    </section>
    <aside class="panel context-panel">
      <span class="eyebrow">Why this is trustworthy</span><h2>Provenance stays visible</h2>
      <p>ResourceBridge never hides the original source when CHWs verify something different.</p>
      <div class="provenance-list"><div><span>External source</span><strong>${esc(sourceLabel(r.external_source))}</strong></div><div><span>Source last assured</span><strong>${esc(r.external_assured_date||"Not provided")}</strong></div><div><span>Last encountered</span><strong>${fmt(r.last_seen,true)}</strong></div></div>
      ${safeLink(r.source_url||r.website)?`<a class="external-link" href="${esc(safeLink(r.source_url||r.website))}" target="_blank" rel="noreferrer">Open source page ↗</a>`:""}
      <div class="context-rule"><strong>Match ≠ eligibility guarantee.</strong><p>CHWs should confirm program-specific eligibility and availability before referral.</p></div>
    </aside>
  </div>`;
}

function fieldRow(r, field){
  const value=effective(r,field); const override=r.overrides?.[field]; const original=clean(r[field]);
  const attention=fieldAttention(r,field);
  const cues=[];
  if(attention.flags.length) cues.push("⚠ Needs verification");
  if(attention.proposed.length) cues.push("↺ Correction proposed");
  const proposal=attention.proposed[0];
  return `<div class="field-row ${override?"verified-field":""} ${attention.active?"attention-field":""}"><div><span>${FIELD_LABELS[field]}</span>${attention.active?`<small class="field-attention-cue">${cues.join(" · ")}</small>`:override?`<small>✓ Community verified ${fmt(override.approved_at,true)}</small>`:`<small>${esc(sourceLabel(r.external_source))}</small>`}</div><div class="field-value">${field==="website"&&safeLink(value)?`<a href="${esc(safeLink(value))}" target="_blank" rel="noreferrer">${esc(value)} ↗</a>`:esc(value||"Not stated")}${override&&original&&original!==value?`<small class="original-value">Source currently lists: ${esc(original)}</small>`:""}${attention.flags.length?`<small class="attention-reason">${esc(short(attention.flags[0].reason,145))}</small>`:""}${proposal?`<small class="proposed-value">Proposed: ${esc(proposal.suggestedValue||"See correction")}</small>`:""}</div></div>`;
}

function knowledgeTab(r,posts){
  return `<section class="panel"><div class="panel-head"><div><span class="eyebrow">Human knowledge layer</span><h2>What CHWs have learned</h2><p>Experience-based context stays separate from trusted resource fields.</p></div><button class="primary" data-add-note="${r.id}">+ Share knowledge</button></div>
  ${posts.length?`<div class="feed">${posts.map(postCard).join("")}</div>`:`<div class="empty"><strong>No CHW knowledge yet.</strong><p>Share a practical tip, observation, or question that could help the next referral.</p></div>`}</section>`;
}
function correctionsTab(r,corrections){
  return `<section class="panel"><div class="panel-head"><div><span class="eyebrow">Structured verification</span><h2>Proposed corrections</h2><p>Corrections require evidence and distinct CHW confirmations before becoming a ResourceBridge override.</p></div><button class="primary" data-suggest-correction="${r.id}">Suggest correction</button></div>
  ${corrections.length?`<div class="feed">${corrections.map(correctionCard).join("")}</div>`:`<div class="empty"><strong>No corrections proposed.</strong><p>If a factual field appears wrong, submit a correction rather than editing the source data directly.</p></div>`}</section>`;
}
function freshnessTab(r){
  const flags=r.flags||[];
  return `<div class="detail-grid">
    <section class="panel"><div class="panel-head"><div><span class="eyebrow">Deterministic monitoring</span><h2>Freshness signals</h2><p>Structured source differences and official-site text signals identify information worth checking. The affected Overview field is highlighted, but nothing is changed automatically.</p></div><button class="secondary" data-run-freshness ${state.freshnessRunning?"disabled":""}>${state.freshnessRunning?"Checking…":"Check stored resources now"}</button></div>
      <div class="freshness-checklist"><span>Each official-site scan checks</span><b>reachability</b><b>redirects</b><b>phone</b><b>address + ZIP</b><b>hours</b><b>languages</b><b>eligibility</b><b>intake / waitlist</b><b>program status</b></div>
      ${flags.length?flags.map(flagCard).join(""):`<div class="clear-state"><span>✓</span><div><strong>No unresolved freshness flags</strong><p>No strong deterministic signal currently suggests this resource needs review.</p></div></div>`}
    </section>
    <aside class="panel"><span class="eyebrow">Verified history</span><h2>ResourceBridge changes</h2><p>When community consensus changes a trusted field, both the old and new value remain traceable.</p>${state.resourceHistory.length?`<div class="history-list">${state.resourceHistory.map(historyRow).join("")}</div>`:`<div class="empty compact"><strong>No verified overrides yet.</strong><p>The original source remains the displayed value.</p></div>`}</aside>
  </div>`;
}
function flagCard(f){ return `<article class="flag-card"><div class="flag-icon">!</div><div><div class="flag-top"><strong>${esc(FIELD_LABELS[f.field]||f.field||"Resource information")}</strong><span>${fmt(f.detected_at,true)}</span></div><p>${esc(f.reason)}</p>${f.stored_value||f.observed_value?`<div class="comparison"><span><small>Stored</small>${esc(f.stored_value||"Not stated")}</span><span>→</span><span><small>Observed</small>${esc(f.observed_value||"Not stated")}</span></div>`:""}${safeLink(f.evidence_url)?`<a class="evidence-link" href="${esc(safeLink(f.evidence_url))}" target="_blank" rel="noreferrer">Review evidence ↗</a>`:""}</div></article>`; }
function historyRow(h){ return `<div class="history-row"><span class="history-check">✓</span><div><strong>${esc(FIELD_LABELS[h.field]||h.field)}</strong><p>${esc(h.previous_value||"Not stated")} → <b>${esc(h.approved_value)}</b></p><small>${esc(h.author||"CHW community")} · ${fmt(h.changed_at)}</small></div></div>`; }

function networkView(){
  const combined=[...state.posts.map(p=>({...p,_type:"post"})),...state.corrections.map(c=>({...c,_type:"correction"}))].sort((a,b)=>activityTime(b)-activityTime(a));
  const q=state.networkSearch.toLowerCase().trim();
  const visible=combined.filter(item=>{
    const filterOK=state.networkFilter==="All" || (state.networkFilter==="Notes"&&item._type==="post"&&item.category==="Note") || (state.networkFilter==="Questions"&&item._type==="post"&&item.category==="Question") || (state.networkFilter==="Corrections"&&item._type==="correction") || (state.networkFilter==="Verified"&&item._type==="correction"&&item.status==="verified");
    const searchOK=!q || [item.organization,item.message,item.issueType,item.category,item.author,item.evidence].join(" ").toLowerCase().includes(q);
    return filterOK&&searchOK;
  });
  return shell(`
    <section class="page-heading"><div><span class="eyebrow">Shared CHW knowledge</span><h1>CHW Network</h1><p>A resource-linked forum for practical notes, questions, evidence, and structured corrections.</p></div><button class="primary" data-add-note="">+ Share knowledge</button></section>
    <div class="network-toolbar"><div class="search-box small"><span>⌕</span><input id="network-search" value="${esc(state.networkSearch)}" placeholder="Search notes, corrections, or resources" /></div><div class="filter-tabs">${["All","Notes","Questions","Corrections","Verified"].map(x=>`<button class="${state.networkFilter===x?"active":""}" data-network-filter="${x}">${x}</button>`).join("")}</div></div>
    <div class="network-layout"><section class="panel feed-panel">${visible.length?`<div class="feed">${visible.map(item=>item._type==="correction"?correctionCard(item):postCard(item)).join("")}</div>`:`<div class="empty"><strong>No posts match this view.</strong><p>Try another filter or share the first resource-linked update.</p></div>`}</section><aside class="panel network-guide"><span class="eyebrow">What belongs here?</span><h2>Useful knowledge, with clear boundaries.</h2><div class="guide-item"><b>Note</b><p>Practical experience that may help another CHW.</p></div><div class="guide-item"><b>Question</b><p>Something another CHW may know about a resource.</p></div><div class="guide-item"><b>Correction</b><p>A factual change to a structured field, backed by evidence.</p></div><div class="context-rule"><strong>No ratings. No client-identifying information.</strong><p>ResourceBridge captures factual, resource-specific knowledge—not public reviews of organizations.</p></div></aside></div>`);
}

function compactActivity(item){
  const isCorrection=item.kind==="correction" || item.issueType;
  return `<button class="compact-activity" ${item.resource_id?`data-open-resource="${item.resource_id}"`:`data-view="network"`}><span class="activity-icon ${isCorrection?"correction":"note"}">${isCorrection?"↺":"✦"}</span><div><strong>${esc(item.organization|| (isCorrection?"Resource correction":"CHW Network"))}</strong><p>${esc(short(item.message,90))}</p><small>${esc(item.author)} · ${fmt(item.timestamp)}</small></div><span>→</span></button>`;
}

function postCard(p){
  return `<article class="post-card"><div class="post-head"><span class="avatar small">${esc(p.initials||initials(p.author))}</span><div><strong>${esc(p.author)}</strong><small>${esc(p.location||"CHW Network")} · ${fmt(p.timestamp)}</small></div><span class="pill purple">${esc(p.category||"Note")}</span></div>${p.organization?`<button class="resource-link" data-open-resource="${p.resource_id}">▤ ${esc(p.organization)} →</button>`:""}<p class="post-message">${esc(p.message)}</p>${p.noteFlagged?`<div class="inline-warning">⚠ This time-sensitive note may need reconfirmation</div>`:""}<div class="post-actions"><button class="vote-button ${p.userVoted?"active":""}" data-vote-post="${p.id}">♡ Helpful <b>${p.votes||0}</b></button><button class="text-button" data-comment-post="${p.id}">Comment ${p.comments?.length?`(${p.comments.length})`:""}</button></div>${renderComments(p.comments||[])}</article>`;
}

function correctionCard(c){
  const threshold=c.consensusThreshold||state.health?.consensus_threshold||3; const verified=c.status==="verified";
  return `<article class="post-card correction-card ${verified?"is-verified":""}"><div class="post-head"><span class="avatar small">${esc(c.initials||initials(c.author))}</span><div><strong>${esc(c.author)}</strong><small>${esc(c.location||"CHW Network")} · ${fmt(c.timestamp)}</small></div><span class="pill ${verified?"green":"amber"}">${verified?"✓ Community verified":"Proposed correction"}</span></div>${c.organization?`<button class="resource-link" data-open-resource="${c.resource_id}">▤ ${esc(c.organization)} →</button>`:""}<div class="correction-field"><span>${esc(c.issueType||"Resource information")}</span>${c.currentValue||c.suggestedValue?`<div><small>Current</small><b>${esc(c.currentValue||"Not stated")}</b><span>→</span><small>Suggested</small><b>${esc(c.suggestedValue||"See note")}</b></div>`:""}</div><p class="post-message">${esc(c.message)}</p>${c.evidence?`<div class="evidence-box"><strong>Evidence</strong><p>${esc(c.evidence)}</p></div>`:""}<div class="consensus"><div class="consensus-bar"><span style="width:${Math.min(100,(c.votes||0)/threshold*100)}%"></span></div><div><strong>${c.votes||0} of ${threshold}</strong><span>${verified?"Consensus reached — ResourceBridge override applied":`${Math.max(0,threshold-(c.votes||0))} more distinct CHW confirmation${Math.max(0,threshold-(c.votes||0))===1?"":"s"} needed`}</span></div></div><div class="post-actions">${verified?"":`<button class="confirm-button ${c.userVoted?"active":""}" data-vote-correction="${c.id}">${c.userVoted?"✓ Confirmed":"Confirm correction"}</button>`}<button class="text-button" data-comment-correction="${c.id}">Discuss ${c.comments?.length?`(${c.comments.length})`:""}</button></div>${renderComments(c.comments||[])}</article>`;
}
function renderComments(comments){ if(!comments.length) return ""; return `<div class="comment-list">${comments.map(c=>`<div class="comment"><strong>${esc(c.author)}</strong><span>${fmt(c.created_at)}</span><p>${esc(c.message)}</p></div>`).join("")}</div>`; }

function aboutView(){
  return shell(`
    <section class="about-hero"><span class="eyebrow">Why ResourceBridge is built this way</span><h1>A resource directory is useful.<br>A shared memory is better.</h1><p>Community resource information changes constantly, and the people who discover those changes are often the CHWs actually using the resources. ResourceBridge is designed to preserve that knowledge without confusing experience, automation, and verified facts.</p></section>
    <section class="architecture-stack">
      <article><div class="arch-num">1</div><div><span class="eyebrow">Discovery layer</span><h2>Start with existing resource ecosystems.</h2><p>ResourceBridge does not try to rebuild every community resource from scratch. It searches an external catalog and the growing internal library together, then remembers what CHWs actually encounter.</p></div><div class="arch-visual"><span>External catalog</span><b>+</b><span>ResourceBridge memory</span><b>→</b><strong>Relevant resources</strong></div></article>
      <article><div class="arch-num">2</div><div><span class="eyebrow">Knowledge layer</span><h2>Separate practical experience from official data.</h2><p>CHWs can add notes and questions without those observations silently becoming eligibility rules or official program facts.</p></div><div class="arch-visual"><span>Source facts</span><span>CHW notes</span><span>Questions</span></div></article>
      <article><div class="arch-num">3</div><div><span class="eyebrow">Freshness layer</span><h2>Use automation to find uncertainty—not declare truth.</h2><p>Weekly deterministic checks compare structured source fields and recency, then inspect the known official website end-to-end: reachability, redirects, published phone numbers, structured address/ZIP, hours, language access, eligibility, intake/waitlist language, program status, and page-change evidence. A strong discrepancy creates a field-specific flag; it never rewrites trusted data.</p></div><div class="arch-visual"><span>Stored record</span><b>↔</b><span>Current evidence</span><b>→</b><strong>Flag, not edit</strong></div></article>
      <article><div class="arch-num">4</div><div><span class="eyebrow">Verification layer</span><h2>Let CHWs close the loop.</h2><p>A structured correction contains the current value, proposed value, and supporting evidence. Distinct CHW confirmations move it toward consensus. At the configured threshold, ResourceBridge creates a verified override while preserving the original source value and history.</p></div><div class="arch-visual"><span>Correction</span><b>→</b><span>${state.health?.consensus_threshold||3} CHW confirmations</span><b>→</b><strong>Verified override</strong></div></article>
    </section>
    <section class="two-col about-bottom"><div class="panel"><span class="eyebrow">What we intentionally removed</span><h2>Features that distracted from the actual problem</h2><ul class="decision-list"><li><b>Patient charts and encounter logs</b><span>ResourceBridge is not an EHR and should not create unnecessary PHI/security scope.</span></li><li><b>Calendar and caseload management</b><span>These do not solve resource discovery or freshness and duplicate tools CHWs already use.</span></li><li><b>Public ratings or trust scores</b><span>Community knowledge should be factual and resource-specific, not Yelp-style reputation.</span></li><li><b>Automatic AI edits</b><span>No model should decide that a community resource fact is true and silently replace it.</span></li></ul></div><div class="panel"><span class="eyebrow">Product guardrails</span><h2>Designed for useful uncertainty</h2>${trustLegend()}<div class="context-rule"><strong>Production note</strong><p>This prototype uses selectable demo CHW identities to demonstrate distinct confirmations. A deployed version should use real authentication, permissions, audit logs, and client-approved governance.</p></div></div></section>`);
}

function renderModal(){
  if(!state.modal) return "";
  if(state.modal==="note") return noteModal();
  if(state.modal==="correction") return correctionModal();
  if(state.modal==="comment") return commentModal();
  return "";
}
function resourceOptions(selected=""){
  return `<option value="">No resource / general network post</option>${state.resources.map(r=>`<option value="${r.id}" ${String(r.id)===String(selected)?"selected":""}>${esc(r.organization)}</option>`).join("")}`;
}
function noteModal(){
  const rid=state.modalResourceId||"";
  return `<div class="modal-backdrop"><div class="modal"><button class="modal-close" data-close-modal>×</button><span class="eyebrow">CHW knowledge</span><h2>Share what you learned</h2><p>Use this for practical context or a question. If a factual field is wrong, use <b>Suggest correction</b> instead.</p><label>Resource<select id="note-resource">${resourceOptions(rid)}</select></label><div class="form-row"><label>Post type<select id="note-category"><option>Note</option><option>Question</option></select></label><label>Neighborhood / location<input id="note-location" placeholder="Optional" /></label></div><label>Knowledge to share<textarea id="note-message" placeholder="Keep it factual, useful, and free of client-identifying information."></textarea></label><div class="modal-actions"><button class="secondary" data-close-modal>Cancel</button><button class="primary" data-save-note>Share with CHW Network</button></div></div></div>`;
}
function correctionModal(){
  const r=resourceById(state.modalResourceId); if(!r) return "";
  return `<div class="modal-backdrop"><div class="modal wide"><button class="modal-close" data-close-modal>×</button><span class="eyebrow">Structured resource correction</span><h2>Suggest a correction</h2><p><b>${esc(r.organization)}</b> · The correction will enter CHW Network for discussion and distinct confirmations.</p><div class="form-row"><label>Field<select id="correction-field">${["Phone number","Hours","Address","ZIP code","Website","Languages","Eligibility requirements","Program status","Other"].map(x=>`<option>${x}</option>`).join("")}</select></label><label>Current ResourceBridge value<input id="correction-current" readonly /></label></div><label>Suggested value<input id="correction-suggested" placeholder="What should this field say?" /></label><label>Evidence<textarea id="correction-evidence" placeholder="How did you verify this? Example: Called intake staff on Nov 16; official program page also lists the new number."></textarea></label><label>Context<textarea id="correction-message" placeholder="Briefly explain what changed and anything another CHW should know."></textarea></label><div class="consensus-explainer"><span>1</span><p>You propose</p><i>→</i><span>2</span><p>Other CHWs review evidence</p><i>→</i><span>3</span><p>${state.health?.consensus_threshold||3} confirmations</p><i>→</i><span>✓</span><p>Verified override</p></div><div class="modal-actions"><button class="secondary" data-close-modal>Cancel</button><button class="primary" data-save-correction>Post correction</button></div></div></div>`;
}
function commentModal(){
  return `<div class="modal-backdrop"><div class="modal"><button class="modal-close" data-close-modal>×</button><span class="eyebrow">CHW discussion</span><h2>Add a comment</h2><p>Use comments to confirm evidence, add nuance, or explain why you disagree.</p><label>Comment<textarea id="comment-message" placeholder="Add factual, non-identifying context…"></textarea></label><div class="modal-actions"><button class="secondary" data-close-modal>Cancel</button><button class="primary" data-save-comment>Add comment</button></div></div></div>`;
}

function render(){
  const content={home:homeView,find:findView,library:libraryView,network:networkView,resource:resourceView,about:aboutView}[state.view]?.() || homeView();
  $("#app").innerHTML=content;
  bind();
  if(state.modal==="correction") updateCorrectionCurrentValue();
}

function bind(){
  $$('[data-view]').forEach(el=>el.onclick=async()=>{state.view=el.dataset.view; state.modal=null; render();});
  $("#identity-select")?.addEventListener("change", async e=>{
    state.currentUser=USERS.find(u=>u.id===e.target.value)||USERS[0];
    localStorage.setItem("resourcebridge-demo-user",state.currentUser.id);
    await refreshCore(); if(state.selectedResourceId) await refreshResourceHistory(state.selectedResourceId); render();
  });
  $("#home-search-form")?.addEventListener("submit",e=>{e.preventDefault(); state.view="find"; runSearch($("#home-search").value,$("#home-zip")?.value||"");});
  $("#resource-search-form")?.addEventListener("submit",e=>{e.preventDefault(); runSearch($("#resource-search").value,$("#resource-zip")?.value||"");});
  $$('[data-example-search]').forEach(el=>el.onclick=()=>{state.view="find"; runSearch(el.dataset.exampleSearch);});
  $$('[data-open-resource]').forEach(el=>el.onclick=()=>openResource(el.dataset.openResource));
  $$('[data-resource-tab]').forEach(el=>el.onclick=()=>{state.selectedTab=el.dataset.resourceTab; render();});
  $("#library-search")?.addEventListener("input",e=>{state.librarySearch=e.target.value; render(); requestAnimationFrame(()=>{$("#library-search")?.focus(); $("#library-search")?.setSelectionRange(state.librarySearch.length,state.librarySearch.length);});});
  $("#network-search")?.addEventListener("input",e=>{state.networkSearch=e.target.value; render(); requestAnimationFrame(()=>{$("#network-search")?.focus(); $("#network-search")?.setSelectionRange(state.networkSearch.length,state.networkSearch.length);});});
  $$('[data-network-filter]').forEach(el=>el.onclick=()=>{state.networkFilter=el.dataset.networkFilter; render();});
  $$('[data-add-note]').forEach(el=>el.onclick=()=>{state.modal="note"; state.modalResourceId=el.dataset.addNote||""; render();});
  $$('[data-suggest-correction]').forEach(el=>el.onclick=()=>{state.modal="correction"; state.modalResourceId=Number(el.dataset.suggestCorrection); render();});
  $$('[data-close-modal]').forEach(el=>el.onclick=()=>{state.modal=null; state.commentTarget=null; render();});
  $("#correction-field")?.addEventListener("change",updateCorrectionCurrentValue);
  $('[data-save-note]')?.addEventListener("click",saveNote);
  $('[data-save-correction]')?.addEventListener("click",saveCorrection);
  $$('[data-comment-post]').forEach(el=>el.onclick=()=>{state.commentTarget={type:"post",id:Number(el.dataset.commentPost)}; state.modal="comment"; render();});
  $$('[data-comment-correction]').forEach(el=>el.onclick=()=>{state.commentTarget={type:"correction",id:Number(el.dataset.commentCorrection)}; state.modal="comment"; render();});
  $('[data-save-comment]')?.addEventListener("click",saveComment);
  $$('[data-vote-post]').forEach(el=>el.onclick=()=>votePost(Number(el.dataset.votePost)));
  $$('[data-vote-correction]').forEach(el=>el.onclick=()=>voteCorrection(Number(el.dataset.voteCorrection)));
  $$('[data-run-freshness]').forEach(el=>el.onclick=runFreshness);
}

function updateCorrectionCurrentValue(){
  const r=resourceById(state.modalResourceId); const field=ISSUE_TO_FIELD[$("#correction-field")?.value]; const input=$("#correction-current");
  if(input) input.value=field?effective(r,field):"Describe in context below";
}

async function saveNote(){
  const rid=$("#note-resource")?.value; const resource=rid?resourceById(rid):null; const message=clean($("#note-message")?.value);
  if(!message){toast("Add a note or question before posting.","error");return;}
  try{
    await api("/api/network/posts",{method:"POST",body:JSON.stringify({resource:resource?resourcePayload(resource):null,author:currentUser().name,initials:currentUser().initials,user_identifier:currentUser().id,category:$("#note-category").value,message,location:$("#note-location").value})});
    state.modal=null; await refreshCore(); toast("Shared with CHW Network."); render();
  }catch(e){toast(e.message,"error");}
}

async function saveCorrection(){
  const r=resourceById(state.modalResourceId); if(!r) return;
  const issue=$("#correction-field").value; const suggested=clean($("#correction-suggested").value); const evidence=clean($("#correction-evidence").value); const context=clean($("#correction-message").value);
  if(!suggested){toast("Enter the corrected value.","error");return;}
  if(!evidence){toast("Add evidence so other CHWs can evaluate the correction.","error");return;}
  try{
    await api("/api/corrections",{method:"POST",body:JSON.stringify({resource:resourcePayload(r),author:currentUser().name,initials:currentUser().initials,user_identifier:currentUser().id,issue_type:issue,current_value:$("#correction-current").value,suggested_value:suggested,evidence,message:context||`${issue} may need to be updated.`,location:r.neighborhood||""})});
    state.modal=null; await refreshCore(); await refreshResourceHistory(r.id); state.selectedTab="corrections"; toast("Correction posted for CHW review."); render();
  }catch(e){toast(e.message,"error");}
}

async function saveComment(){
  const message=clean($("#comment-message")?.value); if(!message){toast("Write a comment first.","error");return;}
  const t=state.commentTarget; if(!t) return;
  const path=t.type==="correction"?`/api/corrections/${t.id}/comments`:`/api/network/posts/${t.id}/comments`;
  try{ await api(path,{method:"POST",body:JSON.stringify({author:currentUser().name,message})}); state.modal=null; state.commentTarget=null; await refreshCore(); toast("Comment added."); render(); }catch(e){toast(e.message,"error");}
}
async function votePost(id){
  try{await api(`/api/network/posts/${id}/vote`,{method:"POST",body:JSON.stringify({user_identifier:currentUser().id})}); await refreshCore(); render();}catch(e){toast(e.message,"error");}
}
async function voteCorrection(id){
  try{
    const result=await api(`/api/corrections/${id}/vote`,{method:"POST",body:JSON.stringify({user_identifier:currentUser().id})});
    await refreshCore(); if(state.selectedResourceId) await refreshResourceHistory(state.selectedResourceId);
    if(result.status==="verified") toast("Consensus reached — ResourceBridge verified the correction.");
    render();
  }catch(e){toast(e.message,"error");}
}
async function runFreshness(){
  if(state.freshnessRunning) return;
  state.freshnessRunning=true; render();
  try{const result=await api("/api/freshness/run",{method:"POST",body:"{}"}); await refreshCore(); if(state.selectedResourceId) await refreshResourceHistory(state.selectedResourceId); toast(`Freshness check complete: ${result.flags_created||0} new flag${Number(result.flags_created||0)===1?"":"s"}.`);}
  catch(e){toast(`Freshness check could not complete: ${e.message}`,"error");}
  finally{state.freshnessRunning=false; render();}
}

async function init(){
  await refreshCore();
  render();
}
init();
