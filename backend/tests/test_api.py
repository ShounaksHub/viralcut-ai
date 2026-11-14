from app import database as db
from app.config import STORAGE
from app.services.downloader import validate_url, DownloadError
import pytest

def test_health(client):
    assert client.get('/api/health').json()['status']=='ok'

def test_reject_invalid_upload(client):
    r=client.post('/api/upload',files={'file':('notvideo.mp4',b'garbage','video/mp4')})
    assert r.status_code==400

def test_reject_bad_extension(client):
    r=client.post('/api/upload',files={'file':('evil.exe',b'garbage','application/octet-stream')})
    assert r.status_code==400

def test_url_rejects_private_and_unapproved():
    for u in ('http://youtube.com/watch?v=xxx','https://127.0.0.1/v','https://youtube.com:8080/a','file:///etc/passwd','https://example.com/video'):
        with pytest.raises(DownloadError):validate_url(u)

def test_url_api_validation(client):
    r=client.post('/api/url',json={'url':'https://localhost/private'})
    assert r.status_code==400

def test_not_found(client):
    assert client.get('/api/jobs/unknown').status_code==404
    assert client.get('/api/jobs/unknown/transcript').status_code==404

def test_uploaded_video_job(client,video,monkeypatch):
    # Validate real upload & persisted metadata; avoid large model download by replacing only the worker.
    import app.main as module
    def local_worker(job_id):
        from app.services.media import probe, extract_audio
        job=db.get(job_id)
        source=STORAGE/'uploads'/job['filename']
        meta=probe(source)
        wav=extract_audio(source,STORAGE/'audio'/f'{job_id}.wav')
        db.update(job_id,status='completed',stage='Test ingestion completed',metadata=meta,progress=100)
    monkeypatch.setattr(module,'process_job',local_worker)
    with video.open('rb') as f:
        r=client.post('/api/upload',files={'file':('source.mp4',f,'video/mp4')})
    assert r.status_code==202,r.text
    import time
    job_id=r.json()['job_id']
    for _ in range(40):
        job=client.get('/api/jobs/'+job_id).json()
        if job['status']=='completed':break
        time.sleep(.05)
    assert job['status']=='completed'
    assert job['metadata']['width']==320
    assert (STORAGE/'audio'/f'{job_id}.wav').exists()
    assert client.get('/api/jobs/'+job_id+'/transcript').status_code==409
    assert client.delete('/api/jobs/'+job_id).json()['deleted']

def test_website_served(client):
    r=client.get('/')
    assert r.status_code==200
    assert 'ViralCut AI' in r.text
    assert client.get('/assets/app.js').status_code==200
