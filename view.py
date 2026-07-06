"""Media Manager console view.

Page (public) at /plugins/media_manager/view; gated data + upload routes under
/api/plugins/media_manager. Server-rendered HTML linking the DS plugin-kit, no
build step. Note: NO `from __future__ import annotations` here — FastAPI resolves
UploadFile/Form type hints at import, and PEP 563 stringized hints break that.
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from . import ingest, jellyfin, jobs


def build_view_router() -> APIRouter:
    router = APIRouter()

    @router.get("/view")
    async def view() -> HTMLResponse:
        return HTMLResponse(_PAGE)

    return router


def build_data_router() -> APIRouter:
    router = APIRouter()

    @router.get("/server")
    async def server() -> JSONResponse:
        info = jellyfin.server_info(ingest._CFG.get("jellyfin_url", ""))
        key = bool(ingest._CFG.get("jellyfin_api_key"))
        return JSONResponse({"jellyfin": info or "unreachable", "scan_enabled": key,
                             "library": ingest._CFG.get("library_path", "")})

    @router.get("/jobs")
    async def list_jobs() -> JSONResponse:
        return JSONResponse({"jobs": jobs.recent(40)})

    @router.post("/enqueue")
    async def enqueue(url: str = Form(...), audio_only: bool = Form(False),
                      subtitles: bool = Form(True), single: bool = Form(False)) -> JSONResponse:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return JSONResponse({"error": "provide an http(s) URL"}, status_code=400)
        jid = jobs.enqueue("url", url, options={"audio_only": audio_only,
                                                "subtitles": subtitles, "single": single})
        return JSONResponse({"job_id": jid})

    @router.post("/upload")
    async def upload(file: UploadFile = File(...), title: str = Form(""),
                     audio_only: bool = Form(False)) -> JSONResponse:
        stage = Path(ingest._CFG["staging_dir"]) / "uploads"
        stage.mkdir(parents=True, exist_ok=True)
        safe = os.path.basename(file.filename or f"upload-{uuid.uuid4().hex[:8]}")
        dest = stage / f"{uuid.uuid4().hex[:8]}-{safe}"
        with open(dest, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                fh.write(chunk)
        jid = jobs.enqueue("upload", str(dest), title=title.strip(),
                           options={"audio_only": audio_only})
        return JSONResponse({"job_id": jid, "filename": safe})

    return router


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Media Manager</title>
<link rel="stylesheet" id="pl-kit-css" />
<style>
  body { margin:0; font:14px/1.5 var(--pl-font, ui-sans-serif, system-ui); }
  .wrap { padding:20px; max-width:1000px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--pl-muted,#888); margin:0 0 18px; font-size:13px; }
  .card { border:1px solid var(--pl-border,#333); border-radius:10px; padding:16px; margin-bottom:16px;
          background:var(--pl-panel, transparent); }
  label { font-size:13px; }
  input[type=text],input[type=url]{ width:100%; box-sizing:border-box; padding:9px 12px; border-radius:8px;
          border:1px solid var(--pl-border,#444); background:var(--pl-input,transparent); color:inherit; font:inherit; }
  .opts { display:flex; gap:16px; margin:12px 0; flex-wrap:wrap; font-size:13px; color:var(--pl-muted,#aaa); }
  .opts label { display:flex; gap:6px; align-items:center; cursor:pointer; }
  button { font:inherit; padding:8px 18px; border-radius:8px; cursor:pointer; border:1px solid var(--pl-border,#444);
           background:var(--pl-accent,#2563eb); color:#fff; }
  button.ghost { background:transparent; color:inherit; }
  #drop { border:2px dashed var(--pl-border,#555); border-radius:10px; padding:22px; text-align:center;
          color:var(--pl-muted,#999); cursor:pointer; transition:.15s; }
  #drop.over { border-color:var(--pl-accent,#2563eb); color:var(--pl-fg,inherit); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--pl-border,#2a2a2a); vertical-align:top; }
  th { color:var(--pl-muted,#999); font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  .bar { height:6px; border-radius:4px; background:var(--pl-border,#333); overflow:hidden; min-width:80px; }
  .bar > i { display:block; height:100%; background:var(--pl-accent,#2563eb); }
  .st { padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; border:1px solid var(--pl-border,#444); }
  .st.done{ color:#7fe0a0; } .st.error{ color:#ff8080; } .st.running{ color:#ffcf5c; } .st.queued{ color:#8ab4ff; }
  .stamp { color:var(--pl-muted,#888); font-size:12px; }
  .row { display:flex; gap:8px; align-items:center; }
</style>
</head>
<body>
<div class="wrap">
  <h1>🎬 Media Manager</h1>
  <p class="sub" id="serverline">Paste a YouTube / video URL or drop a file — it's pulled, tagged for Jellyfin, and filed into the library.</p>

  <div class="card">
    <label for="url">Video, playlist, or channel URL</label>
    <div class="row" style="margin-top:6px">
      <input type="url" id="url" placeholder="https://www.youtube.com/watch?v=… or https://…/clip.mp4" />
      <button id="go">Ingest</button>
    </div>
    <div class="opts">
      <label><input type="checkbox" id="subs" checked /> subtitles</label>
      <label><input type="checkbox" id="audio" /> audio only</label>
      <label><input type="checkbox" id="single" /> single (don't expand playlist)</label>
    </div>
  </div>

  <div class="card">
    <div id="drop">Drop a video/audio file here, or click to choose — it's uploaded and ingested.</div>
    <input type="file" id="fileinput" style="display:none" accept="video/*,audio/*" />
  </div>

  <div class="card">
    <div class="row" style="justify-content:space-between; margin-bottom:10px">
      <strong style="font-size:13px">Ingest queue</strong>
      <span class="stamp" id="stamp"></span>
    </div>
    <table><thead><tr><th>Job</th><th>Title / source</th><th>Status</th><th>Progress</th><th>Note</th></tr></thead>
      <tbody id="jobs"><tr><td colspan="5" class="stamp">loading…</td></tr></tbody>
    </table>
  </div>
</div>
<script type="module">
  const base = location.pathname.split('/plugins/')[0];
  document.getElementById('pl-kit-css').href = base + '/_ds/plugin-kit.css';
  let apiFetch = (p,i)=>fetch(p,i);
  try { const kit = await import(base + '/_ds/plugin-kit.js');
    if (kit.initPluginView) await kit.initPluginView();
    if (kit.apiFetch) apiFetch = kit.apiFetch; } catch(e){}
  const api = base + '/api/plugins/media_manager';

  async function serverInfo(){ try{
    const j = await (await apiFetch(api+'/server')).json();
    const scan = j.scan_enabled ? 'scan on' : 'no API key (scheduled scan only)';
    document.getElementById('serverline').textContent =
      `Jellyfin: ${j.jellyfin} · library ${j.library} · ${scan}`;
  }catch(e){} }

  function esc(s){ return (s||'').replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
  async function loadJobs(){
    try{
      const j = await (await apiFetch(api+'/jobs')).json();
      const rows = (j.jobs||[]).map(r=>`<tr>
        <td><code>${r.id}</code></td>
        <td>${esc(r.title||r.source).slice(0,70)}</td>
        <td><span class="st ${r.status}">${r.status}</span></td>
        <td><div class="bar"><i style="width:${Math.round(r.progress)}%"></i></div></td>
        <td class="stamp">${esc(r.message).slice(0,80)}</td></tr>`).join('');
      document.getElementById('jobs').innerHTML = rows || '<tr><td colspan="5" class="stamp">no jobs yet</td></tr>';
      document.getElementById('stamp').textContent = 'updated ' + new Date().toLocaleTimeString();
    }catch(e){ document.getElementById('stamp').textContent = 'error: '+e; }
  }

  async function ingest(){
    const url = document.getElementById('url').value.trim();
    if(!url) return;
    const fd = new FormData();
    fd.append('url', url);
    fd.append('audio_only', document.getElementById('audio').checked);
    fd.append('subtitles', document.getElementById('subs').checked);
    fd.append('single', document.getElementById('single').checked);
    const r = await apiFetch(api+'/enqueue', {method:'POST', body:fd});
    if(r.ok){ document.getElementById('url').value=''; loadJobs(); }
    else { const e = await r.json().catch(()=>({})); alert('Error: '+(e.error||r.status)); }
  }
  async function uploadFile(file){
    const fd = new FormData();
    fd.append('file', file);
    fd.append('audio_only', document.getElementById('audio').checked);
    const r = await apiFetch(api+'/upload', {method:'POST', body:fd});
    if(r.ok) loadJobs(); else alert('Upload failed: '+r.status);
  }

  document.getElementById('go').onclick = ingest;
  document.getElementById('url').addEventListener('keydown', e=>{ if(e.key==='Enter') ingest(); });
  const drop = document.getElementById('drop'), fi = document.getElementById('fileinput');
  drop.onclick = ()=>fi.click();
  fi.onchange = ()=>{ if(fi.files[0]) uploadFile(fi.files[0]); };
  drop.addEventListener('dragover', e=>{ e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', ()=>drop.classList.remove('over'));
  drop.addEventListener('drop', e=>{ e.preventDefault(); drop.classList.remove('over');
    if(e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]); });

  serverInfo(); loadJobs(); setInterval(loadJobs, 3000);
</script>
</body>
</html>
"""
