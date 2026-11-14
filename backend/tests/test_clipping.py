from pathlib import Path
import json
from app.services.highlights import score, select_clips, desired_clip_count, AnalysisError
from app.services.render import ass_time, build_ass, render
from app.services.media import probe
import pytest

def sample_segments(n=16):
    return [{'start':float(i*3),'end':float(i*3+2.95),'text':f'Sentence {i} has important details and meaning.'} for i in range(n)]

def test_scoring():
    assert score({k:10 for k in ('hook','content','emotion','standalone','payoff','clarity','visual')})==100
    assert score({})==0

def test_duration_driven_clip_batch_size():
    assert desired_clip_count(30)==1
    assert desired_clip_count(120)==4
    assert desired_clip_count(600)>=6
    assert desired_clip_count(3600)==20

def test_llm_clip_boundaries_and_overlap():
    segments=sample_segments()
    transcript={'segments':segments}
    def analyzer(_):
        base={'title':'A strong moment','reason':'A complete story','ratings':{'hook':9,'content':8,'emotion':7,'standalone':9,'payoff':8,'clarity':9,'visual':5}}
        return {'clips':[dict(base,start=3,end=32),dict(base,start=6,end=34),dict(base,start=26,end=47)]}
    results=select_clips(transcript,48,count=4,analyzer=analyzer)
    assert len(results)==2
    assert all(20<=c['duration']<=60 for c in results)
    assert results[0]['score']>0

def test_empty_speech():
    with pytest.raises(AnalysisError):select_clips({'segments':[]},100,analyzer=lambda _:None)

def test_ass_timing(tmp_path):
    p=tmp_path/'captions.ass'
    build_ass([{'start':5,'end':5.5,'text':'Hello'},{'start':5.5,'end':6,'text':'world'}],4,24,p)
    s=p.read_text(encoding='utf-8-sig')
    assert 'Dialogue:' in s and 'Hello' in s and '0:00:01.00' in s
    assert ass_time(61.23)=='0:01:01.23'

def test_1080_vertical_mp4_real_ffmpeg(tmp_path):
    import subprocess
    source=tmp_path/'source.mp4';out=tmp_path/'clip.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
       '-f','lavfi','-i','testsrc2=size=240x135:rate=12:duration=21',
       '-f','lavfi','-i','sine=frequency=420:duration=21','-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(source)],check=True)
    metadata=render(source,[{'start':i,'end':i+.5,'text':'hello'} for i in range(20)],0,20,out,face_track=False)
    assert metadata['width']==1080 and metadata['height']==1920 and metadata['has_audio']
    assert out.stat().st_size>10000

def test_clips_zip_api(client,tmp_path):
    from app import database as db
    from app.config import STORAGE
    from app.workers.processing import save_manifest
    from zipfile import ZipFile
    job='abc123';db.insert(job,'upload',filename='abc123.mp4')
    p=STORAGE/'clips'/f'{job}_01.mp4';p.write_bytes(b'genuine-test-media')
    save_manifest(job,{'clips':[{'id':'01','filename':p.name,'start':1,'end':21,'theme':'professional'}]})
    r=client.get(f'/api/jobs/{job}/download-all')
    assert r.status_code==200
    import io
    with ZipFile(io.BytesIO(r.content)) as archive:
        assert p.name in archive.namelist()
    assert client.get(f'/api/jobs/{job}/clips/01/video').status_code==200
    assert client.get(f'/api/jobs/{job}/clips/02/video').status_code==404
