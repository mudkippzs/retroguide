"""ROAMER: the mobile-first RetroGuide PWA (iPhone over Tailscale).

Self-contained single page like ``web.py`` (no external JS/CSS so it works
with no internet), but designed for a phone: a bottom tab bar with three
explicit modes — LIVE (tune channels), GUIDE (magazine listing), BINGE (the
full catalog + queue parity with the desktop app). Playback is the native iOS
player over HLS: live via the channel sessions, on-demand via ROAMER-VOD.

Design tokens come from ``tokens.py`` (shared with the desktop HUD) — never
hardcode a colour here.
"""
from __future__ import annotations

import json

from .tokens import TOKENS_CSS

_PAGE = r"""<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=theme-color content="#070b12">
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-status-bar-style content=black-translucent>
<meta name=apple-mobile-web-app-title content="RetroGuide">
<link rel=manifest href="/manifest.webmanifest">
<link rel=apple-touch-icon href="/icon.png">
<title>RetroGuide</title>
<style>
__TOKENS__
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{
  margin:0; background:
    radial-gradient(900px 420px at 80% -10%, var(--bloom), transparent 60%),
    var(--bg);
  color:var(--text); font-family:var(--font);
  -webkit-font-smoothing:antialiased;
  overscroll-behavior:none;
}
.app{display:flex;flex-direction:column;height:100dvh}

/* ---- header ---- */
header{display:flex;align-items:center;gap:10px;
  padding:calc(8px + env(safe-area-inset-top)) 14px 8px;
  border-bottom:1px solid var(--border)}
.brand{font-weight:800;letter-spacing:1px;font-size:16px}
.brand b{color:var(--accent)}
.clock{margin-left:auto;font-family:var(--mono);font-size:13px;color:var(--accent)}
.skin select{background:var(--panel2);color:var(--text);border:1px solid var(--border);
  border-radius:7px;padding:5px 4px;font-size:11px;font-family:var(--font)}
.tg{background:var(--panel2);color:var(--muted);border:1px solid var(--border);
  border-radius:7px;padding:5px 8px;font-size:11px;font-weight:800;letter-spacing:1px}
.tg.on{color:#06121a;background:var(--accent);border-color:var(--accent)}

/* ---- main + tab bar ---- */
main{flex:1;min-height:0;position:relative}
.tab{position:absolute;inset:0;overflow-y:auto;padding:12px 12px 20px;
  -webkit-overflow-scrolling:touch}
.tab[hidden]{display:none}
.tabbar{flex:0 0 auto;display:flex;border-top:1px solid var(--border);
  background:var(--panel);padding-bottom:env(safe-area-inset-bottom)}
.tabbar button{flex:1;background:none;border:0;color:var(--muted);
  font-family:var(--font);font-size:10px;font-weight:800;letter-spacing:2px;
  padding:10px 0 12px;display:flex;flex-direction:column;align-items:center;gap:3px}
.tabbar button .ico{font-size:17px;line-height:1;filter:grayscale(1) opacity(.6)}
.tabbar button.on{color:var(--accent)}
.tabbar button.on .ico{filter:none}

/* ---- LIVE ---- */
.screen{position:relative;background:#000;border:1px solid var(--border);
  border-radius:14px;overflow:hidden;aspect-ratio:16/9;
  box-shadow:0 12px 40px rgba(0,0,0,.5)}
.screen video{display:block;width:100%;height:100%;object-fit:contain;background:#000}
.crtfx{position:absolute;inset:0;z-index:2;pointer-events:none;display:none}
html[data-crt="on"] .crtfx{display:block}
html[data-crt="on"] .crtfx::before{content:"";position:absolute;inset:0;
  background:repeating-linear-gradient(rgba(0,0,0,calc(var(--scan)*1.4)) 0 1px,
    transparent 1px 3px);mix-blend-mode:multiply}
html[data-crt="on"] .crtfx::after{content:"";position:absolute;inset:0;
  background:radial-gradient(120% 120% at 50% 50%,transparent 58%,rgba(0,0,0,.55) 100%)}
html[data-bw="on"] .screen video{filter:grayscale(1) contrast(1.08) brightness(1.02)!important}
.badge{position:absolute;top:10px;right:10px;z-index:4;font-size:9px;
  font-weight:800;letter-spacing:2px;color:#fff;background:var(--now);
  padding:3px 8px;border-radius:6px}
.bug{position:absolute;top:10px;left:10px;z-index:4;height:30px;width:auto;
  max-width:96px;opacity:.9;filter:drop-shadow(0 2px 6px rgba(0,0,0,.7))}
.vtools{position:absolute;right:8px;bottom:44px;z-index:5;display:flex;
  flex-direction:column;gap:8px}
.vtools button{width:38px;height:38px;border-radius:10px;font-size:10px;
  font-weight:800;letter-spacing:.5px;background:rgba(14,21,33,.82);
  color:var(--text);border:1px solid var(--border);backdrop-filter:blur(4px)}
.vtools button.on{color:#06121a;background:var(--accent);border-color:var(--accent)}

.nowcard{margin-top:12px;background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border);border-radius:14px;padding:14px 16px}
.eyebrow{font-size:9px;letter-spacing:2px;font-weight:800;color:var(--accent)}
.nowcard h2{margin:5px 0 2px;font-size:18px;font-weight:800;line-height:1.15}
.nowcard .sub{color:var(--muted);font-size:12.5px}
.nowcard .blurb{margin-top:8px;color:var(--text);opacity:.8;font-size:12.5px;
  line-height:1.45;display:-webkit-box;-webkit-line-clamp:3;
  -webkit-box-orient:vertical;overflow:hidden}
.bar{margin-top:12px;height:5px;border-radius:5px;background:rgba(0,0,0,.4);
  overflow:hidden;border:1px solid var(--border)}
.bar > i{display:block;height:100%;width:0;
  background:linear-gradient(90deg,var(--accent),transparent 160%)}
.times{display:flex;justify-content:space-between;margin-top:5px;font-size:10.5px;
  color:var(--muted);font-family:var(--mono)}
.nextup{margin-top:10px;font-size:11.5px;color:var(--muted)}
.nextup b{color:var(--text)}

/* channel rail (LIVE + GUIDE) */
.chrail{display:flex;gap:8px;overflow-x:auto;padding:12px 2px 4px;
  -webkit-overflow-scrolling:touch}
.chip{flex:0 0 auto;min-width:150px;max-width:190px;background:var(--panel);
  border:1px solid var(--border);border-radius:12px;padding:9px 11px;
  cursor:pointer}
.chip.active{border-color:var(--ca,var(--accent));
  box-shadow:0 0 0 1px var(--ca,var(--accent)) inset}
.chip .cn{font-weight:700;font-size:12.5px;display:flex;align-items:center;gap:6px}
.chip .cs{color:var(--muted);font-size:11px;margin-top:2px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.chip .mini{margin-top:6px;height:3px;border-radius:3px;background:rgba(0,0,0,.4);
  overflow:hidden}
.chip .mini > i{display:block;height:100%;width:0;background:var(--ca,var(--accent))}
.clogo{width:18px;height:18px;object-fit:contain;border-radius:4px}

/* ---- GUIDE ---- */
.daypart{font-size:9.5px;letter-spacing:2px;font-weight:800;color:var(--accent);
  padding:14px 4px 5px}
.g-row{display:flex;gap:11px;padding:9px 8px;border-radius:10px;
  border:1px solid transparent}
.g-row.now{background:rgba(255,94,138,.07);border-color:var(--now)}
.g-row .gt{font-family:var(--mono);font-size:11.5px;color:var(--muted);
  width:44px;flex:0 0 auto;padding-top:1px}
.g-row.now .gt{color:var(--now)}
.g-row .gm{flex:1;min-width:0}
.g-row .gtitle{font-size:13.5px;font-weight:600}
.g-row .gsub{font-size:11.5px;color:var(--muted)}
.g-row .gblurb{font-size:11px;color:var(--muted);margin-top:2px;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.g-row.missing{opacity:.45}
.onair{font-size:8.5px;font-weight:800;letter-spacing:1px;color:var(--now);
  border:1px solid var(--now);padding:1px 5px;border-radius:5px}
.empty{color:var(--muted);font-size:13px;padding:32px 20px;text-align:center;
  line-height:1.5}

/* ---- BINGE ---- */
.search{width:100%;background:var(--panel2);border:1px solid var(--border);
  border-radius:11px;color:var(--text);font-size:15px;font-family:var(--font);
  padding:11px 13px;margin-bottom:10px}
.seg{display:flex;gap:5px;margin-bottom:10px}
.seg button{flex:1;background:var(--panel2);border:1px solid var(--border);
  color:var(--muted);font-size:10.5px;font-weight:800;letter-spacing:1.5px;
  padding:8px 0;border-radius:9px;font-family:var(--font)}
.seg button.on{color:#06121a;background:var(--accent);border-color:var(--accent)}
.b-row{display:flex;align-items:center;gap:11px;padding:11px 10px;
  border-radius:11px;border:1px solid transparent;cursor:pointer}
.b-row:active{background:var(--panel2)}
.b-row + .b-row{border-top:1px solid var(--border)}
.b-row .bm{flex:1;min-width:0}
.b-row .bt{font-size:14px;font-weight:600}
.b-row .bs{font-size:11.5px;color:var(--muted);margin-top:1px}
.b-row .bmeta{font-size:10.5px;color:var(--muted);font-family:var(--mono);
  text-align:right;flex:0 0 auto}
.wcheck{color:var(--accent);font-weight:800}
.resume{font-size:9px;color:var(--now);font-weight:800;letter-spacing:.5px}
.b-back{display:inline-block;color:var(--accent);font-size:13px;font-weight:700;
  padding:6px 2px 10px;cursor:pointer}
.shdr{font-size:9.5px;letter-spacing:2px;font-weight:800;color:var(--accent);
  padding:12px 4px 5px}
.qtools{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.qtools button{background:var(--panel2);border:1px solid var(--border);
  color:var(--text);font-size:10px;font-weight:800;letter-spacing:.5px;
  padding:8px 10px;border-radius:9px;font-family:var(--font)}
.qtools button.on{color:#06121a;background:var(--accent);border-color:var(--accent)}
.qtools button.play{background:var(--accent);color:#06121a;border-color:var(--accent)}
.qbtn{width:34px;height:30px;border-radius:8px;background:var(--panel2);
  border:1px solid var(--border);color:var(--muted);font-size:13px;flex:0 0 auto}

/* ---- VOD player overlay ---- */
.pwrap{position:fixed;inset:0;z-index:40;background:#000;display:flex;
  flex-direction:column}
.pwrap[hidden]{display:none}
.pstage{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
.pstage video{flex:1;min-height:0;width:100%;object-fit:contain;background:#000}
.ptop{position:absolute;top:0;left:0;right:0;z-index:6;display:flex;
  align-items:center;gap:10px;padding:calc(10px + env(safe-area-inset-top)) 12px 10px;
  background:linear-gradient(rgba(0,0,0,.72),transparent);pointer-events:none}
.ptop > *{pointer-events:auto}
.ptop .pt{flex:1;min-width:0}
.ptop .ptt{font-size:13.5px;font-weight:700;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.ptop .pts{font-size:11px;color:#c7d0de;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.pbtn{width:38px;height:38px;border-radius:10px;background:rgba(14,21,33,.82);
  color:var(--text);border:1px solid var(--border);font-size:14px;font-weight:800;
  backdrop-filter:blur(4px)}
.pbtn.on{color:#06121a;background:var(--accent);border-color:var(--accent)}
.pstatus{position:absolute;left:12px;bottom:calc(52px + env(safe-area-inset-bottom));
  z-index:6;font-size:10.5px;font-family:var(--mono);color:var(--accent);
  background:rgba(14,21,33,.82);border:1px solid var(--border);
  padding:5px 9px;border-radius:8px;backdrop-filter:blur(4px)}
.pstatus[hidden]{display:none}

/* ---- action sheet ---- */
.scrim{position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.55);
  display:flex;align-items:flex-end}
.scrim[hidden]{display:none}
.sheet{width:100%;background:var(--panel);border-top:1px solid var(--border);
  border-radius:18px 18px 0 0;padding:14px 14px calc(16px + env(safe-area-inset-bottom))}
.sheet h4{margin:0 0 4px;font-size:15px}
.sheet .sh-sub{color:var(--muted);font-size:12px;margin-bottom:10px}
.sheet button{display:block;width:100%;text-align:left;background:var(--panel2);
  border:1px solid var(--border);color:var(--text);font-size:14.5px;
  font-family:var(--font);padding:13px 14px;border-radius:12px;margin-top:8px}
.sheet button.accent{background:var(--accent);border-color:var(--accent);
  color:#06121a;font-weight:800}
.sheet button.cancel{background:none;border-color:transparent;color:var(--muted);
  text-align:center}
.toast{position:fixed;left:50%;bottom:calc(86px + env(safe-area-inset-bottom));
  transform:translateX(-50%);z-index:60;background:var(--panel2);
  border:1px solid var(--accent);color:var(--text);padding:10px 16px;
  border-radius:10px;font-size:13px;opacity:0;transition:.25s;
  box-shadow:0 10px 30px rgba(0,0,0,.5);white-space:nowrap;max-width:92vw;
  overflow:hidden;text-overflow:ellipsis}
.toast.show{opacity:1}
::-webkit-scrollbar{display:none}
</style>
</head>
<body>
<div class=app>
  <header>
    <div class=brand>RETRO<b>GUIDE</b></div>
    <div class=skin>
      <select id=era title="Broadcast era">
        <option value=70s>70s</option><option value=80s>80s</option>
        <option value=90s>90s</option><option value=00s>00s</option>
      </select>
      <button id=crt class=tg title="CRT scanlines">CRT</button>
      <button id=bw class=tg title="Black &amp; white picture">B&amp;W</button>
    </div>
    <div class=clock id=clock>--:--</div>
  </header>
  <main>
    <section class=tab id=tab-live>
      <div class=screen>
        <video id=lv controls playsinline></video>
        <div class=crtfx></div>
        <img class=bug id=bug alt="" style=display:none>
        <span class=badge id=badge style=display:none>ON AIR</span>
        <div class=vtools>
          <button id=vb-cc title="English subtitles (burned in)">CC</button>
          <button id=vb-ar title="Aspect ratio: fit / fill / stretch">AR</button>
          <button id=vb-pip title="Picture in picture">PiP</button>
        </div>
      </div>
      <div class=nowcard>
        <div class=eyebrow id=eyebrow>NOW PLAYING</div>
        <h2 id=ntitle>Tuning in&hellip;</h2>
        <div class=sub id=nsub></div>
        <div class=blurb id=nblurb></div>
        <div class=bar><i id=nprog></i></div>
        <div class=times><span id=tstart></span><span id=tend></span></div>
        <div class=nextup id=nextup></div>
      </div>
      <div class=chrail id=chrail></div>
    </section>
    <section class=tab id=tab-guide hidden>
      <div class=chrail id=gchrail></div>
      <div id=guidelist></div>
    </section>
    <section class=tab id=tab-binge hidden>
      <input class=search id=search placeholder="Search your library&hellip;">
      <div class=seg>
        <button id=seg-series class=on>SERIES</button>
        <button id=seg-films>FILMS</button>
        <button id=seg-queue>QUEUE</button>
      </div>
      <div id=bingelist></div>
    </section>
  </main>
  <nav class=tabbar>
    <button id=tb-live class=on><span class=ico>📺</span>LIVE</button>
    <button id=tb-guide><span class=ico>📖</span>GUIDE</button>
    <button id=tb-binge><span class=ico>🍿</span>BINGE</button>
  </nav>
</div>

<div class=pwrap id=pwrap hidden>
  <div class=pstage>
    <video id=pv controls playsinline></video>
    <div class=ptop>
      <button class=pbtn id=pclose title="Close player">✕</button>
      <div class=pt><div class=ptt id=ptt></div><div class=pts id=pts></div></div>
      <button class=pbtn id=pcc title="Subtitles" style=display:none>CC</button>
      <button class=pbtn id=par title="Aspect ratio: fit / fill / stretch">AR</button>
    </div>
    <div class=pstatus id=pstatus hidden></div>
  </div>
</div>

<div class=scrim id=scrim hidden><div class=sheet id=sheet></div></div>
<div class=toast id=toast></div>

<script>
const $ = s => document.querySelector(s);
const lv = $('#lv'), pv = $('#pv');

/* Only Apple devices need HLS for LIVE (same rule as the desktop page). VOD is
   always HLS. */
const _ua = navigator.userAgent || '';
const IS_APPLE =
  /iPad|iPhone|iPod/.test(_ua) ||
  (navigator.platform === 'MacIntel' && (navigator.maxTouchPoints || 0) > 1) ||
  (/Macintosh/.test(_ua) && /Safari/.test(_ua) && !/Chrome|Chromium|Edg|OPR|Firefox/.test(_ua));
const LIVE_HLS = IS_APPLE &&
  (lv.canPlayType('application/vnd.apple.mpegurl') !== '' ||
   lv.canPlayType('application/x-mpegURL') !== '');

/* ---- retro skin (shared localStorage keys with the desktop page) ---- */
const root = document.documentElement;
function applySkin(){
  root.dataset.era = localStorage.retro_era || "__ERA__";
  root.dataset.crt = localStorage.retro_crt || "__CRT__";
  root.dataset.bw  = localStorage.retro_bw  || "__BW__";
  $('#era').value = root.dataset.era;
  $('#crt').classList.toggle('on', root.dataset.crt==='on');
  $('#bw').classList.toggle('on', root.dataset.bw==='on');
}
$('#era').onchange = e => { localStorage.retro_era = e.target.value; applySkin(); };
$('#crt').onclick = () => { localStorage.retro_crt = root.dataset.crt==='on'?'off':'on'; applySkin(); };
$('#bw').onclick  = () => { localStorage.retro_bw  = root.dataset.bw==='on'?'off':'on'; applySkin(); };
applySkin();

/* ---- helpers ---- */
function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function pad(n){ return String(n).padStart(2,'0'); }
function fmt(ts){ const d=new Date(ts*1000); return pad(d.getHours())+':'+pad(d.getMinutes()); }
function clamp(x){ return Math.max(0,Math.min(1,x)); }
function hms(sec){
  sec = Math.max(0, Math.floor(sec||0));
  const h = Math.floor(sec/3600), m = Math.floor(sec%3600/60);
  return h ? h+'h '+pad(m)+'m' : m+'m';
}
function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.classList.remove('show'),2400); }
async function api(path, body){
  const opt = body===undefined ? {} :
    {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
  const r = await fetch(path, opt);
  const j = await r.json().catch(()=>({}));
  if(!r.ok){ const e = new Error(j.error||('HTTP '+r.status)); e.status=r.status; throw e; }
  return j;
}

/* ---- aspect ratio: fit / fill / stretch (inline; fullscreen is native) ---- */
const AR_MODES = ['contain','cover','fill'];
const AR_NAMES = {contain:'Fit', cover:'Fill', fill:'Stretch'};
let arMode = localStorage.retro_ar || 'contain';
function applyAR(){
  lv.style.objectFit = arMode; pv.style.objectFit = arMode;
  $('#vb-ar').textContent = AR_NAMES[arMode];
  $('#par').textContent = AR_NAMES[arMode];
}
$('#vb-ar').onclick = () => {
  arMode = AR_MODES[(AR_MODES.indexOf(arMode)+1)%AR_MODES.length];
  localStorage.retro_ar = arMode; applyAR();
  toast('Aspect: '+AR_NAMES[arMode]);
};
$('#par').onclick = $('#vb-ar').onclick;
applyAR();

/* ---- tabs ---- */
let tab = 'live';
function showTab(name){
  tab = name;
  ['live','guide','binge'].forEach(n=>{
    $('#tab-'+n).hidden = n!==name;
    $('#tb-'+n).classList.toggle('on', n===name);
  });
  if(name==='guide') loadGuide(curSlug || (CH[0]&&CH[0].slug));
  if(name==='binge') loadBinge();
}
$('#tb-live').onclick = () => showTab('live');
$('#tb-guide').onclick = () => showTab('guide');
$('#tb-binge').onclick = () => showTab('binge');

/* ================= LIVE ================= */
let CH = [], curSlug = null, skew = 0;
let subsOn = localStorage.retro_subs === 'on';
function applySubs(){ $('#vb-cc').classList.toggle('on', subsOn); }
$('#vb-cc').onclick = () => {
  subsOn = !subsOn; localStorage.retro_subs = subsOn ? 'on' : 'off';
  applySubs(); if(curSlug) loadLive(curSlug);
  toast(subsOn ? 'Subtitles: English (burned in)' : 'Subtitles off');
};
applySubs();

if(!document.pictureInPictureEnabled){ $('#vb-pip').style.display='none'; }
$('#vb-pip').onclick = async () => {
  try{
    if(document.pictureInPictureElement) await document.exitPictureInPicture();
    else await lv.requestPictureInPicture();
  }catch(e){ toast('Picture-in-picture unavailable'); }
};

let errDelay = 2500, errTimer = null;
function loadLive(slug){
  clearTimeout(errTimer); errTimer = null;
  const path = LIVE_HLS ? '/index.m3u8' : '/live.mp4';
  lv.src = '/c/'+slug+path+'?t='+Date.now()+(subsOn?'&subs=1':'');
  lv.load(); lv.play().catch(()=>{});
}
lv.addEventListener('ended', ()=>{ if(curSlug) loadLive(curSlug); refreshNow(); });
lv.addEventListener('playing', ()=>{ errDelay=2500; });
lv.addEventListener('error', ()=>{
  if(!curSlug) return;
  if(lv.error && lv.error.code === 1) return;   // our own abort on retune
  clearTimeout(errTimer);
  errTimer = setTimeout(()=>{ if(curSlug) loadLive(curSlug); }, errDelay);
  errDelay = Math.min(30000, errDelay*2);
});

function selectChannel(slug){
  if(!slug) return;
  curSlug = slug;
  const c = CH.find(x=>x.slug===slug);
  if(c){ toast('Tuned to '+c.name);
    const bug=$('#bug');
    if(c.logo){ bug.src='/logo/'+slug; bug.style.display='block'; }
    else { bug.style.display='none'; } }
  document.querySelectorAll('.chip').forEach(el=>
    el.classList.toggle('active', el.dataset.slug===slug));
  loadLive(slug);
  renderNow();
  if(tab==='guide') loadGuide(slug);
}

function renderChannels(boxId){
  const box = $(boxId); box.innerHTML='';
  CH.forEach(c=>{
    const el = document.createElement('div');
    el.className='chip'; el.dataset.slug=c.slug; el.style.setProperty('--ca',c.accent);
    const badge = c.logo
      ? `<img class=clogo src="/logo/${encodeURIComponent(c.slug)}" alt="">` : '';
    el.innerHTML = `<div class=cn>${badge}${esc(c.name)}</div>
      <div class=cs data-show></div><div class=mini><i data-mini></i></div>`;
    el.onclick = ()=>{ selectChannel(c.slug); if(boxId==='#gchrail') showTab('live'); };
    box.appendChild(el);
  });
}

function renderNow(){
  const c = CH.find(x=>x.slug===curSlug);
  const now = c && c.now;
  if(!now){
    $('#eyebrow').textContent='OFF AIR'; $('#ntitle').textContent='Nothing scheduled';
    $('#nsub').textContent=''; $('#nblurb').textContent='';
    $('#badge').style.display='none'; $('#tstart').textContent='';
    $('#tend').textContent=''; $('#nextup').textContent=''; return;
  }
  $('#eyebrow').textContent=(now.daypart||'NOW PLAYING').toUpperCase();
  $('#ntitle').textContent=now.title;
  $('#nsub').textContent=now.subtitle||'';
  $('#nblurb').textContent=now.blurb||'';
  $('#badge').style.display='block';
  $('#tstart').textContent=fmt(now.start);
  $('#tend').textContent=fmt(now.end);
  $('#nextup').innerHTML = c.next
    ? 'Next <b>'+esc(c.next.title)+'</b>'+(c.next.subtitle?' · '+esc(c.next.subtitle):'')+' at '+fmt(c.next.start)
    : '';
}

async function refreshNow(){
  try{
    const j = await api('/api/now');
    skew = j.server_now - Date.now()/1000;
    CH = j.channels;
    if(!curSlug && CH[0]) curSlug = CH[0].slug;
    renderChannels('#chrail'); renderChannels('#gchrail');
    document.querySelectorAll('.chip').forEach(el=>
      el.classList.toggle('active', el.dataset.slug===curSlug));
    renderNow();
  }catch(e){}
}
function liveNow(){ return Date.now()/1000 + skew; }
function animate(){
  const c = CH.find(x=>x.slug===curSlug); const now = liveNow();
  if(c && c.now){
    $('#nprog').style.width=(clamp((now-c.now.start)/(c.now.end-c.now.start))*100).toFixed(2)+'%';
  }
  document.querySelectorAll('.chip').forEach(el=>{
    const cc = CH.find(x=>x.slug===el.dataset.slug); if(!cc) return;
    const show = el.querySelector('[data-show]'), mini = el.querySelector('[data-mini]');
    if(cc.now){
      show.textContent = cc.now.title + (cc.now.subtitle?' · '+cc.now.subtitle.split(' - ')[0]:'');
      mini.style.width = (clamp((now-cc.now.start)/(cc.now.end-cc.now.start))*100).toFixed(1)+'%';
    } else { show.textContent = 'Off air'; mini.style.width='0'; }
  });
  requestAnimationFrame(animate);
}

/* ================= GUIDE ================= */
let guideSlug = null;
async function loadGuide(slug){
  if(!slug) return;
  guideSlug = slug;
  try{
    const j = await api('/api/schedule/'+slug);
    renderGuide(j);
  }catch(e){}
}
function renderGuide(j){
  const box = $('#guidelist');
  const now = liveNow(); let html=''; let lastDay=null;
  (j.programs||[]).forEach(p=>{
    if(!p) return;
    if(p.daypart && p.daypart!==lastDay){
      html += `<div class=daypart>${esc(p.daypart.toUpperCase())}</div>`; lastDay=p.daypart;
    }
    const on = p.start<=now && now<p.end;
    const miss = p.playable===false;
    html += `<div class="g-row${on?' now':''}${miss?' missing':''}">
      <div class=gt>${fmt(p.start)}</div>
      <div class=gm><div class=gtitle>${esc(p.title)}${on?' <span class=onair>ON AIR</span>':''}${miss?' ⚠':''}</div>
      <div class=gsub>${esc(p.subtitle||'')}</div>
      ${p.blurb?`<div class=gblurb>${esc(p.blurb)}</div>`:''}</div></div>`;
  });
  box.innerHTML = html || '<div class=empty>No upcoming programs on this channel.</div>';
  const onRow = box.querySelector('.g-row.now');
  if(onRow) onRow.scrollIntoView({block:'center'});
}

/* ================= BINGE ================= */
let CAT = null, bingeSeg = 'series', drill = null;
$('#seg-series').onclick = ()=>{ bingeSeg='series'; drill=null; renderBinge(); };
$('#seg-films').onclick = ()=>{ bingeSeg='films'; drill=null; renderBinge(); };
$('#seg-queue').onclick = ()=>{ bingeSeg='queue'; drill=null; renderBinge(); };
$('#search').oninput = () => renderBinge();

async function loadBinge(){
  if(!CAT){
    try{ CAT = await api('/api/catalog'); }catch(e){ return; }
  }
  renderBinge();
}

function rowHTML(title, sub, meta, extra){
  return `<div class=bm><div class=bt>${title}</div>
    <div class=bs>${sub||''}</div></div>
    <div class=bmeta>${meta||''}${extra||''}</div>`;
}

function renderBinge(){
  ['series','films','queue'].forEach(n=>
    $('#seg-'+n).classList.toggle('on', n===bingeSeg));
  const box = $('#bingelist');
  const q = ($('#search').value||'').toLowerCase();
  if(!CAT){ box.innerHTML='<div class=empty>Loading your library&hellip;</div>'; return; }
  if(bingeSeg==='queue'){ renderQueue(box); return; }
  if(drill){ renderDrill(box); return; }
  const items = bingeSeg==='series' ? CAT.series : CAT.movies;
  const list = items.filter(x=>!q || x.name.toLowerCase().includes(q));
  if(!list.length){
    box.innerHTML = `<div class=empty>${q?'Nothing matches that search.':'Your library is empty here.'}</div>`;
    return;
  }
  box.innerHTML='';
  list.forEach(it=>{
    const el = document.createElement('div');
    el.className='b-row';
    if(bingeSeg==='series'){
      const watched = it.n_watched ? ` <span class=wcheck>✓${it.n_watched===it.n_episodes?' ALL':''}</span>` : '';
      el.innerHTML = rowHTML(esc(it.name)+watched,
        esc([it.year, it.bucket].filter(Boolean).join(' · ')),
        `${it.n_playable}/${it.n_episodes} eps`);
      el.onclick = ()=>{ drill=it; renderBinge(); };
    }else{
      const resume = it.position ? `<div class=resume>RESUME ${hms(it.position)}</div>` : '';
      const w = it.watched ? ' <span class=wcheck>✓</span>' : '';
      el.innerHTML = rowHTML(esc(it.name)+w,
        esc([it.year, hms(it.duration)].filter(Boolean).join(' · '))+resume,
        it.playable?'':'unavailable');
      el.onclick = ()=>{ if(it.playable) filmSheet(it); else toast('File not available'); };
    }
    box.appendChild(el);
  });
}

async function renderDrill(box){
  box.innerHTML = `<span class=b-back id=bback>‹ Library</span>
    <div class=empty>Loading ${esc(drill.name)}&hellip;</div>`;
  $('#bback').onclick = ()=>{ drill=null; renderBinge(); };
  let d;
  try{ d = await api('/api/catalog/series/'+drill.id); }
  catch(e){ box.innerHTML='<div class=empty>Could not load series.</div>'; return; }
  let html = `<span class=b-back id=bback2>‹ Library</span>
    <div class=daypart>${esc(d.name.toUpperCase())}</div>`;
  d.seasons.forEach(s=>{
    html += `<div class=shdr>SEASON ${s.season===0?'SPECIALS':s.season}</div>`;
    s.episodes.forEach(ep=>{
      const w = ep.watched ? ' <span class=wcheck>✓</span>' : '';
      const resume = ep.position ? `<div class=resume>RESUME ${hms(ep.position)}</div>` : '';
      html += `<div class="b-row ep" data-eid="${ep.id}">
        ${rowHTML('E'+pad(ep.episode)+' — '+esc(ep.name||('Episode '+ep.episode))+w,
                  resume || esc(ep.overview||'').slice(0,80),
                  ep.playable ? hms(ep.duration) : 'unavailable')}</div>`;
    });
  });
  box.innerHTML = html;
  $('#bback2').onclick = ()=>{ drill=null; renderBinge(); };
  box.querySelectorAll('.b-row.ep').forEach(el=>{
    el.onclick = ()=>{
      const eid = +el.dataset.eid;
      const ep = d.seasons.flatMap(s=>s.episodes).find(e=>e.id===eid);
      if(!ep.playable){ toast('File not available'); return; }
      episodeSheet(d, ep);
    };
  });
}

/* ---- action sheets ---- */
function sheet(title, sub, buttons){
  const scrim = $('#scrim'), sh = $('#sheet');
  sh.innerHTML = `<h4>${esc(title)}</h4><div class=sh-sub>${esc(sub||'')}</div>`;
  buttons.forEach(([label, cls, fn])=>{
    const b = document.createElement('button');
    b.textContent = label; if(cls) b.className = cls;
    b.onclick = ()=>{ scrim.hidden = true; fn(); };
    sh.appendChild(b);
  });
  const cancel = document.createElement('button');
  cancel.textContent = 'Cancel'; cancel.className='cancel';
  cancel.onclick = ()=>{ scrim.hidden = true; };
  sh.appendChild(cancel);
  scrim.hidden = false;
  scrim.onclick = e=>{ if(e.target===scrim) scrim.hidden = true; };
}

function episodeSheet(series, ep){
  const sub = `S${pad(ep.season===0?0:ep.season)}E${pad(ep.episode)}${ep.duration?' · '+hms(ep.duration):''}`;
  const btns = [];
  btns.push([ep.position ? `Resume from ${hms(ep.position)}` : 'Play', 'accent',
    ()=>playVOD({episode_id: ep.id})]);
  if(ep.position) btns.push(['Play from start', '', ()=>playVOD({episode_id: ep.id, fresh:true})]);
  btns.push(['Add episode to queue', '', ()=>queueAdd({kind:'episode', title_id:series.id, episode_id:ep.id})]);
  btns.push(['Add season to queue', '', ()=>queueAdd({kind:'season', title_id:series.id, season:ep.season})]);
  btns.push(['Add whole series to queue', '', ()=>queueAdd({kind:'series', title_id:series.id})]);
  sheet(series.name, sub, btns);
}
function filmSheet(f){
  const btns = [];
  btns.push([f.position ? `Resume from ${hms(f.position)}` : 'Play', 'accent',
    ()=>playVOD({title_id: f.id})]);
  if(f.position) btns.push(['Play from start', '', ()=>playVOD({title_id: f.id, fresh:true})]);
  btns.push(['Add to queue', '', ()=>queueAdd({kind:'film', title_id:f.id})]);
  sheet(f.name, [f.year, hms(f.duration)].filter(Boolean).join(' · '), btns);
}

/* ---- queue ---- */
let qRandom = localStorage.retro_qrandom === 'on';
let qRepeat = localStorage.retro_qrepeat === 'on';
async function queueAdd(body){
  try{
    await api('/api/queue/add', body);
    toast('Added to queue');
    if(bingeSeg==='queue') renderBinge();
  }catch(e){ toast('Could not add'); }
}
async function renderQueue(box){
  let q;
  try{ q = await api('/api/queue'); }catch(e){ box.innerHTML='<div class=empty>Queue unavailable.</div>'; return; }
  let html = `<div class=qtools>
    <button id=qplay class=play>▶ PLAY</button>
    <button id=qshuffle>SHUFFLE</button>
    <button id=qrandom${qRandom?' class=on':''}>RANDOM</button>
    <button id=qrepeat${qRepeat?' class=on':''}>REPEAT</button>
    <button id=qtheme>🎲 THEME</button>
    <button id=qsave>SAVE</button>
    <button id=qload>LOAD</button>
    <button id=qclear>CLEAR</button></div>`;
  if(!q.items.length){
    html += '<div class=empty>Queue is empty.<br>Add episodes, seasons, series or films from the library tabs.</div>';
  } else {
    q.items.forEach(it=>{
      html += `<div class="b-row q" data-iid="${it.id}">
        <button class=qbtn data-mv="-1" title="Move up">▲</button>
        <button class=qbtn data-mv="1" title="Move down">▼</button>
        ${rowHTML(esc(it.label), it.n_files+' file(s) · '+hms(it.seconds), '')}
        <button class=qbtn data-rm="1" title="Remove">✕</button></div>`;
    });
    html += `<div class=shdr>UP NEXT (${q.expanded.length})</div>`;
    q.expanded.slice(0, 12).forEach(e=>{
      html += `<div class=b-row>${rowHTML(esc(e.title), esc(e.subtitle)+' · '+hms(e.duration), '')}</div>`;
    });
    if(q.expanded.length > 12) html += `<div class=empty>&hellip;and ${q.expanded.length-12} more</div>`;
  }
  box.innerHTML = html;
  $('#qplay').onclick = ()=>playQueue();
  $('#qshuffle').onclick = async ()=>{ await api('/api/queue/shuffle',{}); renderBinge(); toast('Queue shuffled'); };
  $('#qrandom').onclick = ()=>{ qRandom=!qRandom; localStorage.retro_qrandom=qRandom?'on':'off'; renderBinge(); };
  $('#qrepeat').onclick = ()=>{ qRepeat=!qRepeat; localStorage.retro_qrepeat=qRepeat?'on':'off'; renderBinge(); };
  $('#qclear').onclick = async ()=>{ await api('/api/queue/clear',{}); renderBinge(); toast('Queue cleared'); };
  $('#qsave').onclick = ()=>{
    const name = prompt('Save queue as:');
    if(name) api('/api/queue/save',{name}).then(()=>toast('Saved "'+name+'"')).catch(()=>toast('Name refused'));
  };
  $('#qload').onclick = async ()=>{
    const j = await api('/api/queue/saved');
    if(!j.saved.length){ toast('No saved queues yet'); return; }
    sheet('Load queue', 'Replaces the current queue',
      j.saved.map(s=>[s.name+' ('+s.items+')', '', async ()=>{
        await api('/api/queue/load',{name:s.name}); renderBinge(); toast('Loaded "'+s.name+'"');
      }]));
  };
  $('#qtheme').onclick = ()=>{
    const kind = bingeSeg==='films' ? 'film' : 'film';
    const tag = prompt('Theme (tag or genre, blank for any film picks):','');
    if(tag===null) return;
    api('/api/queue/theme-random',{tag: tag||null, kind, n:5})
      .then(j=>{ toast(j.added ? j.added+' pick(s) added' : 'Nothing matched'); renderBinge(); })
      .catch(()=>toast('Theme-random failed'));
  };
  box.querySelectorAll('.b-row.q').forEach(el=>{
    const iid = +el.dataset.iid;
    el.querySelectorAll('[data-mv]').forEach(b=>b.onclick = async e=>{
      e.stopPropagation();
      await api('/api/queue/move',{item_id:iid, delta:+b.dataset.mv});
      renderBinge();
    });
    el.querySelector('[data-rm]').onclick = async e=>{
      e.stopPropagation();
      await api('/api/queue/remove',{item_id:iid});
      renderBinge();
    });
  });
}

/* ================= VOD PLAYER ================= */
let cur = null;            // {key,url,title,subtitle,duration,subs,next,queueMode}
let playedIdx = [];        // queue mode: expanded indices already played
let watchTimer = null, statusTimer = null;

function keyToBody(key){
  const [kind, id] = key.split(':');
  const n = parseInt(id, 10);
  return kind==='ep' ? {episode_id: n} : {title_id: n};
}

async function playVOD(body, queueMode){
  try{
    const j = await api('/api/vod/play', body);
    startPlayer(j, !!queueMode, !!body.fresh);
  }catch(e){
    toast(e.status===503 ? 'Server busy — try again in a moment'
                         : 'Could not play that');
  }
}

async function playQueue(){
  let q;
  try{ q = await api('/api/queue'); }catch(e){ toast('Queue unavailable'); return; }
  if(!q.expanded.length){ toast('Queue is empty'); return; }
  const j = await api('/api/queue/next',
    {played: playedIdx, random: qRandom, repeat: qRepeat});
  if(j.index===null || !j.item){ playedIdx=[]; toast('Queue finished'); return; }
  playedIdx.push(j.index);
  playVOD(keyToBody(j.item.key), true);
}

function startPlayer(j, queueMode, fresh){
  cur = j; cur.queueMode = queueMode;
  $('#ptt').textContent = j.title;
  $('#pts').textContent = j.subtitle || '';
  const ccBtn = $('#pcc');
  if(j.subs==='native'){ ccBtn.style.display='block'; ccBtn.classList.add('on');
    ccBtn.title = 'Subtitles: native — use the iOS player CC menu'; }
  else if(j.subs==='bitmap'){ ccBtn.style.display='block'; ccBtn.classList.remove('on');
    ccBtn.title = 'Subtitles available (burn-in)'; }
  else { ccBtn.style.display='none'; }
  $('#pwrap').hidden = false;
  pv.src = j.url;
  pv.load();
  const resume = (!fresh && j.position && j.position>30) ? j.position : 0;
  pv.addEventListener('loadedmetadata', function once(){
    pv.removeEventListener('loadedmetadata', once);
    if(resume){ pv.currentTime = resume; toast('Resumed from '+hms(resume)); }
    pv.play().catch(()=>{});
  });
  clearInterval(watchTimer); clearInterval(statusTimer);
  watchTimer = setInterval(postWatch, 10000);
  statusTimer = setInterval(pollStatus, 5000);
  pollStatus();
}

function postWatch(){
  if(!cur || !pv.duration || pv.paused && pv.currentTime===0) return;
  api('/api/watch', {key: cur.key.split('+')[0], position: pv.currentTime,
                     duration: pv.duration || cur.duration}).catch(()=>{});
}

async function pollStatus(){
  if(!cur || !cur.status_url) return;
  try{
    const s = await api(cur.status_url);
    const el = $('#pstatus');
    if(s.done){ el.hidden = true; return; }
    el.hidden = false;
    el.textContent = 'TRANSCODING AHEAD · '+hms(s.transcoded_sec)+' OF '+hms(s.duration)+' READY';
  }catch(e){}
}

pv.addEventListener('pause', postWatch);
pv.addEventListener('ended', async ()=>{
  postWatch();
  if(!cur) return;
  if(cur.queueMode){
    playQueue();
  }else if(cur.next && cur.next.episode_id){
    toast('Up next: '+cur.next.subtitle);
    playVOD({episode_id: cur.next.episode_id});
  }else{
    closePlayer();
  }
});
pv.addEventListener('error', ()=>{
  if(!cur) return;
  if(pv.error && pv.error.code === 1) return;  // our own abort
  // A dead session (server restart / reaper) respawns from scratch; the
  // stored watch position re-seeks on load.
  const key = cur.key;
  toast('Stream hiccup — restarting');
  setTimeout(()=>{ if(cur && cur.key===key) playVOD(keyToBody(key.split('+')[0]), cur.queueMode); }, 2500);
});

$('#pcc').onclick = ()=>{
  if(!cur) return;
  if(cur.subs==='bitmap'){
    const body = keyToBody(cur.key.split('+')[0]); body.subs = true;
    api('/api/watch', {key: cur.key.split('+')[0], position: pv.currentTime,
                       duration: pv.duration || cur.duration}).catch(()=>{});
    playVOD(body, cur.queueMode);
    toast('Restarting with subtitles burned in');
  }else{
    toast('Use the iOS player CC menu for subtitles');
  }
};
function closePlayer(){
  postWatch();
  clearInterval(watchTimer); clearInterval(statusTimer);
  pv.pause(); pv.removeAttribute('src'); pv.load();
  cur = null;
  $('#pwrap').hidden = true;
  CAT = null;                    // refresh watched/resume badges next visit
  if(tab==='binge') loadBinge();
}
$('#pclose').onclick = closePlayer;

/* ---- clock & boot ---- */
function clock(){ const d=new Date(liveNow()*1000);
  $('#clock').textContent=pad(d.getHours())+':'+pad(d.getMinutes()); }

(async function init(){
  await refreshNow();
  if(curSlug){ selectChannel(curSlug); }
  clock(); setInterval(clock, 1000);
  setInterval(refreshNow, 15000);
  requestAnimationFrame(animate);
})();
</script>
</body>
</html>"""


def render_mobile(era: str = "90s", crt: bool = True, bw: bool = False) -> str:
    return (_PAGE
            .replace("__TOKENS__", TOKENS_CSS)
            .replace("__ERA__", era)
            .replace("__CRT__", "on" if crt else "off")
            .replace("__BW__", "on" if bw else "off"))


_MANIFEST = {
    "name": "RetroGuide",
    "short_name": "RetroGuide",
    "description": "Your library, programmed like TV — live channels and "
                   "binge mode, anywhere on your tailnet.",
    "start_url": "/m",
    "scope": "/",
    "display": "standalone",
    "background_color": "#070b12",
    "theme_color": "#070b12",
    "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png",
               "purpose": "any"}],
}


def render_manifest() -> bytes:
    return json.dumps(_MANIFEST, indent=2).encode()
