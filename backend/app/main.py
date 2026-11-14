import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from app import database as db
from app.config import STORAGE, MAX_UPLOAD_GB, ALLOWED_EXTENSIONS, get_gemini_model, WHISPER_PRESETS
from app.services.downloader import validate_url, DownloadError
from app.services.media import probe, MediaError, qsv_available
from app.workers.processing import process_job, load_manifest, save_manifest
from app.services.render import render, THEMES
import io, zipfile
import shutil

executor = None

def startup():
    global executor
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='viralcut-worker')
    with db.connection():
        pass
    recovered = db.recover_active_jobs()
    if recovered:
        import logging
        logging.getLogger(__name__).warning('Marked %d stale job(s) interrupted after startup: %s', len(recovered), recovered)

def shutdown():
    if executor:
        executor.shutdown(wait=False, cancel_futures=True)

app = FastAPI(title='ViralCut AI', version='0.1.0')
app.on_event('startup')(startup)
app.on_event('shutdown')(shutdown)

from app.services.gemini import get_key, save_key, clear_key

class GeminiSettings(BaseModel):
    api_key: str

@app.get('/api/settings/gemini')
def gemini_settings():
    # No key material is ever returned to browser.
    return {'configured': bool(get_key()), 'model': get_gemini_model()}

@app.post('/api/settings/gemini')
def set_gemini_settings(body: GeminiSettings, request: Request):
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403,'API key configuration is allowed only from this computer.')
    origin=request.headers.get('origin','')
    if origin and origin not in ('http://127.0.0.1:8000','http://localhost:8000'):
        raise HTTPException(403,'Open the local website to configure your API key.')
    try:save_key(body.api_key)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    return {'configured':True}

@app.delete('/api/settings/gemini')
def delete_gemini_settings(request: Request):
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403,'Local access only.')
    origin=request.headers.get('origin','')
    if origin and origin not in ('http://127.0.0.1:8000','http://localhost:8000'):
        raise HTTPException(403,'Local website only.')
    clear_key()
    return {'configured':bool(get_key())}

@app.get('/api/health')
def health():
    from app.config import runtime_config, get_gemini_model
    ffmpeg = bool(shutil.which('ffmpeg'))
    ffprobe = bool(shutil.which('ffprobe'))
    return {'status': 'ok', 'ready': ffmpeg and ffprobe, 'phase': 'complete-pipeline',
            'python': __import__('sys').version.split()[0], 'ffmpeg': ffmpeg, 'ffprobe': ffprobe,
            'whisper': runtime_config(detect_gpu=False), 'whisper_presets': WHISPER_PRESETS,
            'intel_qsv_encoder_advertised': qsv_available(),
            'gemini_configured': bool(get_key()), 'gemini_model': get_gemini_model(),
            'database': db.DB_PATH.exists(), 'storage_writable': STORAGE.exists()}

@app.post('/api/upload', status_code=202)
async def upload_video(file: UploadFile = File(...), processing_mode: str = Form('BALANCED')):
    processing_mode = processing_mode.upper()
    if processing_mode not in WHISPER_PRESETS:
        raise HTTPException(400, 'Invalid processing mode. Choose FAST, BALANCED, or HIGH_ACCURACY.')
    ext = Path(file.filename or '').suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, 'Unsupported extension. Upload MP4, MOV, MKV, AVI, WEBM or MPEG.')
    job_id = uuid.uuid4().hex
    destination = STORAGE / 'uploads' / f'{job_id}{ext}'
    total = 0
    try:
        with destination.open('xb') as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_GB * 1024**3:
                    raise HTTPException(413, f'File exceeds {MAX_UPLOAD_GB} GB upload limit.')
                output.write(chunk)
        if total == 0:
            raise HTTPException(400, 'The uploaded file is empty.')
        # Validate actual media before putting anything in the queue.
        try:
            probe(destination)
        except MediaError as exc:
            raise HTTPException(400, f'Invalid/corrupt video: {exc}') from exc
        db.insert(job_id, 'upload', filename=destination.name, processing_mode=processing_mode)
        asyncio.get_running_loop().run_in_executor(executor, process_job, job_id)
        return {'job_id': job_id, 'status': 'queued'}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

class UrlSubmission(BaseModel):
    url: str
    processing_mode: str = 'BALANCED'

