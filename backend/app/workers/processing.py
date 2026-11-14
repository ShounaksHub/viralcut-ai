import json
import logging
import shutil
import uuid
import hashlib
import time
from app import database as db
from app.config import STORAGE, get_gemini_model
from app.services.media import probe, extract_audio
from app.services.downloader import (
    download, DownloadCancelled, get_cached_download, save_to_download_cache
)
from app.services.transcription import transcribe
from app.services.highlights import select_clips, desired_clip_count
from app.services.render import render

logger = logging.getLogger(__name__)

class JobCancelled(RuntimeError):
    pass

def _check_cancel(job_id):
    job = db.get(job_id)
    if not job:
        raise RuntimeError('Job was deleted')
    if job.get('cancel_requested'):
        db.set_stage(job_id, 'cancelled', 'Cancelled by user', None, error=None, worker_token=None)
        raise JobCancelled('Processing cancelled by user.')
    return job

def _stage(job_id, status, label, progress=None, **extra):
    _check_cancel(job_id)
    db.set_stage(job_id, status, label, progress, **extra)

def process_job(job_id, count=None, theme='professional'):
    try:
        timings = {}
        total_started = time.perf_counter()
        job = db.get(job_id)
        if not job:
            raise RuntimeError('Job not found')
        if job.get('cancel_requested'):
            db.update(job_id, status='cancelled', stage='Cancelled by user', progress=None, error=None, worker_token=None)
            return
        db.update(job_id, worker_token=uuid.uuid4().hex, cancel_requested=0, error=None)
        workspace = STORAGE / 'temp' / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        if job['source_type'] == 'url':
            source = None
            # 1. On retry, verify if existing uploaded source file is valid
            if job.get('filename'):
                existing_candidate = STORAGE / 'uploads' / job['filename']
                if existing_candidate.is_file() and existing_candidate.stat().st_size > 0:
                    try:
                        probe(existing_candidate)
                        source = existing_candidate
                        logger.info('Reusing valid downloaded video for job %s: %s', job_id, source)
                    except Exception:
                        logger.warning('Existing file for job %s was invalid, re-downloading', job_id)
                        existing_candidate.unlink(missing_ok=True)
                        db.update(job_id, filename=None)

            # 2. Check persistent URL cache
            if not source and job.get('source_url'):
                cached = get_cached_download(job['source_url'])
                if cached and cached.is_file() and cached.stat().st_size > 0:
                    try:
                        probe(cached)
                        dest = STORAGE / 'uploads' / (job_id + cached.suffix.lower())
                        shutil.copy2(cached, dest)
                        source = dest
                        db.update(job_id, filename=source.name)
                        logger.info('Cache hit for job %s from URL cache: %s', job_id, cached)
                    except Exception:
                        cached.unlink(missing_ok=True)

            # 3. Check other completed jobs with the same URL
            if not source and job.get('source_url'):
                for other in db.list_jobs():
                    if other.get('id') != job_id and other.get('source_url') == job['source_url'] and other.get('filename'):
                        other_path = STORAGE / 'uploads' / other['filename']
                        if other_path.is_file() and other_path.stat().st_size > 0:
                            try:
                                probe(other_path)
                                dest = STORAGE / 'uploads' / (job_id + other_path.suffix.lower())
                                shutil.copy2(other_path, dest)
                                save_to_download_cache(job['source_url'], dest)
                                source = dest
                                db.update(job_id, filename=source.name)
                                logger.info('Reused valid source from job %s for job %s', other['id'], job_id)
                                break
                            except Exception:
                                pass

            # 4. If no valid cached source exists, perform optimized download
            if not source:
                _stage(job_id, 'downloading', 'Connecting to video source...', 0)
                last_db_heartbeat = 0.0

                def download_progress(pct, stage_text=None):
                    nonlocal last_db_heartbeat
                    current = db.get(job_id)
                    if not current or current.get('cancel_requested'):
                        return False
                    now = time.perf_counter()
                    # Throttle DB writes to ~3-4 times per second to prevent sqlite lock contention
                    if now - last_db_heartbeat >= 0.25 or pct == 100.0 or last_db_heartbeat == 0.0:
                        last_db_heartbeat = now
                        if stage_text:
                            db.heartbeat(job_id, stage=stage_text, progress=pct)
                        else:
                            db.heartbeat(job_id, progress=pct)
                    return True

                downloaded = download(job['source_url'], workspace, progress=download_progress)
                probe(downloaded)
                dest = STORAGE / 'uploads' / (job_id + downloaded.suffix.lower())
                shutil.copy2(downloaded, dest)
                save_to_download_cache(job['source_url'], dest)
                source = dest
                db.update(job_id, filename=source.name)
        else:
            source = STORAGE / 'uploads' / job['filename']
        if not source or not source.is_file():
            raise RuntimeError('Original video was not found')

        _stage(job_id, 'extracting_audio', 'Inspecting video')
        meta = probe(source)
        db.update(job_id, metadata=meta)
        if not meta['has_audio']:
            raise RuntimeError('This video has no audio for speech-based highlight detection.')
        if count is None:
            count = desired_clip_count(meta['duration'])
        if count <= 0:
            raise RuntimeError('The source video is shorter than the 20-second minimum clip duration.')
        wav = STORAGE / 'audio' / f'{job_id}.wav'
        _stage(job_id, 'extracting_audio', 'Extracting audio')
        audio_started = time.perf_counter()
        if not wav.exists():
            extract_audio(source, wav)
        timings['audio_extraction'] = round(time.perf_counter() - audio_started, 3)

        transcript_path = STORAGE / 'transcripts' / f'{job_id}.json'
        _stage(job_id, 'transcribing', 'Transcribing audio', 0)
        whisper_started = time.perf_counter()
        if not transcript_path.exists():
            def whisper_progress(pct, done, total):
                if db.get(job_id).get('cancel_requested'):
                    raise JobCancelled('Processing cancelled by user.')
                done_m, done_s = divmod(int(done), 60)
                total_m, total_s = divmod(int(total), 60)
                db.heartbeat(job_id, stage=f'Transcribing: {done_m}:{done_s:02d} / {total_m}:{total_s:02d}', progress=pct)
            result = transcribe(wav, progress_callback=whisper_progress,
                                mode=job.get('processing_mode', 'BALANCED'))
            transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            result = json.loads(transcript_path.read_text(encoding='utf-8'))
        timings['whisper'] = round(time.perf_counter() - whisper_started, 3)
        db.update(job_id, transcript_path=str(transcript_path))

        analysis_path = STORAGE / 'transcripts' / f'{job_id}.analysis.json'
        analysis_fingerprint = hashlib.sha256(
            (json.dumps(result, ensure_ascii=False, sort_keys=True) +
             f'|gemini-model={get_gemini_model()}|count={count}|analysis-v=2').encode('utf-8')
        ).hexdigest()
        _stage(job_id, 'analyzing', 'Analyzing transcript with Gemini')
        gemini_started = time.perf_counter()
        cached_analysis = None
        if analysis_path.exists():
            try:
                candidate_cache = json.loads(analysis_path.read_text(encoding='utf-8'))
                if (isinstance(candidate_cache, dict) and
                        candidate_cache.get('fingerprint') == analysis_fingerprint and
                        candidate_cache.get('model') == get_gemini_model() and
                        isinstance(candidate_cache.get('clips'), list)):
                    cached_analysis = candidate_cache['clips']
            except (OSError, ValueError, TypeError):
                cached_analysis = None
        if cached_analysis is not None:
            selections = cached_analysis
        else:
            selections = select_clips(result, meta['duration'], count=count)
            # Write only successful analysis responses; failed requests never poison the cache.
            analysis_path.write_text(json.dumps({
                'version': 2, 'model': get_gemini_model(),
                'fingerprint': analysis_fingerprint, 'clips': selections,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
        timings['gemini_analysis'] = round(time.perf_counter() - gemini_started, 3)
        _stage(job_id, 'selecting_clips', 'Selecting independent highlights')

        manifest = {'clips': [], 'requested_count': count, 'source': source.name, 'theme': theme, 'version': 1}
        save_manifest(job_id, manifest)
        for idx, selection in enumerate(selections):
            _stage(job_id, 'rendering', f'Rendering clip {idx + 1}/{len(selections)}')
            clip_id = f'{idx + 1:02d}'
            output = STORAGE / 'clips' / f'{job_id}_{clip_id}.mp4'
            output.unlink(missing_ok=True)
            render_started = time.perf_counter()
            render(source, result['words'], selection['start'], selection['end'], output, theme=theme,
                   progress_callback=lambda pct: db.heartbeat(job_id, progress=pct),
                   cancel_callback=lambda: bool(db.get(job_id).get('cancel_requested')))
            _check_cancel(job_id)
            timings[f'render_clip_{idx + 1}'] = round(time.perf_counter() - render_started, 3)
            selection.update(id=clip_id, filename=output.name, theme=theme, crop_x=None)
            manifest['clips'].append(selection)
            save_manifest(job_id, manifest)
        db.update(job_id, status='completed', stage=f'{len(selections)} clips ready', progress=100,
                  worker_token=None, cancel_requested=0, error=None)
        timings['total'] = round(time.perf_counter() - total_started, 3)
        logger.info('Job %s performance timings: %s', job_id, timings)
    except (JobCancelled, DownloadCancelled):
        logger.info('Job %s cancelled', job_id)
        db.update(job_id, status='cancelled', stage='Cancelled by user', progress=None, error=None, worker_token=None)
    except Exception as exc:
        logger.exception('Job %s failed', job_id)
        current = db.get(job_id)
        if current:
            if current.get('cancel_requested') or 'cancelled by user' in str(exc).lower():
                db.update(job_id, status='cancelled', stage='Cancelled by user', progress=None, error=None, worker_token=None)
            else:
                db.update(job_id, status='failed', stage='Failed', progress=None, error=str(exc)[:1200], worker_token=None)

def manifest_path(job_id):
    return STORAGE / 'clips' / f'{job_id}.json'

def save_manifest(job_id, manifest):
    path = manifest_path(job_id)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def load_manifest(job_id):
    path = manifest_path(job_id)
    if not path.exists():
        return {'clips': []}
    return json.loads(path.read_text(encoding='utf-8'))
