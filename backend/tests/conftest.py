import os
import tempfile
from pathlib import Path

# Isolate DB/media created by tests before app imports.
_tmp = tempfile.TemporaryDirectory(prefix='viralcut-tests-')
os.environ['VIRALCUT_STORAGE'] = _tmp.name
import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def video(tmp_path):
    import subprocess
    path = tmp_path / 'sample.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=25:duration=2',
                    '-f','lavfi','-i','sine=frequency=440:duration=2', '-c:v','libx264','-pix_fmt','yuv420p',
                    '-c:a','aac','-shortest',str(path)],check=True)
    return path