@app.post('/api/url', status_code=202)
def submit_url(body: UrlSubmission):
    processing_mode = body.processing_mode.upper()
    if processing_mode not in WHISPER_PRESETS:
        raise HTTPException(400, 'Invalid processing mode. Choose FAST, BALANCED, or HIGH_ACCURACY.')
    try:
        validate_url(body.url)
    except DownloadError as exc:
        raise HTTPException(400, str(exc)) from exc
    canonical = body.url.strip()
    for job in db.list_jobs():
        if job['source_url'] == canonical and job['status'] in (*db.ACTIVE_STATES, 'completed'):
            return {'job_id': job['id'], 'status': job['status'], 'duplicate': True}
    job_id = uuid.uuid4().hex
    db.insert(job_id, 'url', source_url=canonical, processing_mode=processing_mode)
    executor.submit(process_job, job_id)
    return {'job_id': job_id, 'status': 'queued'}

@app.get('/api/jobs')
def jobs():
    return db.list_jobs()

@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    job = db.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    return job

@app.post('/api/jobs/{job_id}/cancel', status_code=202)
def cancel(job_id: str):
    job = db.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    if job['status'] not in db.ACTIVE_STATES:
        raise HTTPException(409, 'Only an active job can be cancelled.')
    db.request_cancel(job_id)
    return {'job_id': job_id, 'status': 'cancelling'}

@app.get('/api/jobs/{job_id}/transcript')
def transcript(job_id: str):
    job = db.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    if not job['transcript_path']:
        raise HTTPException(409, 'Transcription is not ready')
    path = STORAGE / 'transcripts' / f'{job_id}.json'
    if not path.is_file():
        raise HTTPException(404, 'Transcript file missing')
    return json.loads(path.read_text(encoding='utf-8'))

@app.get('/api/jobs/{job_id}/clips')
def clips(job_id: str):
    if not db.get(job_id):raise HTTPException(404,'Job not found')
    return load_manifest(job_id)

@app.get('/api/jobs/{job_id}/clips/{clip_id}/video')
def clip_video(job_id: str,clip_id: str):
    if not re.fullmatch(r'\d{2}',clip_id) or not db.get(job_id):raise HTTPException(404,'Clip not found')
    clip=next((c for c in load_manifest(job_id)['clips'] if c['id']==clip_id),None)
    if clip is None:raise HTTPException(404,'Clip not found')
    path=STORAGE/'clips'/clip['filename']
    if not path.is_file():raise HTTPException(404,'Rendered MP4 missing')
    return FileResponse(path,media_type='video/mp4',filename=path.name,content_disposition_type='inline')

@app.get('/api/jobs/{job_id}/clips/{clip_id}/download')
def clip_download(job_id:str,clip_id:str):
    if not re.fullmatch(r'\d{2}',clip_id) or not db.get(job_id):raise HTTPException(404,'Clip not found')
    clip=next((c for c in load_manifest(job_id)['clips'] if c['id']==clip_id),None)
    if not clip:raise HTTPException(404,'Clip not found')
    path=STORAGE/'clips'/clip['filename']
    if not path.is_file():raise HTTPException(404,'Clip missing')
    return FileResponse(path,media_type='video/mp4',filename=path.name)

@app.get('/api/jobs/{job_id}/download-all')
def download_all(job_id: str):
    if not db.get(job_id):raise HTTPException(404,'Job not found')
    items=load_manifest(job_id)['clips']
    if not items:raise HTTPException(409,'No finished clips')
    # ZIP written on disk instead of memory for large projects.
    path=STORAGE/'clips'/f'{job_id}.zip'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_STORED) as z:
        for item in items:
            clip=STORAGE/'clips'/item['filename']
            if clip.is_file():z.write(clip,arcname=item['filename'])
        z.writestr('clip_details.json',json.dumps(items,ensure_ascii=False,indent=2))
    return FileResponse(path,media_type='application/zip',filename='viralcut-clips.zip')

class ClipEdit(BaseModel):
    start: float | None=None
    end: float | None=None
    theme: str | None=None
    crop_x: float | None=None

