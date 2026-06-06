"""The browser app served to LAN devices: player + channel switcher + guide.

Self-contained single page (no external JS/CSS, so it works on a LAN with no
internet). The Python side only injects the initial channel slug; everything
else is driven by the ``/api/now`` and ``/api/schedule/<slug>`` JSON endpoints.
"""
from __future__ import annotations

import json

_PAGE = r"""<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=theme-color content="#070b12">
<title>RetroGuide</title>
<style>
:root{
  /* 90s cable is the default era */
  --bg:#070b12; --panel:#0e1521; --panel2:#141d2c; --border:#21304a;
  --text:#e8edf5; --muted:#8794a8; --accent:#36e0c8; --now:#ff5e8a;
  --font:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;
  --scan:.16;        /* scanline strength */
  --bloom:rgba(54,224,200,.08);
}
/* 70s: warm amber, woodgrain dark, serif, heavy glass */
html[data-era="70s"]{
  --bg:#140d06; --panel:#1d140a; --panel2:#271a0d; --border:#3a2a14;
  --text:#f3e6d0; --muted:#b79a72; --accent:#e8a13a; --now:#df6a2c;
  --font:Georgia,"Times New Roman",serif; --scan:.26; --bloom:rgba(232,161,58,.10);
}
/* 80s: neon magenta/cyan on black, mono, glow */
html[data-era="80s"]{
  --bg:#06010f; --panel:#10071f; --panel2:#180b2c; --border:#34155a;
  --text:#f4ecff; --muted:#a98fce; --accent:#ff3df0; --now:#00e5ff;
  --font:"Courier New",ui-monospace,monospace; --scan:.22; --bloom:rgba(255,61,240,.12);
}
/* 00s: sleek digital-cable blue/silver, thin, sharp */
html[data-era="00s"]{
  --bg:#070b14; --panel:#0d1422; --panel2:#131c2e; --border:#243349;
  --text:#eef3fa; --muted:#8aa0bd; --accent:#4aa3ff; --now:#ff5e8a;
  --font:"Segoe UI",system-ui,Helvetica,Arial,sans-serif; --scan:.07; --bloom:rgba(74,163,255,.08);
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; background:
    radial-gradient(1200px 600px at 80% -10%, var(--bloom), transparent 60%),
    radial-gradient(900px 500px at -10% 110%, rgba(255,94,138,.06), transparent 60%),
    var(--bg);
  color:var(--text); font-family:var(--font);
  -webkit-font-smoothing:antialiased;
}
/* CRT overlay: scanlines + vignette + gentle flicker, gated by data-crt.
   Scoped to the screen only -- the surrounding UI stays crisp. */
.crtfx{position:absolute;inset:0;z-index:2;pointer-events:none;display:none}
html[data-crt="on"] .crtfx{display:block}
html[data-crt="on"] .crtfx::before{content:"";position:absolute;inset:0;
  background:repeating-linear-gradient(rgba(0,0,0,calc(var(--scan)*1.4)) 0 1px,
    transparent 1px 3px);mix-blend-mode:multiply;animation:flick 5s infinite steps(60)}
html[data-crt="on"] .crtfx::after{content:"";position:absolute;inset:0;
  background:radial-gradient(120% 120% at 50% 50%,transparent 58%,rgba(0,0,0,.55) 100%);
  box-shadow:inset 0 0 80px rgba(0,0,0,.5)}
@keyframes flick{0%,100%{opacity:.9}50%{opacity:1}}
html[data-crt="on"] .screen{border-radius:22px/16px}
html[data-crt="on"] .screen video{filter:saturate(1.12) contrast(1.04)}
/* B&W toggle desaturates the picture ONLY (a black-and-white set) */
html[data-bw="on"] .screen video{filter:grayscale(1) contrast(1.08) brightness(1.02)!important}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:linear-gradient(rgba(54,224,200,.035) 1px,transparent 1px),
    linear-gradient(90deg,rgba(54,224,200,.035) 1px,transparent 1px);
  background-size:42px 42px; mask-image:radial-gradient(circle at 50% 40%,#000,transparent 85%)}
.app{position:relative;z-index:1;display:flex;flex-direction:column;height:100dvh}

/* ---- HUD top bar ---- */
header{display:flex;align-items:center;gap:14px;padding:12px 18px;
  border-bottom:1px solid var(--border);
  box-shadow:0 1px 0 rgba(54,224,200,.18);backdrop-filter:blur(6px)}
.brand{font-weight:800;letter-spacing:1px;font-size:18px;white-space:nowrap}
.brand b{color:var(--accent)}
.live{display:inline-flex;align-items:center;gap:6px;font-size:10px;font-weight:800;
  letter-spacing:2px;color:var(--now);border:1px solid var(--now);
  padding:3px 8px;border-radius:999px}
.live i{width:7px;height:7px;border-radius:50%;background:var(--now);
  box-shadow:0 0 8px var(--now);animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.hdr-mid{flex:1;min-width:0;text-align:center}
.hdr-ch{font-weight:700;font-size:14px}
.hdr-show{color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.clock{font-family:var(--mono);
  font-size:15px;color:var(--accent);letter-spacing:1px;white-space:nowrap}
.skin{display:flex;align-items:center;gap:6px}
.skin select{background:var(--panel2);color:var(--text);border:1px solid var(--border);
  border-radius:7px;padding:5px 6px;font-size:11px;font-family:var(--font)}
.tg-btn{background:var(--panel2);color:var(--muted);border:1px solid var(--border);
  border-radius:7px;padding:5px 9px;font-size:11px;font-weight:800;letter-spacing:1px;
  cursor:pointer}
.tg-btn.on{color:#06121a;background:var(--accent);border-color:var(--accent);
  box-shadow:0 0 10px var(--bloom)}

/* ---- main layout ---- */
main{flex:1;min-height:0;display:grid;grid-template-columns:1fr 380px;gap:16px;
  padding:16px;overflow:hidden}
@media(max-width:920px){main{grid-template-columns:1fr;overflow:auto}
  .stage{min-height:auto}.screen{flex:0 0 auto}
  video{height:auto;aspect-ratio:16/9}}

.stage{min-width:0;min-height:0;display:flex;flex-direction:column;gap:14px}
.screen{position:relative;flex:1 1 auto;min-height:0;background:#000;
  border:1px solid var(--border);
  border-radius:14px;overflow:hidden;box-shadow:0 0 0 1px rgba(0,0,0,.4),
  0 20px 60px rgba(0,0,0,.55)}
.screen::before,.screen::after{content:"";position:absolute;width:18px;height:18px;
  border:2px solid var(--accent);opacity:.7;z-index:3;pointer-events:none}
.screen::before{top:10px;left:10px;border-right:0;border-bottom:0;border-radius:4px 0 0 0}
.screen::after{bottom:10px;right:10px;border-left:0;border-top:0;border-radius:0 0 4px 0}
video{display:block;width:100%;height:100%;object-fit:contain;background:#000}
.screen .badge{position:absolute;top:12px;right:12px;z-index:4;font-size:10px;
  font-weight:800;letter-spacing:2px;color:#fff;background:rgba(255,94,138,.92);
  padding:4px 9px;border-radius:6px;box-shadow:0 0 14px rgba(255,94,138,.6)}
.screen .bug{position:absolute;top:12px;left:12px;z-index:4;height:38px;width:auto;
  max-width:120px;opacity:.9;filter:drop-shadow(0 2px 6px rgba(0,0,0,.7))}
.clogo{width:22px;height:22px;object-fit:contain;border-radius:4px;flex:0 0 auto}

.nowbar{flex:0 0 auto;background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border);border-radius:14px;padding:16px 18px}
.nowbar .eyebrow{font-size:10px;letter-spacing:2px;font-weight:800;color:var(--accent)}
.nowbar h2{margin:6px 0 2px;font-size:21px;font-weight:800;line-height:1.15}
.nowbar .sub{color:var(--muted);font-size:13px}
.nowbar .blurb{margin-top:10px;color:#c7d0de;font-size:13px;line-height:1.5;
  max-height:4.5em;overflow:hidden}
.bar{margin-top:14px;height:6px;border-radius:6px;background:#0a1018;overflow:hidden;
  border:1px solid var(--border)}
.bar > i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),#7af7e4);
  box-shadow:0 0 10px rgba(54,224,200,.6)}
.times{display:flex;justify-content:space-between;margin-top:6px;font-size:11px;
  color:var(--muted);font-family:"JetBrains Mono",ui-monospace,monospace}

/* ---- side panel ---- */
.side{min-height:0;display:flex;flex-direction:column;gap:14px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:14px;
  display:flex;flex-direction:column;min-height:0;overflow:hidden}
.panel > h3{margin:0;padding:12px 15px;font-size:11px;letter-spacing:2px;
  font-weight:800;color:var(--muted);border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center}
.panel .scroll{overflow:auto;padding:8px}
.channels{flex:0 0 auto;max-height:46%}
.guide{flex:1 1 auto}

.chip{display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:10px;
  cursor:pointer;border:1px solid transparent;transition:.12s}
.chip:hover{background:var(--panel2)}
.chip.active{background:var(--panel2);border-color:var(--ca)}
.chip .barv{width:4px;align-self:stretch;border-radius:3px;background:var(--ca);
  box-shadow:0 0 8px var(--ca);opacity:.85}
.chip .num{font-family:"JetBrains Mono",monospace;font-size:11px;color:var(--muted);
  width:16px;text-align:center}
.chip .meta{flex:1;min-width:0}
.chip .cn{font-weight:700;font-size:13.5px;display:flex;align-items:center;gap:7px}
.chip .cs{color:var(--muted);font-size:11.5px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.chip .mini{margin-top:5px;height:3px;border-radius:3px;background:#0a1018;overflow:hidden}
.chip .mini > i{display:block;height:100%;width:0;background:var(--ca)}
.onair{font-size:9px;font-weight:800;letter-spacing:1px;color:var(--now);
  border:1px solid var(--now);padding:1px 5px;border-radius:5px}

.daypart{font-size:10px;letter-spacing:2px;font-weight:800;color:var(--accent);
  padding:14px 8px 5px}
.g-row{display:flex;gap:11px;padding:8px;border-radius:9px;border:1px solid transparent}
.g-row.now{background:rgba(255,94,138,.07);border-color:rgba(255,94,138,.4)}
.g-row .gt{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--muted);
  width:46px;flex:0 0 auto;padding-top:1px}
.g-row.now .gt{color:var(--now)}
.g-row .gm{flex:1;min-width:0}
.g-row .gtitle{font-size:13.5px;font-weight:600}
.g-row .gsub{font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.g-row.now .gtitle{color:#fff}
.empty{color:var(--muted);font-size:13px;padding:24px;text-align:center}

/* ---- guide view toggle ---- */
.gv{display:flex;gap:4px}
.vb{background:transparent;border:1px solid var(--border);color:var(--muted);
  font-size:9px;font-weight:800;letter-spacing:1px;padding:3px 8px;border-radius:6px;
  cursor:pointer}
.vb.on{color:#06121a;background:var(--accent);border-color:var(--accent)}

/* ---- timeline / EPG grid ---- */
.gridwrap{flex:1 1 auto;overflow:auto;position:relative}
.ginner{position:relative}
.gruler{position:sticky;top:0;height:22px;z-index:5;background:var(--panel);
  border-bottom:1px solid var(--border)}
.gcorner{position:sticky;left:0;display:inline-block;width:96px;height:22px;
  z-index:6;background:var(--panel)}
.gh{position:absolute;top:5px;font-size:10px;color:var(--muted);font-family:var(--mono);
  white-space:nowrap;border-left:1px solid var(--border);padding-left:4px;height:16px}
.glane{position:relative;height:52px;border-top:1px solid var(--border)}
.glabel{position:sticky;left:0;z-index:4;width:96px;height:100%;
  display:flex;align-items:center;gap:6px;padding:0 8px;font-size:11px;
  font-weight:700;background:var(--panel2);border-right:2px solid var(--ca)}
.glabel .gdot{width:4px;align-self:stretch;margin:8px 0;border-radius:2px;
  background:var(--ca);box-shadow:0 0 6px var(--ca)}
.gblock{position:absolute;top:5px;height:42px;overflow:hidden;cursor:pointer;
  background:var(--panel2);border:1px solid var(--ca);border-radius:6px;
  padding:4px 6px;box-sizing:border-box}
.gblock:hover{background:#1b2a3c}
.gblock.now{background:rgba(255,94,138,.12);border-color:var(--now)}
.gblock .gbt{font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.gblock .gbs{font-size:9.5px;color:var(--muted);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.gnow{position:absolute;top:22px;width:2px;background:var(--now);z-index:3;
  box-shadow:0 0 8px var(--now)}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:20;
  background:var(--panel2);border:1px solid var(--accent);color:var(--text);
  padding:10px 16px;border-radius:10px;font-size:13px;opacity:0;transition:.25s;
  box-shadow:0 10px 30px rgba(0,0,0,.5)}
.toast.show{opacity:1}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#223048;border-radius:8px}
::-webkit-scrollbar-track{background:transparent}
</style>
</head>
<body>
<div class=app>
  <header>
    <div class=brand>RETRO<b>GUIDE</b></div>
    <span class=live><i></i>LIVE</span>
    <div class=hdr-mid>
      <div class=hdr-ch id=hch>&mdash;</div>
      <div class=hdr-show id=hshow></div>
    </div>
    <div class=skin>
      <select id=era title="Broadcast era">
        <option value=70s>70s</option>
        <option value=80s>80s</option>
        <option value=90s>90s</option>
        <option value=00s>00s</option>
      </select>
      <button id=bw class=tg-btn title="Black &amp; white picture">B&amp;W</button>
      <button id=crt class=tg-btn title="CRT scanlines">CRT</button>
    </div>
    <div class=clock id=clock>--:--:--</div>
  </header>
  <main>
    <section class=stage>
      <div class=screen>
        <video id=v autoplay controls playsinline></video>
        <div class=crtfx></div>
        <img class=bug id=bug alt="" style=display:none>
        <span class=badge id=badge style=display:none>ON AIR</span>
      </div>
      <div class=nowbar>
        <div class=eyebrow id=eyebrow>NOW PLAYING</div>
        <h2 id=ntitle>Tuning in&hellip;</h2>
        <div class=sub id=nsub></div>
        <div class=blurb id=nblurb></div>
        <div class=bar><i id=nprog></i></div>
        <div class=times><span id=tstart></span><span id=tend></span></div>
      </div>
    </section>
    <aside class=side>
      <div class="panel channels">
        <h3>CHANNELS</h3>
        <div class=scroll id=chanlist></div>
      </div>
      <div class="panel guide">
        <h3><span class=gv><button id=vlist class="vb on">LIST</button><button id=vgrid class=vb>GRID</button></span><span id=guidech style="color:var(--accent)"></span></h3>
        <div class=scroll id=guidelist></div>
        <div class=gridwrap id=gridwrap style=display:none></div>
      </div>
    </aside>
  </main>
</div>
<div class=toast id=toast></div>
<script>
const INITIAL = __INITIAL__;
const $ = s => document.querySelector(s);
const v = $('#v');

// iOS/macOS Safari can't play our open-ended progressive MP4, but it plays
// HLS natively. Use HLS only where the browser supports it natively (so we
// need no hls.js dependency); everyone else keeps the lower-latency MP4 path.
const USE_HLS = __HLS__ &&
  (v.canPlayType('application/vnd.apple.mpegurl') !== '' ||
   v.canPlayType('application/x-mpegURL') !== '');

/* ---- retro skin (era / CRT / B&W) ---- */
const root = document.documentElement;
function applySkin(){
  root.dataset.era = localStorage.retro_era || "__ERA__";
  root.dataset.crt = localStorage.retro_crt || "__CRT__";
  root.dataset.bw  = localStorage.retro_bw  || "__BW__";
  const era=$('#era'); if(era) era.value=root.dataset.era;
  $('#crt').classList.toggle('on', root.dataset.crt==='on');
  $('#bw').classList.toggle('on', root.dataset.bw==='on');
}
$('#era').onchange = e => { localStorage.retro_era = e.target.value; applySkin(); };
$('#crt').onclick = () => { localStorage.retro_crt = root.dataset.crt==='on'?'off':'on'; applySkin(); };
$('#bw').onclick  = () => { localStorage.retro_bw  = root.dataset.bw==='on'?'off':'on'; applySkin(); };
applySkin();
let CH = [];            // channels from /api/now
let cur = null;         // current slug
let skew = 0;           // server_now - client_now (seconds)
let guideCache = null;  // {slug, programs}

function liveNow(){ return Date.now()/1000 + skew; }
function pad(n){ return String(n).padStart(2,'0'); }
function fmt(ts){ const d=new Date(ts*1000); return pad(d.getHours())+':'+pad(d.getMinutes()); }
function clamp(x){ return Math.max(0,Math.min(1,x)); }

function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.classList.remove('show'),2200); }

let hlsProgEnd = 0;       // guards the HLS program-boundary reload
let streamLoadWall = 0;   // wall-clock when the current stream's t=0 began
let lastResync = 0;       // throttle automatic resyncs
let errDelay = 2500;      // error-retry backoff (grows; capped)
let errTimer = null;
const RESYNC_DRIFT = 12;  // seconds behind live before we re-seek
const RESYNC_MIN_GAP = 45;// don't resync more than this often
const ERR_MAX = 30000;

function loadVideo(slug){
  streamLoadWall = liveNow();
  const path = USE_HLS ? '/index.m3u8' : '/live.mp4';
  v.src = '/c/'+slug+path+'?t='+Date.now();
  v.load(); v.play().catch(()=>{});
}
v.addEventListener('ended', ()=>{ errDelay=2500; if(cur) loadVideo(cur); refreshNow(); });
v.addEventListener('playing', ()=>{ errDelay=2500; });  // healthy: reset backoff
v.addEventListener('error', ()=>{
  if(!cur) return;
  clearTimeout(errTimer);
  errTimer = setTimeout(()=>loadVideo(cur), errDelay);
  errDelay = Math.min(ERR_MAX, errDelay*2);  // back off so a stuck client can't storm the host
});

// Live-edge resync: a long program can drift behind wall-clock as small
// rebuffers accumulate (the "falls behind after a couple hours" problem).
// Reloading re-seeks to the live offset -- the same fix as switching channel,
// done automatically when drift exceeds the threshold.
function resyncCheck(){
  // HLS players manage the live edge themselves (and currentTime isn't
  // wall-relative for HLS), so this manual re-seek only applies to MP4.
  if(USE_HLS) return;
  if(!cur || v.paused || v.seeking || v.readyState<3 || !streamLoadWall) return;
  const wall = liveNow();
  if(wall - lastResync < RESYNC_MIN_GAP) return;
  const expected = wall - streamLoadWall;        // where playback should be
  const drift = expected - v.currentTime;        // positive => behind live
  if(drift > RESYNC_DRIFT){
    lastResync = wall;
    loadVideo(cur);
  }
}

function selectChannel(slug, {play=true}={}){
  if(!slug) return;
  cur = slug;
  history.replaceState(null,'', '/c/'+slug);
  const c = CH.find(x=>x.slug===slug);
  if(c){ $('#hch').textContent = c.name; toast('Tuned to '+c.name);
    const bug=$('#bug');
    if(c.logo){ bug.src='/logo/'+slug; bug.style.display='block'; }
    else { bug.style.display='none'; } }
  document.querySelectorAll('.chip').forEach(el=>
    el.classList.toggle('active', el.dataset.slug===slug));
  if(play) loadVideo(slug);
  renderNow();
  if(view==='grid') renderGrid(false); else loadGuide(slug);
}

/* ---- channel rail ---- */
function renderChannels(){
  const box = $('#chanlist');
  if(!box.children.length || box.dataset.count!=CH.length){
    box.innerHTML=''; box.dataset.count=CH.length;
    CH.forEach((c,i)=>{
      const el=document.createElement('div');
      el.className='chip'; el.dataset.slug=c.slug; el.style.setProperty('--ca',c.accent);
      const badge = c.logo
        ? `<img class=clogo src="/logo/${encodeURIComponent(c.slug)}" alt="">`
        : `<div class=barv></div>`;
      el.innerHTML=`${badge}<div class=num>${i+1}</div>
        <div class=meta><div class=cn>${esc(c.name)}<span class=onair data-air style=display:none>ON AIR</span></div>
        <div class=cs data-show></div><div class=mini><i data-mini></i></div></div>`;
      el.onclick=()=>selectChannel(c.slug);
      box.appendChild(el);
    });
  }
  // update per-chip now text
  [...box.children].forEach(el=>{
    const c=CH.find(x=>x.slug===el.dataset.slug); if(!c) return;
    const now=c.now;
    el.querySelector('[data-show]').textContent = now? now.title+(now.subtitle?' · '+now.subtitle.split(' - ')[0]:'') : 'Off air';
    el.classList.toggle('active', el.dataset.slug===cur);
  });
}

/* ---- now playing ---- */
function renderNow(){
  const c=CH.find(x=>x.slug===cur);
  const now=c&&c.now;
  $('#hch').textContent = c? c.name : '—';
  if(!now){
    $('#eyebrow').textContent='OFF AIR'; $('#ntitle').textContent='Nothing scheduled';
    $('#nsub').textContent=''; $('#nblurb').textContent='';
    $('#hshow').textContent=''; $('#badge').style.display='none';
    $('#tstart').textContent=''; $('#tend').textContent=''; return;
  }
  $('#eyebrow').textContent = (now.daypart||'NOW PLAYING').toUpperCase();
  $('#ntitle').textContent = now.title;
  $('#nsub').textContent = now.subtitle||'';
  $('#nblurb').textContent = now.blurb||'';
  $('#hshow').textContent = now.title + (now.subtitle?' — '+now.subtitle:'');
  $('#badge').style.display='block';
  $('#tstart').textContent = fmt(now.start);
  $('#tend').textContent = fmt(now.end);
}

/* ---- guide for current channel ---- */
async function loadGuide(slug){
  $('#guidech').textContent = (CH.find(x=>x.slug===slug)||{}).name||'';
  try{
    const r=await fetch('/api/schedule/'+slug);
    const j=await r.json();
    guideCache = j; renderGuide();
  }catch(e){}
}
function renderGuide(){
  const box=$('#guidelist'); if(!guideCache){box.innerHTML='';return;}
  const now=liveNow(); let html=''; let lastDay=null;
  guideCache.programs.forEach(p=>{
    if(!p) return;
    if(p.daypart && p.daypart!==lastDay){ html+=`<div class=daypart>${esc(p.daypart.toUpperCase())}</div>`; lastDay=p.daypart; }
    const on = p.start<=now && now<p.end;
    html+=`<div class="g-row${on?' now':''}">
      <div class=gt>${fmt(p.start)}</div>
      <div class=gm><div class=gtitle>${esc(p.title)}${on?' <span class=onair>ON AIR</span>':''}</div>
      <div class=gsub>${esc(p.subtitle||'')}</div></div></div>`;
  });
  box.innerHTML = html || '<div class=empty>No upcoming programs.</div>';
}

/* ---- timeline / EPG grid view ---- */
let gridCache = null;
let view = localStorage.retro_view || 'list';
const GPX = 4;          // px per minute
const GLABEL = 96;      // label column width (matches CSS)

function applyView(){
  const grid = view==='grid';
  $('#vlist').classList.toggle('on', !grid);
  $('#vgrid').classList.toggle('on', grid);
  $('#guidelist').style.display = grid ? 'none' : '';
  $('#gridwrap').style.display = grid ? 'block' : 'none';
  if(grid){ loadGrid(true); } else { loadGuide(cur); }
}
$('#vlist').onclick = () => { view='list'; localStorage.retro_view='list'; applyView(); };
$('#vgrid').onclick = () => { view='grid'; localStorage.retro_view='grid'; applyView(); };

async function loadGrid(scroll){
  try{
    const r = await fetch('/api/grid');
    gridCache = await r.json();
    renderGrid(scroll);
  }catch(e){}
}
function tickGrid(){
  if(view!=='grid' || !gridCache) return;
  const now=liveNow();
  const nx=GLABEL+(now-gridCache.origin)/60*GPX;
  const ln=$('#gridwrap .gnow'); if(ln) ln.style.left=nx+'px';
}
function renderGrid(scroll){
  if(!gridCache){ return; }
  const wrap=$('#gridwrap'); const {origin,end,channels}=gridCache;
  const totalMin=(end-origin)/60; const W=GLABEL+totalMin*GPX;
  const x=ts=>GLABEL+(ts-origin)/60*GPX;
  let ruler='<div class=gruler style="width:'+W+'px"><span class=gcorner></span>';
  for(let t=Math.ceil(origin/3600)*3600; t<end; t+=3600){
    const d=new Date(t*1000);
    ruler+='<span class=gh style="left:'+x(t)+'px">'+pad(d.getHours())+':00</span>';
  }
  ruler+='</div>';
  const now=liveNow();
  let lanes='';
  channels.forEach(c=>{
    let blocks='';
    (c.programs||[]).forEach(p=>{
      if(!p) return;
      const on=p.start<=now&&now<p.end;
      const left=x(p.start), w=Math.max(18,(p.end-p.start)/60*GPX);
      const sub=(p.subtitle||'').split(' - ')[0];
      blocks+='<div class="gblock'+(on?' now':'')+'" data-slug="'+c.slug+'" '+
        'style="left:'+left+'px;width:'+w+'px" title="'+esc(p.title)+'">'+
        '<div class=gbt>'+esc(p.title)+'</div><div class=gbs>'+esc(sub)+'</div></div>';
    });
    const logo=c.logo?'<img class=clogo src="/logo/'+encodeURIComponent(c.slug)+'" alt="">':'<span class=gdot></span>';
    lanes+='<div class=glane style="width:'+W+'px;--ca:'+c.accent+'">'+
      '<div class=glabel style="--ca:'+c.accent+'">'+logo+esc(c.name)+'</div>'+blocks+'</div>';
  });
  const nh=22+channels.length*52;
  const nowline='<div class=gnow style="left:'+x(now)+'px;height:'+(nh-22)+'px"></div>';
  wrap.innerHTML='<div class=ginner style="width:'+W+'px">'+ruler+lanes+nowline+'</div>';
  wrap.querySelectorAll('.gblock').forEach(el=>
    el.onclick=()=>selectChannel(el.dataset.slug));
  if(scroll){ wrap.scrollLeft=Math.max(0, x(now)-GLABEL-90); }
}

/* ---- live progress animation ---- */
function animate(){
  const c=CH.find(x=>x.slug===cur); const now=liveNow();
  if(c&&c.now){
    const p=clamp((now-c.now.start)/(c.now.end-c.now.start));
    $('#nprog').style.width=(p*100).toFixed(2)+'%';
    // HLS boundary safety net: ffmpeg's ENDLIST normally fires 'ended', but if
    // that's missed, roll to the next program once wall-clock passes this one.
    if(USE_HLS && now > c.now.end + 0.3 && hlsProgEnd !== c.now.end){
      hlsProgEnd = c.now.end; loadVideo(cur); refreshNow();
    }
  }
  document.querySelectorAll('.chip').forEach(el=>{
    const c=CH.find(x=>x.slug===el.dataset.slug); if(!c) return;
    const air=el.querySelector('[data-air]'), mini=el.querySelector('[data-mini]');
    if(c.now){ air.style.display='inline-block';
      mini.style.width=(clamp((now-c.now.start)/(c.now.end-c.now.start))*100).toFixed(1)+'%'; }
    else { air.style.display='none'; mini.style.width='0'; }
  });
  requestAnimationFrame(animate);
}

/* ---- clock ---- */
function clock(){ const d=new Date((liveNow())*1000);
  $('#clock').textContent=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
  tickGrid(); resyncCheck(); }

/* ---- data refresh ---- */
async function refreshNow(){
  try{
    const r=await fetch('/api/now'); const j=await r.json();
    skew = j.server_now - Date.now()/1000;
    CH = j.channels;
    if(!cur){ cur = (INITIAL && CH.find(x=>x.slug===INITIAL))? INITIAL : (CH[0]&&CH[0].slug); }
    renderChannels(); renderNow();
    if(guideCache && guideCache.slug===cur) renderGuide();
    if(view==='grid') loadGrid(false);
  }catch(e){}
}
function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

/* ---- keyboard: arrows surf, digits tune ---- */
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT') return;
  const i=CH.findIndex(x=>x.slug===cur);
  if(e.key==='ArrowDown'){ e.preventDefault(); selectChannel(CH[(i+1)%CH.length].slug); }
  else if(e.key==='ArrowUp'){ e.preventDefault(); selectChannel(CH[(i-1+CH.length)%CH.length].slug); }
  else if(/^[1-9]$/.test(e.key) && CH[+e.key-1]){ selectChannel(CH[+e.key-1].slug); }
});

(async function init(){
  await refreshNow();
  selectChannel(cur, {play:true});
  applyView();
  clock(); setInterval(clock,1000);
  setInterval(refreshNow,15000);
  requestAnimationFrame(animate);
})();
</script>
</body>
</html>"""


def render_app(initial_slug: str | None, era: str = "90s",
               crt: bool = True, bw: bool = False, hls: bool = True) -> str:
    return (_PAGE
            .replace("__INITIAL__", json.dumps(initial_slug))
            .replace("__ERA__", era)
            .replace("__CRT__", "on" if crt else "off")
            .replace("__BW__", "on" if bw else "off")
            .replace("__HLS__", "true" if hls else "false"))
