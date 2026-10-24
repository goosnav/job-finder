"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const human = (v) => String(v || "unknown").replace(/_/g," ");
  const clamp = (n,min,max) => Math.max(min,Math.min(max,Number(n)||0));
  const words = (v) => String(v||"").trim().split(/\s+/).filter(Boolean).length;
  const date = (v) => { if(!v)return "—";const d=new Date(v);return isNaN(d)?"—":d.toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"}); };
  const url = (v) => { try { const u=new URL(v); return ["http:","https:"].includes(u.protocol)&&!u.username&&!u.password?u.href:""; } catch {return "";} };
  const host = (v) => {try{return new URL(v).hostname.replace(/^www\./,"");}catch{return "";}};
  const link = (v,label,cls="") => url(v)?`<a class="${esc(cls)}" href="${esc(url(v))}" target="_blank" rel="noopener noreferrer">${esc(label||host(v))} ↗</a>`:"";
  const tone = (s) => ["eligible","open","approved","verified","completed","offer","strong","matched"].includes(s)?"good":["blocked","closed","failed","rejected","suppressed","invalid","conflict","bounced"].includes(s)?"bad":["review","company_lead","running","paused","budget_exhausted","drafted","partial"].includes(s)?"accent":"";
  const badge = (s,t=undefined) => `<span class="badge ${esc(t===undefined?tone(s):t)}">${esc(human(s))}</span>`;
  const icon = (n) => {const paths={search:'<circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/>',list:'<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M7 7h6M7 10h6M7 13h4"/>',upload:'<path d="M10 13V3m-4 4 4-4 4 4M3 13v4h14v-4"/>',close:'<path d="m5 5 10 10M15 5 5 15"/>',check:'<path d="m4 10 4 4 8-8"/>',arrow:'<path d="M3 10h13m-5-5 5 5-5 5"/>',mail:'<rect x="2" y="4" width="16" height="12" rx="2"/><path d="m3 5 7 6 7-6"/>',export:'<path d="M10 2v10m-4-4 4 4 4-4M3 13v4h14v-4"/>'};return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[n]||paths.list}</svg>`;};
  const defaults={name:"",objective:"",demo:false,candidate:{name:"",email:"",location:"",resume_text:"",skills:[],years_experience:null,work_authorization:"",needs_sponsorship:null,clearance:"",links:[]},preferences:{roles:[],locations:[],industries:[],work_modes:[],strict_work_mode:false,salary_min:null,salary_currency:"USD",employment_types:[],seniority:"",must_have:"",exclude:"",relocation:"unknown",include_company_leads:true,max_post_age_days:45},settings:{provider:"codex",model:"",target_results:25,min_fit:55,discovery_queries:6,max_agent_calls:100,max_minutes:90,concurrency:2,agent_timeout_seconds:180,max_turns:8,max_cost_usd:10,research_pages:4,ats_boards:[],routes:{profile:{provider:"inherit",model:""},discovery:{provider:"inherit",model:""},research:{provider:"inherit",model:""},outreach:{provider:"inherit",model:""}},weights:{role:20,skills:25,seniority:10,location:15,compensation:10,preferences:15,timing:5}}};
  const state={session:null,searches:[],activeId:null,detail:null,view:"opportunities",filter:"",kind:"all",eligibility:"all",selected:new Set(),run:null,events:[],pollTimer:null,polling:false,logOpen:false,dismissed:new Set(),configDirty:false,opportunity:null,working:null,tab:"fit",dirty:false,suppressions:[],exportScope:"all",busy:0};
  const search=()=>state.detail?.search;
  const opportunities=()=>state.detail?.opportunities||[];
  const demo=()=>Boolean(search()?.demo||search()?.settings?.provider==="demo");
  const active=(r=state.run)=>Boolean(r&&["running","queued"].includes(r.status));
  const list=(items,cls="reason-list")=>items?.length?`<ul class="${cls}">${items.map(x=>`<li>${esc(typeof x==="string"?x:x.reason||x.text||"")}</li>`).join("")}</ul>`:'<p class="text-small text-muted">Nothing recorded yet.</p>';
  const stats=()=>({total:opportunities().length,open:opportunities().filter(o=>o.kind==="open_role").length,shortlisted:opportunities().filter(o=>o.application_status==="shortlisted").length,progress:opportunities().filter(o=>["applied","contacted","replied","interview","offer"].includes(o.application_status)).length,drafted:opportunities().filter(o=>o.drafts?.length).length,apply:opportunities().filter(o=>o.ready_to_apply).length,outreach:opportunities().filter(o=>o.ready_to_contact||o.ready_to_send).length});

  async function api(path,options={}) {
    const headers=new Headers(options.headers||{});
    if(state.session?.token)headers.set("X-Session-Token",state.session.token);
    if(options.body&&!(options.body instanceof FormData)&&typeof options.body!=="string"){headers.set("Content-Type","application/json");options.body=JSON.stringify(options.body);}
    let response;
    try{response=await fetch(path,{...options,headers,cache:"no-store",credentials:"same-origin"});}catch{throw new Error("Cannot reach Job Finder. Reopen the app, then retry.");}
    if(!response.ok){let body={};try{body=await response.json();}catch{}throw new Error(body.error?.message||body.message||(typeof body.detail==="string"?body.detail:`Request failed (${response.status}).`));}
    if(options.download)return response;
    return response.status===204?{}:response.json();
  }
  function toast(message,error=false) {
    const dialog=$$('dialog[open]').at(-1);let region=dialog?$('.dialog-toast-region',dialog):$('#toast-region');
    if(!region){region=document.createElement('div');region.className='toast-region dialog-toast-region';region.setAttribute('role','status');region.setAttribute('aria-live','polite');dialog.append(region);}
    const item=document.createElement("div");item.className=`toast${error?" error":""}`;
    const content=document.createElement("span");content.textContent=message;item.append(content);
    const close=document.createElement("button");close.className="toast-close";close.type="button";close.textContent="×";close.setAttribute("aria-label","Dismiss notification");close.onclick=()=>item.remove();item.append(close);region.append(item);
    while(region.children.length>3)region.firstElementChild.remove();setTimeout(()=>item.remove(),error?9000:5000);
  }
  async function busy(label,work,button) {
    state.busy++;$("#request-status").textContent=label;if(button)button.disabled=true;
    try{return await work();}catch(error){toast(error.message||"Something went wrong.",true);return null;}finally{state.busy--;if(!state.busy)$("#request-status").textContent="";if(button&&button.isConnected)button.disabled=button.dataset.action==='run'&&active();}
  }
  function openDialog(selector){const d=$(selector);if(!d.open)d.showModal();}
  function closeDialog(d){if(d.id==="lead-dialog"&&state.dirty&&!confirm("Discard unsaved edits?"))return;d.close();if(d.id==="lead-dialog")state.dirty=false;}
  function leaveConfig(){return !state.configDirty||confirm("Leave without saving these search changes?");}
  let fieldSequence=0;
  function field(label,name,value,{type="text",placeholder="",min,max,step,rows=3,required=false,help=""}={}) {
    const id=`field-${name.replaceAll(".","-")}-${++fieldSequence}`;const attrs=`id="${id}" name="${name}"${required?" required":""}${min!==undefined?` min="${min}"`:""}${max!==undefined?` max="${max}"`:""}${step?` step="${step}"`:""} placeholder="${esc(placeholder)}"${help?` aria-describedby="${id}-help"`:""}`;
    return `<div class="field"><label for="${id}">${label}</label>${type==="textarea"?`<textarea ${attrs} rows="${rows}">${esc(value)}</textarea>`:`<input ${attrs} type="${type}" value="${esc(value??"")}">`}${help?`<small id="${id}-help">${help}</small>`:""}</div>`;
  }
  function select(label,name,value,choices){const id=`field-${name.replaceAll(".","-")}-${++fieldSequence}`;return `<div class="field"><label for="${id}">${label}</label><select id="${id}" name="${name}">${choices.map(([v,l])=>`<option value="${esc(v)}"${String(value)===String(v)?" selected":""}>${esc(l)}</option>`).join("")}</select></div>`;}
  function checkbox(label,name,checked){return `<label class="check-option"><input type="checkbox" name="${name}"${checked?" checked":""}><span>${label}</span></label>`;}
  function providers(selected,includeDemo=false){return [["codex","Codex"],["claude","Claude Code"],...(includeDemo?[["demo","Demo (offline)"]]:[])].map(([v,l])=>`<option value="${v}"${v===selected?" selected":""}>${l}</option>`).join("");}
  function providerNotice(provider){if(provider==="demo")return '<p class="text-small text-muted">Fictional data. No model calls.</p>';const p=state.session?.providers?.find(x=>x.id===provider);return p?.available?'<p class="text-small text-muted">CLI found. Uses its existing sign-in.</p>':`<div class="notice compact-notice">${esc(p?.message||`Install and sign in to ${provider==="claude"?"Claude Code":"Codex"} before running.`)} <button type="button" class="button quiet small" data-action="help">Setup ↗</button></div>`;}

  function navigation(){
    $('#search-switcher').hidden=!state.searches.length;
    $('#active-search').innerHTML=state.searches.map(s=>`<option value="${esc(s.id)}"${s.id===state.activeId?' selected':''}>${esc(s.name)}</option>`).join('');
    $('#demo-badge').hidden=!demo();
  }

  function empty(){return `<form id="new-search-form" class="start-form"><h1>New search</h1>${field('What are you looking for?','objective','',{type:'textarea',rows:3,required:true,placeholder:'Role, industry, or the work you want to do'})}<div class="field-row">${field('Name','name','',{required:true,placeholder:'Search name'})}${field('Locations','preferences.locations','',{placeholder:'Los Angeles, CA; Remote'})}</div><div class="start-actions"><button class="button accent" type="submit">Create search</button><button class="button quiet" data-action="demo" type="button">Load sample</button></div></form>`;}

  function heading(){const disabled=active()||!opportunities().length||(state.view==='shortlist'&&!stats().shortlisted);return `<div class="page-heading">${tabs()}<div class="heading-actions"><button class="button accent" type="button" data-action="run"${active()?' disabled':''}>${state.view==='outreach'?'Write outreach':'Generate report'}</button><button class="button" type="button" data-action="export" data-format="csv" data-scope="${state.view==='shortlist'?'shortlist':state.view==='export'?state.exportScope:'all'}"${disabled?' disabled':''}>CSV ${icon('export')}</button><button class="button" type="button" data-action="export" data-format="xlsx" data-scope="${state.view==='shortlist'?'shortlist':state.view==='export'?state.exportScope:'all'}"${disabled?' disabled':''}>Excel ${icon('export')}</button><button class="icon-button" type="button" data-action="view" data-view="export" aria-label="Export options" title="Export options">⋯</button></div></div>`;}

  function tabs(){const n=stats();return `<nav class="nav-tabs" aria-label="Search sections">${[['opportunities','Jobs',n.total],['shortlist','Shortlist',n.shortlisted],['setup','Search settings',null],['outreach','Outreach',null]].map(([v,label,count])=>`<button type="button" class="nav-tab${state.view===v?' active':''}" data-action="view" data-view="${v}"${state.view===v?' aria-current="page"':''}>${label}${count!==null?`<span class="tab-count">${count}</span>`:''}</button>`).join('')}</nav>`;}

  function salary(o){if(o.salary_min==null&&o.salary_max==null)return "Salary not stated";const format=v=>v==null?"":Number(v).toLocaleString(undefined,{maximumFractionDigits:0});return `${o.salary_currency||""} ${format(o.salary_min)}${o.salary_min!=null&&o.salary_max!=null?"–":""}${format(o.salary_max)}${o.salary_period&&o.salary_period!=="unknown"?` / ${o.salary_period}`:""}`.trim();}
  function opportunityPanel(){return `<section class="table-panel" aria-label="Jobs"><div class="table-toolbar"><div class="search-control">${icon('search')}<input id="opportunity-search" type="search" aria-label="Search roles or companies" placeholder="Search jobs" value="${esc(state.filter)}"></div><div class="toolbar-filters"><select id="kind-filter" aria-label="Opportunity type">${[['all','All types'],['open_role','Job listings'],['company_lead','Company leads']].map(([v,l])=>`<option value="${v}"${state.kind===v?' selected':''}>${l}</option>`).join('')}</select><select id="eligibility-filter" aria-label="Fit and progress">${[['all','All statuses'],['eligible','Eligible'],['review','Needs review'],['blocked','Conflicts'],['in_progress','In progress'],['70','Fit 70+']].map(([v,l])=>`<option value="${v}"${state.eligibility===v?' selected':''}>${l}</option>`).join('')}</select><button class="button quiet small" type="button" data-action="import">${icon('upload')} Import</button><button class="icon-button" type="button" data-action="refresh" title="Refresh" aria-label="Refresh jobs">↻</button></div></div><div id="opportunity-table-region"></div></section>`;}

  function filtered(){const q=state.filter.trim().toLowerCase();return opportunities().filter(o=>{
    if(state.view==="outreach"&&!o.drafts?.length)return false;
    if(state.view==="shortlist"&&o.application_status!=="shortlisted")return false;
    if(q&&![o.company,o.title,o.location,o.contact_name,o.email].some(v=>String(v||"").toLowerCase().includes(q)))return false;
    if(state.kind!=="all"&&o.kind!==state.kind)return false;
    if(["eligible","review","blocked"].includes(state.eligibility)&&o.eligibility!==state.eligibility)return false;
    if(state.eligibility==="shortlisted"&&o.application_status!=="shortlisted")return false;
    if(state.eligibility==="in_progress"&&!["applied","contacted","replied","interview","offer"].includes(o.application_status))return false;
    return state.eligibility!=="70"||o.fit_score>=70;
  });}
  function table(){const region=$("#opportunity-table-region");if(!region)return;const rows=filtered();const selected=state.selected.size?`<div class="bulk-toolbar"><span>${state.selected.size} selected</span><button type="button" class="button small" data-action="bulk-shortlist">Shortlist</button><button type="button" class="button quiet small" data-action="bulk-reject">Skip</button><button type="button" class="button quiet small" data-action="clear-selection">Clear</button></div>`:"";
    if(!rows.length){const noDrafts=state.view==='outreach'&&!opportunities().some(o=>o.drafts?.length),noSaved=state.view==='shortlist'&&!stats().shortlisted;region.innerHTML=`${selected}<div class="empty-table"><h2>${noDrafts?'No drafts':noSaved?'No saved jobs':opportunities().length?'No matches':'No jobs yet'}</h2><div class="inline-actions">${noSaved?'<button class="button" type="button" data-action="view" data-view="opportunities">Browse jobs</button>':noDrafts?'<button class="button accent" type="button" data-action="run">Write outreach</button>':opportunities().length?'<button class="button" type="button" data-action="reset-filters">Clear filters</button>':'<button class="button" type="button" data-action="import">Import jobs</button>'}</div></div>`;return;}
    region.innerHTML=`${selected}<div class="table-wrap" role="region" aria-label="Job results" tabindex="0"><table class="opportunity-table"><thead><tr><th class="check-cell"><input id="select-all" type="checkbox" aria-label="Select visible opportunities"${rows.every(o=>state.selected.has(o.id))?" checked":""}></th><th scope="col">ROLE / COMPANY</th><th scope="col">LOCATION / PAY</th><th scope="col">FIT</th><th scope="col">ACTIONS</th></tr></thead><tbody>${rows.map(o=>`<tr data-opportunity="${esc(o.id)}"><td class="check-cell"><input type="checkbox" data-select="${esc(o.id)}" aria-label="Select ${esc(o.title||o.company)}"${state.selected.has(o.id)?" checked":""}></td><td><div class="ranked-title"><span class="rank-number">${o.rank||opportunities().findIndex(x=>x.id===o.id)+1}</span><div><button class="company-name role-title" type="button" data-action="open-opportunity" data-id="${esc(o.id)}">${esc(o.kind==="company_lead"?o.company:o.title||"Role to confirm")}</button><span class="cell-sub">${esc(o.kind==="company_lead"?"No confirmed vacancy":o.company)}</span><span class="row-badges">${badge(o.kind==="company_lead"?"Company lead":o.listing_status||"unverified",o.kind==="company_lead"?"accent":tone(o.listing_status))}${o.application_status&&o.application_status!=="new"?badge(o.application_status):""}</span><span class="mobile-opportunity-meta">${esc(o.location||"Location unknown")} · Fit ${o.research_status==="complete"?o.fit_score:"—"}</span></div></div></td><td><span>${esc(o.location||"Location not stated")}</span><span class="cell-sub">${esc(salary(o))}</span><span class="cell-sub">${esc(o.work_mode&&o.work_mode!=="unknown"?human(o.work_mode):"Work mode not stated")}</span></td><td><div class="fit-score">${o.research_status==="complete"?o.fit_score:"—"}<span class="fit-track"><span style="width:${clamp(o.fit_score,0,100)}%"></span></span></div><span class="cell-sub" title="How much of the assessment has supporting evidence. Not a hiring probability.">${o.research_status==="complete"?`${o.confidence}% evidence`:"Not assessed"}</span></td><td><button type="button" class="next-action" data-action="open-opportunity" data-id="${esc(o.id)}">Review</button>${o.eligibility==='blocked'?badge('Conflict','bad'):''}<div class="row-job-link">${demo()?'':link(o.apply_url||o.listing_url||o.careers_url,'Open job')}</div>${['new','shortlisted'].includes(o.application_status)?`<button class="save-job${o.application_status==='shortlisted'?' saved':''}" type="button" data-action="toggle-shortlist" data-id="${esc(o.id)}" aria-label="${o.application_status==='shortlisted'?'Remove from shortlist':'Save to shortlist'}" aria-pressed="${o.application_status==='shortlisted'}">${o.application_status==='shortlisted'?'★':'☆'}</button>`:''}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function setup(){
    const s=search(),c={...defaults.candidate,...s.candidate},p={...defaults.preferences,...s.preferences},settings={...defaults.settings,...s.settings};
    const profile=s.profile_brief||s.profile||{},docs=state.detail.documents||[];
    const routeRows=[['profile','Résumé & direction'],['discovery','Opportunity discovery'],['research','Research & fit'],['outreach','Email writing']].map(([key,label])=>{const r=settings.routes?.[key]||defaults.settings.routes[key];return `<div class="route-row"><span>${label}</span><label class="sr-only" for="route-${key}-provider">${label} provider</label><select id="route-${key}-provider" name="settings.routes.${key}.provider">${[['inherit','Use default'],['codex','Codex'],['claude','Claude Code']].map(([v,l])=>`<option value="${v}"${r.provider===v?' selected':''}>${l}</option>`).join('')}</select><label class="sr-only" for="route-${key}-model">${label} model</label><input id="route-${key}-model" name="settings.routes.${key}.model" value="${esc(r.model||'')}" placeholder="Default model"></div>`;}).join('');
    return `<form id="search-form"><div class="setup-grid">
      <section class="panel span-all"><div class="panel-header"><h2>Search</h2></div><div class="panel-body">${field('Objective','objective',s.objective,{type:'textarea',rows:3,placeholder:'Find a senior mechanical engineering role in aerospace. I want hands-on design work and a team based in Southern California.'})}<div class="field-row">${field('Target roles','preferences.roles',p.roles.join(', '),{placeholder:'Mechanical engineer, manufacturing engineer'})}${field('Locations','preferences.locations',p.locations.join('; '),{placeholder:'Los Angeles, CA; San Diego, CA; remote US'})}</div><div class="field-row three">${field('Search name','name',s.name,{required:true})}${field('Minimum annual salary','preferences.salary_min',p.salary_min,{type:'number',min:0,placeholder:'Optional'})}${field('Currency','preferences.salary_currency',p.salary_currency,{placeholder:'USD'})}</div><div class="check-options"><span class="field-label">Work mode</span>${['remote','hybrid','onsite'].map(v=>checkbox(human(v),`work_mode.${v}`,p.work_modes.includes(v))).join('')}${checkbox('Required','preferences.strict_work_mode',p.strict_work_mode)}</div><details class="details-block"><summary>More preferences</summary><div class="details-content"><div class="field-row three">${field('Seniority','preferences.seniority',p.seniority,{placeholder:'Senior, individual contributor'})}${field('Industries','preferences.industries',p.industries.join(', '),{placeholder:'Aerospace, robotics'})}${field('Employment types','preferences.employment_types',p.employment_types.join(', '),{placeholder:'Full time, contract'})}</div><div class="field-row">${field('Must have','preferences.must_have',p.must_have,{type:'textarea',rows:2,placeholder:'Requirements that matter to you'})}${field('Exclude','preferences.exclude',p.exclude,{type:'textarea',rows:2,placeholder:'Companies, roles, or conditions to avoid'})}</div><div class="field-row three">${select('Relocation','preferences.relocation',p.relocation,[['unknown','Not specified'],['yes','Open to relocation'],['maybe','Maybe'],['no','No relocation']])}${field('Posting age limit · days','preferences.max_post_age_days',p.max_post_age_days,{type:'number',min:1,max:365})}<div class="field"><label>Discovery</label>${checkbox('Include potential employers','preferences.include_company_leads',p.include_company_leads)}<small>Company leads are clearly marked. A funding event is not proof of an open role.</small></div></div></div></details></div></section>
      <section class="panel span-all"><div class="panel-header"><h2>Résumé</h2></div><div class="panel-body"><div class="document-drop"><span>${icon('upload')} Résumé or portfolio</span><button class="button small" type="button" data-action="documents">Upload</button></div>${docs.length?`<ul class="document-list">${docs.map(d=>`<li class="document-item"><span>${esc(d.name||d.filename||'Document')}<small>${d.characters?`${Number(d.characters).toLocaleString()} characters`:''}</small></span><button class="button quiet small" type="button" data-action="remove-document" data-id="${esc(d.id)}" aria-label="Remove ${esc(d.name||d.filename||'document')}">Remove</button></li>`).join('')}</ul>`:''}${field('Résumé text','candidate.resume_text',c.resume_text,{type:'textarea',rows:6,placeholder:'Experience, achievements, skills, and education…'})}<div class="field-row three">${field('Name','candidate.name',c.name,{placeholder:'Optional'})}${field('Email','candidate.email',c.email,{type:'email',placeholder:'For your draft signature'})}${field('Current location','candidate.location',c.location,{placeholder:'Optional'})}</div><details class="details-block"><summary>Skills, links & eligibility</summary><div class="details-content"><div class="field-row">${field('Skills','candidate.skills',c.skills.join(', '),{placeholder:'CAD, FEA, Python, supply chain'})}${field('Experience · years','candidate.years_experience',c.years_experience,{type:'number',min:0,max:80,step:0.5})}</div>${field('Portfolio or profile URLs','candidate.links',c.links.join('\n'),{type:'textarea',rows:2,placeholder:'One public URL per line'})}<div class="field-row three">${field('Work authorization','candidate.work_authorization',c.work_authorization,{placeholder:'Optional · only what you want to share'})}${select('Need visa sponsorship?','candidate.needs_sponsorship',c.needs_sponsorship==null?'unknown':String(c.needs_sponsorship),[['unknown','Not specified'],['true','Yes'],['false','No']])}${field('Clearance','candidate.clearance',c.clearance,{placeholder:'Optional'})}</div></div></details></div></section>
      ${profile.summary?`<section class="panel span-all profile-panel"><div class="panel-header"><span class="section-number">↗</span><h2>Your direction, refined</h2></div><div class="panel-body"><p>${esc(profile.summary)}</p><div class="profile-columns"><div><h3>Suggested roles</h3>${list(profile.suggested_roles)}</div><div><h3>Your strengths</h3>${list(profile.strengths)}</div><div><h3>To clarify</h3>${list([...(profile.gaps||[]),...(profile.unknowns||[])])}</div></div></div></section>`:''}
      <section class="panel span-all"><div class="panel-header"><h2>Agent settings</h2></div><div class="panel-body"><div class="field-row"><div class="field"><label for="provider-select">Default provider</label><select id="provider-select" name="settings.provider">${providers(settings.provider,demo())}</select></div>${field('Default model','settings.model',settings.model,{placeholder:'Leave blank for the CLI default'})}</div><div id="provider-notice">${providerNotice(settings.provider)}</div><div class="route-grid"><div class="route-row route-heading"><span>TASK</span><span>PROVIDER</span><span>MODEL</span></div>${routeRows}</div><div class="field-row four">${field('Target opportunities','settings.target_results',settings.target_results,{type:'number',min:1,max:1000})}${field('Minimum fit','settings.min_fit',settings.min_fit,{type:'number',min:0,max:100})}${field('Agent call limit','settings.max_agent_calls',settings.max_agent_calls,{type:'number',min:1,max:5000})}${field('Time limit · minutes','settings.max_minutes',settings.max_minutes,{type:'number',min:1,max:1440})}</div><details class="details-block"><summary>Advanced controls & scoring</summary><div class="details-content"><div class="field-row four">${field('Discovery queries','settings.discovery_queries',settings.discovery_queries,{type:'number',min:1,max:40})}${field('Concurrent tasks','settings.concurrency',settings.concurrency,{type:'number',min:1,max:4})}${field('Pages per opportunity','settings.research_pages',settings.research_pages,{type:'number',min:1,max:10})}${field('Task timeout · seconds','settings.agent_timeout_seconds',settings.agent_timeout_seconds,{type:'number',min:15,max:900})}</div><div class="field-row">${field('Claude turn limit','settings.max_turns',settings.max_turns,{type:'number',min:2,max:40})}${field('Claude run budget · USD','settings.max_cost_usd',settings.max_cost_usd,{type:'number',min:0.1,max:1000,step:0.1})}</div><p class="text-small text-muted">Call and time limits apply to the run. Provider billing and model availability depend on your CLI account.</p>${field('Direct ATS board URLs','settings.ats_boards',(settings.ats_boards||[]).join('\n'),{type:'textarea',rows:2,placeholder:'Optional · Greenhouse, Lever, or Ashby board URLs, one per line'})}<h3 class="subheading">Fit weights</h3><div class="weight-fields">${Object.entries({...defaults.settings.weights,...settings.weights}).map(([k,v])=>field(human(k),`settings.weights.${k}`,v,{type:'number',min:0,max:100})).join('')}</div><p class="text-small text-muted">Weights are normalized. Unknown evidence stays unknown; fit is not the chance of getting an offer.</p></div></details></div></section>
      </div><div class="save-bar"><span id="save-message">${state.configDirty?'Unsaved changes':'Saved locally'}</span><button class="button primary" type="submit">Save search ${icon('check')}</button></div></form>`;
  }
  function formSearch(form,existing=search()||defaults){
    const value={name:existing.name||'',objective:existing.objective||'',demo:Boolean(existing.demo),candidate:{...defaults.candidate,...existing.candidate},preferences:{...defaults.preferences,...existing.preferences},settings:{...defaults.settings,...existing.settings,routes:structuredClone(existing.settings?.routes||defaults.settings.routes),weights:{...defaults.settings.weights,...existing.settings?.weights}}};
    const arrays=new Set(['candidate.skills','candidate.links','preferences.roles','preferences.locations','preferences.industries','preferences.employment_types','settings.ats_boards']);
    const nullable=new Set(['candidate.years_experience','preferences.salary_min']);
    for(const input of $$('[name]',form)){
      const name=input.name;if(name.startsWith('work_mode.'))continue;
      const parts=name.split('.');let target=value;for(const part of parts.slice(0,-1))target=target[part]??=( {} );
      let v=input.type==='checkbox'?input.checked:input.value;
      if(arrays.has(name))v=v.split(name==='preferences.locations'?/[\n;]/:['candidate.links','settings.ats_boards'].includes(name)?/\n/:/[\n,]/).map(s=>s.trim()).filter(Boolean);
      else if(input.type==='number')v=v===''?(nullable.has(name)?null:0):Number(v);
      else if(name==='candidate.needs_sponsorship')v=v==='unknown'?null:v==='true';
      target[parts.at(-1)]=v;
    }
    if($('[name="work_mode.remote"]',form))value.preferences.work_modes=$$('[name^="work_mode."]:checked',form).map(i=>i.name.split('.')[1]);
    return value;
  }
  async function saveSearch({rerender=true}={}){
    const form=$('#search-form');if(!form)return true;if(!form.reportValidity())return false;
    const result=await api(`/api/searches/${state.activeId}`,{method:'PATCH',body:formSearch(form)});
    state.detail=await api(`/api/searches/${state.activeId}`);state.configDirty=false;await listSearches();if(rerender)render();else $('#save-message').textContent='Saved locally';return true;
  }
  function newSearchDialog(){
    $('#campaign-dialog').innerHTML=`<form id="new-search-form"><div class="dialog-header"><div><h2 id="campaign-dialog-title">Create a search</h2></div><button class="button icon-button" type="button" data-action="close-dialog" aria-label="Close">${icon('close')}</button></div><div class="dialog-body">${field('Name','name','',{required:true,placeholder:'My next engineering role'})}${field('What are you looking for?','objective','',{type:'textarea',rows:4,required:true,placeholder:'The work you want to do, where, and what matters most.'})}${field('Locations','preferences.locations','',{placeholder:'Optional · use semicolons between places'})}<div class="field-row"><div class="field"><label for="new-provider">Agent</label><select id="new-provider" name="settings.provider">${providers('codex')}</select></div>${field('Target opportunities','settings.target_results',25,{type:'number',min:1,max:1000})}</div></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">Cancel</button><button class="button accent" type="submit">Create search ${icon('arrow')}</button></div></form>`;openDialog('#campaign-dialog');
  }
  function exportPanel(){
    const n=stats(),disabled=active()||!opportunities().length||(demo()&&['apply','outreach'].includes(state.exportScope))||({all:n.total,apply:n.apply,outreach:n.outreach,shortlist:n.shortlisted}[state.exportScope]===0);
    const choices=[['all','All jobs',`${n.total} jobs`],['apply','Ready to apply',`${n.apply} checked open roles`],['outreach','Approved outreach',`${n.outreach} approved public contacts`],['shortlist','Your shortlist',`${n.shortlisted} saved jobs`]];
    return `<div class="export-grid"><section class="panel"><div class="panel-header"><h2>Export</h2></div><div class="panel-body export-choices">${choices.map(([v,label,note])=>`<label class="export-choice${state.exportScope===v?' selected':''}"><input type="radio" name="export-scope" value="${v}"${state.exportScope===v?' checked':''}><span><strong>${label}</strong><small>${note}</small></span></label>`).join('')}${demo()?'<p class="text-small text-muted">Demo data: research and shortlist exports only.</p>':''}<div class="export-buttons"><button class="button accent" type="button" data-action="export" data-format="csv"${disabled?' disabled':''}>${icon('export')} Download CSV</button><button class="button" type="button" data-action="export" data-format="xlsx"${disabled?' disabled':''}>Excel</button></div></div></section></div>`;
  }
  function render(){
    navigation();if(!search()){$('#main').innerHTML=empty();$('#job-region').innerHTML='';return;}
    $('#main').innerHTML=heading()+(state.view==='setup'?setup():state.view==='export'?exportPanel():opportunityPanel());
    table();renderRun();
  }
  async function listSearches(){const result=await api('/api/searches');state.searches=Array.isArray(result)?result:(result.searches||[]);navigation();}
  function latestRun(){const runs=state.detail?.runs||[];return runs.find(r=>['queued','running','paused'].includes(r.status))||runs[0]||null;}
  async function loadSearch(id,{view,reset=true}={}){
    clearTimeout(state.pollTimer);const detail=await api(`/api/searches/${id}`);state.detail=detail;state.activeId=id;if(view)state.view=view;
    if(reset){state.selected.clear();state.filter='';state.kind='all';state.eligibility='all';state.configDirty=false;state.exportScope='all';}
    try{localStorage.setItem('job-finder-active-search',id);}catch{}
    state.run=latestRun();state.events=[];render();if(state.run)await pollRun();
  }
  async function refresh({quiet=false}={}){
    if(!state.activeId)return;const id=state.activeId;const detail=await api(`/api/searches/${id}`);if(id!==state.activeId)return;
    state.detail=detail;await listSearches();
    if(!quiet){const latest=latestRun();if(latest&&latest.id!==state.run?.id){state.run=latest;state.events=[];if(active())state.pollTimer=setTimeout(pollRun,100);}}
    if(state.view==='setup'&&state.configDirty){navigation();renderRun();return;}
    if(quiet&&['opportunities','outreach','shortlist'].includes(state.view)){$('.nav-tabs').outerHTML=tabs();table();renderRun();}else render();
  }
  function renderRun(){
    $$('[data-action="run"]').forEach(button=>button.disabled=active());$$('[data-action="export"]').forEach(button=>button.disabled=active()||!opportunities().length||(demo()&&['apply','outreach'].includes(button.dataset.scope||state.exportScope))||({all:stats().total,apply:stats().apply,outreach:stats().outreach,shortlist:stats().shortlisted}[button.dataset.scope||state.exportScope]===0));
    const region=$('#job-region'),r=state.run;if(!r||state.dismissed.has(r.id)){region.innerHTML='';return;}
    const total=Number(r.total||0),done=Number(r.completed||0),stageIndex=Number(r.stage_index||0),stageCount=Number(r.stage_count||1),fraction=total?done/total:0;
    const pct=r.status==='completed'?100:['pipeline','report'].includes(r.stage)&&stageIndex?clamp((stageIndex-1+fraction)/stageCount*100,0,100):clamp(fraction*100,0,100);
    const labels={report:'Job report',pipeline:'Report + outreach',profile:'Reading your résumé',discover:'Finding opportunities',research:'Assessing fit',verify:'Checking sources',draft:'Writing outreach'};
    const stage=r.current_stage||r.stage,heading=labels[stage]||human(stage);
    const controls=active(r)?'<button class="button small" data-action="pause-run" type="button">Pause</button><button class="button quiet small" data-action="cancel-run" type="button">Cancel</button>':['paused','failed','budget_exhausted'].includes(r.status)?'<button class="button small" data-action="resume-run" type="button">Resume</button><button class="button quiet small" data-action="cancel-run" type="button">Cancel</button>':'<button class="button quiet small" data-action="dismiss-run" type="button" aria-label="Dismiss run">×</button>';
    const log=state.events.slice(-30).map(e=>`<div class="job-log-line"><time>${esc(e.created_at?new Date(e.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}):'')}</time><span>${esc(e.message||e.event||e.text||human(e.type))}</span></div>`).join('');
    const elapsed=Number(r.elapsed_seconds||0),tokens=(r.input_tokens||0)+(r.output_tokens||0),knownCost=r.cost_usd!=null?`$${Number(r.cost_usd).toFixed(2)} reported`:r.claude_cost_usd?`$${Number(r.claude_cost_usd).toFixed(2)} Claude`:'';
    if(r.status==='completed'){region.innerHTML='';return;}
    region.innerHTML=`<section class="job-panel" aria-label="Workflow progress"><div class="job-topline"><div class="job-heading"><span class="status-dot${active(r)?' pulsing':''}"></span><strong>${esc(heading)}</strong>${badge(r.status)}</div><div class="job-controls"><button class="button quiet small" data-action="toggle-log" type="button" aria-expanded="${state.logOpen}">Activity ${state.logOpen?'−':'+'}</button>${controls}</div></div>${r.current_item?`<div class="run-current-item">${esc(r.current_item)}</div>`:''}<div class="job-progress-row"><div class="job-progress${active(r)&&!total?' indeterminate':''}" role="progressbar" aria-label="Workflow progress" aria-valuemin="0" aria-valuemax="100"${total||!active(r)?` aria-valuenow="${Math.round(pct)}"`:''}><span style="width:${pct}%"></span></div><span class="job-progress-count">${['pipeline','report'].includes(r.stage)?`${stageIndex}/${stageCount} stages · `:''}${total?`${done}/${total}`:r.status==='completed'?'Done':active(r)?'Working…':human(r.status)}</span></div><div class="job-meta"><span>${Number(r.agent_calls||0)}${r.max_agent_calls?`/${r.max_agent_calls}`:''} agent calls</span>${elapsed?`<span>${Math.floor(elapsed/60)}m ${Math.floor(elapsed%60)}s elapsed</span>`:''}${tokens?`<span>${tokens.toLocaleString()} tokens reported</span>`:''}${knownCost?`<span>${knownCost}</span>`:''}${r.unpriced_calls?`<span>${r.unpriced_calls} calls unpriced</span>`:''}${r.error_count?`<span>${r.error_count} tasks need attention</span>`:''}</div>${r.error?`<div class="job-error">${esc(r.error)}</div>`:''}${state.logOpen?`<div class="job-log" aria-label="Recent activity">${log||'<span class="text-small text-muted">Waiting for activity.</span>'}</div>`:''}</section>`;
  }
  async function pollRun(){
    if(!state.run||state.polling)return;const id=state.run.id,searchId=state.activeId;state.polling=true;
    try{const result=await api(`/api/runs/${id}`);if(searchId!==state.activeId||state.run?.id!==id)return;const wasActive=active();state.run=result.run||result;state.events=result.events||state.events;if(wasActive&&state.run.status==='completed')toast(state.run.stage==='draft'?'Drafts ready.':'Report ready.');renderRun();await refresh({quiet:true});}
    catch(error){toast(error.message,true);}finally{state.polling=false;if(state.run?.id===id&&active())state.pollTimer=setTimeout(pollRun,1600);}
  }
  async function startRun(stage=state.view==='outreach'?'draft':'report'){
    if(state.configDirty&&!(await saveSearch({rerender:false})))return;
    const result=await api(`/api/searches/${state.activeId}/runs`,{method:'POST',body:{stage}});
    state.run=result.run||result;state.events=[];state.dismissed.delete(state.run.id);if(['report','pipeline','discover'].includes(stage)){state.view='opportunities';state.selected.clear();render();}else renderRun();await pollRun();
  }

  function scorePanel(o){
    const scores=o.score_breakdown?.length?o.score_breakdown:o.match_components||[];
    return `<section class="lead-section"><div class="section-heading"><h3>Fit</h3><span class="text-small text-muted">${o.confidence||0}% evidence</span></div>${scores.length?`<div class="score-grid">${scores.map(c=>{const criterion=c.criterion||c.name||'Fit',match=c.match||c.status||'unknown';const weight=Number(c.weight??search().settings?.weights?.[criterion]??0),points=Number(c.points??c.score??0),pct=c.percent??(weight?points/weight*100:({strong:100,partial:50,gap:0,unknown:0}[match]||0));return `<div class="score-row"><div class="score-label"><strong>${esc(human(criterion))}</strong>${badge(match,tone(match))}</div><div class="score-bar"><span style="width:${clamp(pct,0,100)}%"></span></div><p>${esc(c.reason||'No explanation recorded.')}</p>${c.source_url?link(c.source_url,'Source','text-small'):''}${c.candidate_quote?`<blockquote class="resume-quote">${esc(c.candidate_quote)}</blockquote>`:''}</div>`;}).join('')}</div>`:'<p class="text-small text-muted">Run research to build an evidence-based assessment.</p>'}</section>`;
  }
  function fitDetails(o){
    const statuses=['new','shortlisted','applied','contacted','replied','interview','offer','rejected','withdrawn','not_interested','bounced'];
    return `<div class="lead-section opportunity-summary"><p>${esc(o.research_summary||o.description||'Research this opportunity to see the match, gaps, and next step.')}</p><div class="fact-grid"><div><small>LOCATION</small><strong>${esc(o.location||'Not stated')}</strong><span>${esc(human(o.work_mode||'unknown'))}</span></div><div><small>COMPENSATION</small><strong>${esc(salary(o))}</strong>${o.salary_source?link(o.salary_source,'Source','text-small'):''}</div><div><small>ROLE</small><strong>${esc(o.seniority||'Level not stated')}</strong><span>${esc(o.employment_type||'Type not stated')}</span></div></div></div>
      ${o.blockers?.length?`<section class="lead-section"><h3>Conflicts to resolve</h3>${list(o.blockers,'reason-list risks')}</section>`:''}
      ${o.questions?.length?`<section class="lead-section"><h3>Questions to check</h3>${list(o.questions)}</section>`:''}
      ${scorePanel(o)}
      ${o.requirement_checks?.length?`<section class="lead-section"><h3>Eligibility & requirements</h3><div class="requirement-list">${o.requirement_checks.map(c=>`<div class="requirement-row"><div><strong>${esc(human(c.criterion))}</strong>${badge(c.status,c.status==='met'?'good':tone(c.status))}</div><p>${esc(c.reason)}</p>${c.source_url?link(c.source_url,'Source','text-small'):''}</div>`).join('')}</div></section>`:''}
      ${o.candidate_claims?.length?`<section class="lead-section"><h3>Supported by your résumé</h3>${o.candidate_claims.map(c=>`<div class="candidate-claim"><strong>${esc(c.text)}</strong><blockquote>${esc(c.resume_quote)}</blockquote></div>`).join('')}</section>`:''}
      ${o.skill_gaps?.length||o.risks?.length?`<section class="lead-section"><h3>Gaps & risks</h3>${list([...(o.skill_gaps||[]),...(o.risks||[])])}</section>`:''}
      ${o.application_tips?.length||o.interview_hooks?.length?`<section class="lead-section"><div class="two-col-info"><div><h3>Application notes</h3>${list(o.application_tips)}</div><div><h3>Conversation starters</h3>${list(o.interview_hooks)}</div></div></section>`:''}
      <section class="lead-section"><h3>Your progress</h3><div class="field-row">${select('Application status','application_status',o.application_status||'new',statuses.map(v=>[v,human(v)]))}${field('Follow-up date','next_follow_up',(o.next_follow_up||'').slice(0,10),{type:'date'})}</div>${field('Your notes','review_notes',o.review_notes,{type:'textarea',rows:3,placeholder:'Recruiter conversation, next step, or a question to ask…'})}</section>
      <section class="lead-section"><h3>Public contact</h3><div class="field-row">${field('Name','contact_name',o.contact_name)}${field('Role','contact_role',o.contact_role)}</div><div class="field-row">${field('Email','email',o.email,{type:'email'})}${field('Email source URL','email_source',o.email_source,{type:'url'})}</div><div class="contact-state">${badge(o.email_status||'missing')}<span>${o.verification_at?`Verified ${date(o.verification_at)}`:'Published or MX-found is not mailbox verification.'}</span></div><details class="details-block"><summary>Contact sources</summary><div class="details-content">${field('Contact source URL','contact_source',o.contact_source,{type:'url'})}${field('Public profile URL','linkedin_url',o.linkedin_url,{type:'url'})}</div></details><button class="button quiet small danger-text" type="button" data-action="suppress-opportunity">Do not contact this ${o.email?'email':'company'}</button></section>`;
  }
  function outreachDetails(o){
    if(!o.drafts?.length)return `<div class="empty-table"><h3>No drafts</h3><button class="button primary" type="button" data-action="draft-from-detail">Write outreach ${icon('arrow')}</button></div>`;
    return `<div class="lead-section outreach-intro"><p>${esc(o.outreach_angle||'Review the facts, wording, and recipient before approving.')}</p><div class="contact-state"><strong>${esc(o.contact_name||'Contact to confirm')}</strong><span>${esc(o.email||'No published email found')}</span>${badge(o.email_status||'missing')}</div>${o.export_blockers?.length?`<details class="details-block"><summary>${o.export_blockers.length} export ${o.export_blockers.length===1?'check':'checks'} remaining</summary><div class="details-content">${list(o.export_blockers)}</div></details>`:''}</div>
      <div class="email-sequence">${o.drafts.map((d,i)=>`<article class="email-card"><div class="email-card-heading"><div class="email-step"><span class="step-number">${d.step||i+1}</span><strong>${i===0?'Introduction':i===1?'Follow-up':'Final follow-up'}</strong></div><label class="email-delay">Day <input type="number" min="0" max="60" name="draft.${i}.delay_days" value="${Number(d.delay_days)||0}" aria-label="Email ${i+1} delay in days"></label></div><div class="email-edit-area"><div class="label-row"><label for="draft-subject-${i}">Subject</label><span class="word-counter${words(d.subject)>5?' over':''}" data-count="draft.${i}.subject">${words(d.subject)}/5 words</span></div><input id="draft-subject-${i}" name="draft.${i}.subject" value="${esc(d.subject)}" placeholder="A short subject"><div class="label-row"><label for="draft-body-${i}">Message</label><span class="word-counter${words(d.body)>(i===0?90:65)?' over':''}" data-count="draft.${i}.body">${words(d.body)}/${i===0?90:65} words</span></div><textarea id="draft-body-${i}" name="draft.${i}.body" rows="${i===0?8:6}">${esc(d.body)}</textarea>${d.angle?`<div class="email-angle"><span>WHY THIS ANGLE</span><p>${esc(d.angle)}</p></div>`:''}${d.source_urls?.length?`<div class="email-card-sources"><span>Research</span>${d.source_urls.map(v=>link(v,host(v))).join('')}</div>`:''}${d.candidate_claims?.length?`<details class="details-block"><summary>Résumé support</summary><div class="details-content">${list(d.candidate_claims)}</div></details>`:''}</div></article>`).join('')}</div>
      ${o.qa_issues?.length?`<section class="lead-section"><h3>Copy checks</h3>${list(o.qa_issues,'qa-list')}</section>`:''}<div class="lead-section approval-info">Approval records your review. Export checks still require a public email with its source and current evidence. Mailbox verification is optional. Delays are suggestions; no email is scheduled here.</div>`;
  }
  function evidenceDetails(o){
    return `<section class="lead-section"><h3>Listing freshness</h3><div class="fact-grid"><div><small>POSTED</small><strong>${esc(date(o.posted_at))}</strong><span>${esc(o.date_basis||'Date not confirmed')}</span></div><div><small>LAST CHECKED</small><strong>${esc(date(o.last_checked_at))}</strong><span>${esc(human(o.source_check||'unverified'))}</span></div><div><small>STATUS</small>${badge(o.listing_status||'unverified')}<span>${o.valid_through?`Until ${esc(date(o.valid_through))}`:esc(human(o.listing_source||'unknown'))}</span></div></div><div class="inline-actions">${link(o.listing_url,'Original listing','button small')}${link(o.company_url,'Company website','button small')}${link(o.careers_url,'Careers page','button small')}${link(o.linkedin_url,'Public profile','button small')}</div>${o.source_updated_at?`<p class="text-small text-muted">Source updated ${date(o.source_updated_at)}</p>`:''}</section>
      ${o.signals?.length?`<section class="lead-section"><h3>Why this company, now</h3><p class="text-small text-muted">Signals suggest where to look. They do not confirm a vacancy.</p>${o.signals.map(s=>`<article class="evidence-card"><div class="source-title">${badge(s.kind,'accent')}${link(s.url,host(s.url))}</div><p>${esc(s.summary)}</p><div class="source-meta"><span>Published ${esc(date(s.published_at))}</span><span>Event ${esc(date(s.event_at))}</span></div></article>`).join('')}</section>`:''}
      <section class="lead-section"><h3>Research evidence</h3>${o.evidence?.length?o.evidence.map(e=>`<article class="evidence-card"><div class="source-title">${link(e.url,e.title||host(e.url))}${e.field?badge(e.field):''}</div><blockquote>${esc(e.quote)}</blockquote><div class="source-meta"><span>${esc(host(e.url))}</span><span>Checked ${esc(date(e.checked_at))}</span></div></article>`).join(''):'<p class="text-small text-muted">No source excerpts recorded. Run research to collect them.</p>'}</section>
      ${o.source_urls?.length?`<section class="lead-section"><h3>Additional sources</h3><ul class="source-links">${o.source_urls.map(v=>`<li>${link(v,v)}</li>`).join('')}</ul></section>`:''}`;
  }
  function openOpportunity(id){const o=opportunities().find(x=>x.id===id);if(!o)return;state.opportunity=o;state.working=structuredClone(o);state.tab=state.view==='outreach'?'outreach':'fit';state.dirty=false;renderOpportunity();openDialog('#lead-dialog');sizeDrafts();}
  function renderOpportunity(){
    const o=state.working;if(!o)return;const title=o.kind==='company_lead'?o.company:o.title||'Role to confirm';
    const action=demo()?'<span class="demo-link-note">Fictional demo · links are for illustration</span>':o.kind==='open_role'&&url(o.apply_url||o.listing_url)?link(o.apply_url||o.listing_url,'Open application','button accent small'):link(o.careers_url||o.company_url,'Visit careers page','button small');
    $('#lead-dialog').innerHTML=`<div class="dialog-header"><div class="opportunity-title"><div class="lead-title-line"><span class="eyebrow">${esc(o.kind==='company_lead'?'POTENTIAL EMPLOYER':o.company)}</span>${badge(o.eligibility==='review'?'Needs check':o.eligibility)}</div><h2 id="lead-dialog-title">${esc(title)}</h2><div class="lead-subtitle"><span>${esc(o.location||'Location not stated')}</span><span>Fit ${o.fit_score||0} · ${o.confidence||0}% evidence</span></div><div class="detail-actions">${action}${o.kind==='company_lead'?'<span class="text-small text-muted">No confirmed vacancy</span>':''}</div></div><button class="button icon-button" type="button" data-action="close-dialog" aria-label="Close opportunity">${icon('close')}</button></div><div class="lead-dialog-nav" role="tablist" aria-label="Job details">${[['fit','Fit & next step'],['outreach','Outreach'],['evidence','Evidence']].map(([v,l])=>`<button id="detail-tab-${v}" type="button" role="tab" aria-controls="opportunity-tab-panel" aria-selected="${state.tab===v}" tabindex="${state.tab===v?'0':'-1'}" class="lead-tab${state.tab===v?' active':''}" data-action="detail-tab" data-tab="${v}">${l}${v==='outreach'&&o.drafts?.length?` <span>${o.drafts.length}</span>`:''}</button>`).join('')}</div><div id="opportunity-tab-panel" class="lead-tab-panel" role="tabpanel" aria-labelledby="detail-tab-${state.tab}">${state.tab==='fit'?fitDetails(o):state.tab==='outreach'?outreachDetails(o):evidenceDetails(o)}</div><div class="dialog-footer lead-footer"><div class="lead-save-indicator" id="opportunity-save-indicator">${state.dirty?'Unsaved changes':o.status==='approved'?'Outreach approved':'Saved locally'}</div><div class="inline-actions"><button class="button quiet small" type="button" data-action="skip-opportunity">Skip</button><button class="button small" type="button" data-action="shortlist-opportunity">${o.application_status==='shortlisted'?'Shortlisted ✓':'Shortlist'}</button><button class="button primary small" type="button" data-action="save-opportunity">Save edits</button>${o.drafts?.length?`<button class="button accent small" type="button" data-action="approve-opportunity">${o.status==='approved'?'Approved ✓':'Approve outreach'}</button>`:''}</div></div>`;
    sizeDrafts();
  }
  function sizeDrafts(){if(!$('#lead-dialog').open)return;for(const input of $$('textarea[name^="draft."]',$('#lead-dialog'))){input.style.height='auto';input.style.height=`${Math.max(160,input.scrollHeight+2)}px`;}}
  function captureOpportunity(){
    if(!state.working)return;for(const input of $$('[name]',$('#lead-dialog'))){const name=input.name;if(name.startsWith('draft.')){const [,index,key]=name.split('.');if(state.working.drafts[Number(index)])state.working.drafts[Number(index)][key]=key==='delay_days'?Number(input.value):input.value;}else state.working[name]=input.value;}
  }
  async function saveOpportunity(patch={}){
    captureOpportunity();const o=state.working;if(!o)return;const fields=['application_status','next_follow_up','review_notes','contact_name','contact_role','email','email_source','contact_source','linkedin_url','drafts'];const body={};for(const key of fields)if(key in o)body[key]=o[key];
    const result=await api(`/api/opportunities/${o.id}`,{method:'PATCH',body:{...body,...patch}});state.working=structuredClone(result.opportunity||result);state.opportunity=structuredClone(state.working);state.dirty=false;await refresh({quiet:true});renderOpportunity();toast(patch.status==='approved'?'Outreach approved.':patch.application_status==='shortlisted'?'Added to your shortlist.':'Changes saved.');
  }
  async function bulkUpdate(patch){for(const id of state.selected)await api(`/api/opportunities/${id}`,{method:'PATCH',body:patch});state.selected.clear();await refresh();toast('Progress updated.');}
  async function download(format,scope=state.exportScope){
    if(demo()&&['apply','outreach'].includes(scope))throw new Error('Demo data can only be exported for research or your shortlist.');
    const response=await api(`/api/searches/${state.activeId}/export?format=${encodeURIComponent(format)}&scope=${encodeURIComponent(scope)}`,{download:true});
    const blob=await response.blob(),href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=`${search().name.replace(/[^a-zA-Z0-9_-]+/g,'-').toLowerCase()}-${scope}.${format}`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1500);toast(`${format.toUpperCase()} downloaded.`);
  }
  function importDialog(kind='opportunities'){
    const title={opportunities:'Import opportunities',verification:'Import email verification',outcomes:'Import application progress'}[kind];
    const copy={opportunities:'CSV or Excel. Use company, title, kind, listing_url, apply_url, location, and any other known fields. Existing identities are deduplicated.',verification:'CSV from your verification tool: email, status, checked_at. Verified, risky, catch_all, and invalid results remain distinct.',outcomes:'CSV with application_status and an opportunity ID, listing URL, or email to match. Add next_follow_up for your next check-in.'}[kind];
    $('#small-dialog').innerHTML=`<form id="import-form" data-kind="${kind}"><div class="dialog-header"><div><span class="eyebrow">BRING YOUR RESEARCH</span><h2 id="small-dialog-title">${title}</h2></div><button class="button icon-button" type="button" data-action="close-dialog" aria-label="Close">${icon('close')}</button></div><div class="dialog-body"><p>${copy}</p><div class="field"><label for="import-file">${kind==='opportunities'?'CSV or Excel file':'CSV file'}</label><input id="import-file" name="file" type="file" accept="${kind==='opportunities'?'.csv,.xlsx':'.csv'}" required></div>${kind==='verification'?field('Verification provider','source','',{placeholder:'e.g. your email verifier'}):''}<div class="notice compact-notice">Imported facts keep their source and verification status. They are checked before action exports.</div></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">Cancel</button><button class="button primary" type="submit">Import ${icon('upload')}</button></div></form>`;openDialog('#small-dialog');
  }
  async function importFile(form){
    const file=$('[name="file"]',form).files[0];if(!file)return;if(file.size>8*1024*1024)throw new Error('Choose a file smaller than 8 MB.');const kind=form.dataset.kind;const endpoint=kind==='verification'?'verification/import':kind==='outcomes'?'outcomes/import':'import';
    const data=new FormData();data.append('file',file);if(kind==='verification')data.append('source',$('[name="source"]',form).value);
    const result=await api(`/api/searches/${state.activeId}/${endpoint}`,{method:'POST',body:data});await refresh();$('#small-dialog').close();
    const count=result.imported??result.updated??result.created??result.count;toast(count!=null?`${count} ${kind==='opportunities'?'opportunities imported':'records updated'}.`:'Import complete.');if(result.errors?.length)toast(`${result.errors.length} rows need attention. ${typeof result.errors[0]==='string'?result.errors[0]:result.errors[0].error||result.errors[0].message||''}`,true);
  }
  async function uploadDocuments(files){
    if(state.configDirty&&!(await saveSearch({rerender:false})))return;
    for(const file of files){if(file.size>8*1024*1024)throw new Error(`${file.name} is over 8 MB.`);const data=new FormData();data.append('file',file);await api(`/api/searches/${state.activeId}/documents`,{method:'POST',body:data});}
    await refresh();toast('Document added to your search.');
  }
  async function suppressionsDialog(){const result=await api('/api/suppressions');state.suppressions=Array.isArray(result)?result:result.suppressions||[];
    $('#small-dialog').innerHTML=`<div class="dialog-header"><div><span class="eyebrow">ACROSS ALL SEARCHES</span><h2 id="small-dialog-title">Do not contact</h2></div><button class="button icon-button" type="button" data-action="close-dialog" aria-label="Close">${icon('close')}</button></div><div class="dialog-body"><p class="suppression-note">Blocked addresses and domains are excluded from action exports.</p><form id="suppression-form" class="suppression-form"><div class="field-row">${select('Type','kind','email',[['email','Email'],['domain','Company domain']])}${field('Address or domain','value','',{required:true,placeholder:'person@company.com'})}</div>${field('Reason','reason','',{placeholder:'Optional'})}<button class="button primary" type="submit">Add to list</button></form><div class="suppression-list">${state.suppressions.length?state.suppressions.map(x=>`<div class="suppression-item"><div><strong>${esc(x.value)}</strong><span>${esc(x.reason||human(x.kind))}</span></div>${badge(x.kind)}</div>`).join(''):'<p class="text-small text-muted">No blocked contacts.</p>'}</div></div><div class="dialog-footer"><button class="button" type="button" data-action="suppression-import">Import CSV</button><button class="button primary" type="button" data-action="close-dialog">Done</button></div>`;openDialog('#small-dialog');
  }
  function helpDialog(){
    const providerRows=(state.session?.providers||[]).map(p=>`<div class="provider-card"><div><strong>${esc(p.name||p.id)}</strong>${badge(p.available?'CLI found':'Not found',p.available?'good':'')}</div><p>${esc(p.message||p.path||'Uses your locally installed CLI and existing sign-in.')}</p></div>`).join('');
    $('#small-dialog').innerHTML=`<div class="dialog-header"><div><span class="eyebrow">GOOSNAV LABS</span><h2 id="small-dialog-title">Setup & help.</h2></div><button class="button icon-button" type="button" data-action="close-dialog" aria-label="Close">${icon('close')}</button></div><div class="dialog-body"><h3>Your agents</h3>${providerRows||'<p>Install and sign in to Codex or Claude Code on this computer.</p>'}<p class="text-small text-muted">The app calls your signed-in CLI. Search details and résumé excerpts are sent to the provider selected for each task.</p><h3>Open your reports</h3><p>On a Mac, double-click <strong>Open Job Finder.command</strong>. On Windows, double-click <strong>Open Job Finder.cmd</strong>. On Linux, run <strong>Open Job Finder.sh</strong>. Keep the complete app folder beside the launch files.</p><p>First setup needs internet to download Python and the locked dependencies. Reopen the launcher to return to your saved searches. Use Quit to stop the local server.</p>${state.session?.data_dir?`<div class="help-data-path"><span>Local workspace</span><code>${esc(state.session.data_dir)}</code></div>`:''}</div><div class="dialog-footer"><button class="button quiet small" type="button" data-action="quit">Quit Job Finder</button><button class="button" type="button" data-action="suppressions">Do not contact list</button><button class="button primary" type="button" data-action="close-dialog">Got it</button></div>`;openDialog('#small-dialog');
  }

  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action]');if(!button){const row=event.target.closest('tr[data-opportunity]');if(row&&!event.target.closest('input,a,button'))openOpportunity(row.dataset.opportunity);return;}
    const action=button.dataset.action;
    if(action==='close-dialog'){closeDialog(button.closest('dialog'));return;}
    if(action==='home'){event.preventDefault();if(!leaveConfig())return;state.configDirty=false;state.view='opportunities';render();return;}
    if(action==='new'){if(!leaveConfig())return;newSearchDialog();return;}
    if(action==='help'){helpDialog();return;}
    if(action==='view'){
      if(!leaveConfig())return;state.configDirty=false;state.view=button.dataset.view;state.selected.clear();render();return;
    }
    if(action==='open-opportunity'){openOpportunity(button.dataset.id);return;}
    if(action==='detail-tab'){captureOpportunity();state.tab=button.dataset.tab;renderOpportunity();$(`#detail-tab-${state.tab}`).focus();return;}
    if(action==='clear-selection'){state.selected.clear();table();return;}
    if(action==='reset-filters'){state.filter='';state.kind='all';state.eligibility='all';render();return;}
    if(action==='toggle-log'){state.logOpen=!state.logOpen;renderRun();return;}
    if(action==='dismiss-run'){state.dismissed.add(state.run.id);renderRun();return;}
    if(action==='import'){importDialog();return;}
    if(action==='verification'){importDialog('verification');return;}
    if(action==='outcomes'){importDialog('outcomes');return;}
    if(action==='documents'){$('#documents-input').click();return;}
    if(action==='suppression-import'){$('#suppression-input').click();return;}
    busy('Working…',async()=>{
      if(action==='open-search'){if(leaveConfig())await loadSearch(button.dataset.id);}
      else if(action==='demo'){if(!leaveConfig())return;const result=await api('/api/demo',{method:'POST'});await listSearches();await loadSearch((result.search||result).id,{view:'opportunities'});toast('Demo ready. All people and companies are fictional.');}
      else if(action==='refresh'){if(!leaveConfig())return;state.configDirty=false;await refresh();}
      else if(action==='run')await startRun();
      else if(['pause-run','resume-run','cancel-run'].includes(action)){const verb=action.split('-')[0];const result=await api(`/api/runs/${state.run.id}/${verb}`,{method:'POST'});state.run=result.run||result;renderRun();await pollRun();}
      else if(action==='toggle-shortlist'){const o=opportunities().find(o=>o.id===button.dataset.id);await api(`/api/opportunities/${o.id}`,{method:'PATCH',body:{application_status:o.application_status==='shortlisted'?'new':'shortlisted'}});await refresh({quiet:true});}
      else if(action==='bulk-shortlist')await bulkUpdate({application_status:'shortlisted'});
      else if(action==='bulk-reject')await bulkUpdate({application_status:'not_interested'});
      else if(action==='save-opportunity')await saveOpportunity();
      else if(action==='shortlist-opportunity')await saveOpportunity({application_status:'shortlisted'});
      else if(action==='skip-opportunity')await saveOpportunity({application_status:'not_interested'});
      else if(action==='approve-opportunity')await saveOpportunity({status:'approved'});
      else if(action==='draft-from-detail'){
        if(state.dirty)await saveOpportunity();$('#lead-dialog').close();state.view='outreach';render();await startRun('draft');
      }
      else if(action==='export')await download(button.dataset.format,button.dataset.scope||state.exportScope);
      else if(action==='remove-document'){if(state.configDirty&&!(await saveSearch({rerender:false})))return;await api(`/api/searches/${state.activeId}/documents/${button.dataset.id}`,{method:'DELETE'});await refresh();toast('Document removed.');}
      else if(action==='suppressions')await suppressionsDialog();
      else if(action==='suppress-opportunity'){
        const o=state.working;if(!o.email&&!o.domain)throw new Error('Add a public email or company domain first.');
        await api('/api/suppressions',{method:'POST',body:{kind:o.email?'email':'domain',value:o.email||o.domain,reason:'User marked do not contact'}});state.dirty=false;$('#lead-dialog').close();await refresh();toast('Added to the do not contact list.');
      }
      else if(action==='quit'){
        if(!confirm('Close Job Finder? Active work will be paused.'))return;await api('/api/quit',{method:'POST'});$$('dialog[open]').forEach(d=>d.close());clearTimeout(state.pollTimer);$('#main').innerHTML='<div class="closed-workspace"><h1>Job Finder is closed.</h1></div>';$('#job-region').innerHTML='';
      }
      else if(action==='retry')await init();
    },button);
  });
  document.addEventListener('submit',event=>{
    const form=event.target;if(!['search-form','new-search-form','import-form','suppression-form'].includes(form.id))return;event.preventDefault();
    busy('Saving…',async()=>{
      if(form.id==='search-form'){if(await saveSearch())toast('Search saved.');}
      else if(form.id==='new-search-form'){
        const result=await api('/api/searches',{method:'POST',body:formSearch(form,defaults)});$('#campaign-dialog').close();await listSearches();await loadSearch((result.search||result).id,{view:'setup'});toast('Search created. Add your résumé to refine the match.');
      }
      else if(form.id==='import-form')await importFile(form);
      else if(form.id==='suppression-form'){
        const data=Object.fromEntries(new FormData(form));await api('/api/suppressions',{method:'POST',body:data});await suppressionsDialog();if(state.activeId)await refresh();toast('Added to the do not contact list.');
      }
    },event.submitter);
  });
  document.addEventListener('input',event=>{
    const el=event.target;
    if(el.id==='opportunity-search'){state.filter=el.value;table();}
    if(el.closest('#search-form')){state.configDirty=true;$('#save-message').textContent='Unsaved changes';}
    if(el.closest('#lead-dialog')&&el.name){state.dirty=true;$('#opportunity-save-indicator').textContent='Unsaved changes';
      if(el.name.startsWith('draft.')){const [,index,key]=el.name.split('.');const count=$(`[data-count="${el.name}"]`);if(count){const max=key==='subject'?5:Number(index)===0?90:65;count.textContent=`${words(el.value)}/${max} words`;count.classList.toggle('over',words(el.value)>max);}if(key==='body')sizeDrafts();}
    }
  });
  document.addEventListener('change',event=>{
    const el=event.target;
    if(el.id==='active-search'){if(leaveConfig())busy('Open search…',()=>loadSearch(el.value));else el.value=state.activeId;}
    else if(el.id==='kind-filter'){state.kind=el.value;table();}
    else if(el.id==='eligibility-filter'){state.eligibility=el.value;table();}
    else if(el.id==='provider-select')$('#provider-notice').innerHTML=providerNotice(el.value);
    else if(el.dataset.select){if(el.checked)state.selected.add(el.dataset.select);else state.selected.delete(el.dataset.select);table();}
    else if(el.id==='select-all'){for(const o of filtered()){if(el.checked)state.selected.add(o.id);else state.selected.delete(o.id);}table();}
    else if(el.name==='export-scope'){state.exportScope=el.value;render();$(`input[name="export-scope"][value="${state.exportScope}"]`).focus();}
    else if(el.id==='documents-input'){const files=[...el.files];el.value='';if(files.length)busy('Reading document…',()=>uploadDocuments(files));}
    else if(el.id==='suppression-input'){
      const file=el.files[0];el.value='';if(file)busy('Importing…',async()=>{const data=new FormData();data.append('file',file);await api('/api/suppressions/import',{method:'POST',body:data});await suppressionsDialog();if(state.activeId)await refresh();toast('Do not contact list imported.');});
    }
    if(el.closest('#search-form')){state.configDirty=true;$('#save-message').textContent='Unsaved changes';}
    if(el.closest('#lead-dialog')&&el.name){state.dirty=true;$('#opportunity-save-indicator').textContent='Unsaved changes';}
  });
  document.addEventListener('keydown',event=>{
    if(event.target.matches('[role="tab"]')&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      event.preventDefault();const tabs=$$('[role="tab"]',$('#lead-dialog'));let i=tabs.indexOf(event.target);i=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[i].click();
    }
  });
  $$('dialog').forEach(dialog=>dialog.addEventListener('cancel',event=>{event.preventDefault();closeDialog(dialog);}));
  window.addEventListener('resize',sizeDrafts);
  window.addEventListener('beforeunload',event=>{if(state.configDirty||state.dirty){event.preventDefault();event.returnValue='';}});
  async function init(){
    $('#request-status').textContent='Opening workspace…';
    try{
      state.session=await api('/api/session');state.session.data_dir=state.session.data_dir||state.session.data_directory;await listSearches();let saved='';try{saved=localStorage.getItem('job-finder-active-search')||'';}catch{}
      const first=state.searches.find(s=>s.id===saved)||state.searches[0];if(first)await loadSearch(first.id);else render();
    }catch(error){$('#main').innerHTML=`<section class="empty-workspace"><h1>Connection lost</h1><p>${esc(error.message)}</p><button class="button primary" type="button" data-action="retry">Retry</button></section>`;}
    finally{$('#request-status').textContent='';}
  }
  init();
})();