@app.post('/api/jobs/{job_id}/clips/{clip_id}/rerender',status_code=202)
def rerender(job_id:str,clip_id:str,body:ClipEdit):
    job=db.get(job_id)
    if not job:raise HTTPException(404,'Job not found')
    if job['status']!='completed':raise HTTPException(409,'Job must be completed to edit')
    manifest=load_manifest(job_id)
    clip=next((c for c in manifest['clips'] if c['id']==clip_id),None)
    if not clip:raise HTTPException(404,'Clip not found')
    start=clip['start'] if body.start is None else body.start
    end=clip['end'] if body.end is None else body.end
    theme=clip['theme'] if body.theme is None else body.theme
    crop_x=clip.get('crop_x') if body.crop_x is None else body.crop_x
    if not 20<=end-start<=60 or start<0 or end>job['metadata']['duration'] or theme not in THEMES or (crop_x is not None and not 0<=crop_x<=1):
        raise HTTPException(400,'Invalid clip boundaries, caption theme, or crop position')
    db.update(job_id,status='rendering',stage=f'Re-rendering clip {clip_id}',progress=None,worker_token=uuid.uuid4().hex)
    def worker():
        try:
            transcript=json.loads((STORAGE/'transcripts'/f'{job_id}.json').read_text(encoding='utf-8'))
            source=STORAGE/'uploads'/job['filename']
            output=STORAGE/'clips'/clip['filename']
            temp=output.with_name(output.stem+'_new.mp4')
            render(source,transcript['words'],start,end,temp,theme=theme,crop_x=crop_x,
                   progress_callback=lambda pct: db.heartbeat(job_id, progress=pct),
                   cancel_callback=lambda: bool(db.get(job_id).get('cancel_requested')))
            temp.replace(output)
            clip.update(start=start,end=end,duration=round(end-start,3),theme=theme,crop_x=crop_x)
            save_manifest(job_id,manifest)
            (STORAGE/'clips'/f'{job_id}.zip').unlink(missing_ok=True)
            db.update(job_id,status='completed',stage='Clips ready',progress=100,worker_token=None)
        except Exception as exc:
            current=db.get(job_id)
            if current and current.get('cancel_requested'):
                db.update(job_id,status='cancelled',stage='Cancelled by user',error=None,progress=None,worker_token=None)
            else:
                db.update(job_id,status='failed',stage='Re-render failed',error=str(exc)[:1200],progress=None,worker_token=None)
    executor.submit(worker)
    return {'status':'processing'}

@app.post('/api/jobs/{job_id}/retry', status_code=202)
def retry(job_id: str):
    job = db.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    if job['status'] not in db.RETRYABLE_STATES:
        raise HTTPException(409, 'Only failed, cancelled, or interrupted jobs can be retried')
    db.update(job_id, status='queued', stage='Queued', error=None, progress=None, cancel_requested=0, worker_token=None)
    executor.submit(process_job, job_id)
    return {'job_id': job_id, 'status': 'queued'}

@app.delete('/api/jobs/{job_id}')
def delete(job_id: str):
    job = db.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    if job['status'] in db.ACTIVE_STATES:
        if job.get('worker_token'):
            raise HTTPException(409, 'Cancel the active job first, then delete it when it becomes cancelled.')
        db.update(job_id, status='interrupted', stage='Interrupted', error='No active worker is attached.')
    import shutil
    for folder in ('uploads', 'audio', 'transcripts', 'clips'):
        for item in (STORAGE / folder).glob(job_id + '*'):
            if item.is_file():
                item.unlink()
    shutil.rmtree(STORAGE / 'temp' / job_id, ignore_errors=True)
    with db.connection() as conn:
        deleted = conn.execute('DELETE FROM jobs WHERE id=? AND status NOT IN (' + ','.join('?' * len(db.ACTIVE_STATES)) + ')',
                                [job_id, *db.ACTIVE_STATES]).rowcount
    if deleted != 1:
        raise HTTPException(409, 'The job became active again; cancel it before deleting.')
    return {'deleted': True}

FRONTEND_DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
@app.get('/')
def index():
    index_file = FRONTEND_DIST / 'index.html'
    if index_file.is_file():
        return FileResponse(index_file)
    return {'message': 'Frontend not built. Run npm install && npm run build in frontend/ or run Vite.'}

from fastapi.staticfiles import StaticFiles
if (FRONTEND_DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=FRONTEND_DIST / 'assets'), name='assets')
