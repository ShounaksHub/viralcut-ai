import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from app.services.downloader import (
    validate_url, DownloadError, DownloadCancelled,
    format_bytes_human, format_speed_human, format_eta_human,
    DownloadProgressTracker, get_cached_download, save_to_download_cache,
    download
)
from app.config import get_download_fragments, get_throttled_rate

def test_format_bytes_human():
    assert format_bytes_human(324 * 1024 * 1024) == '324 MB'
    assert format_bytes_human(int(1.2 * 1024 * 1024 * 1024)) == '1.2 GB'
    assert format_bytes_human(500) == '500 B'
    assert format_bytes_human(15 * 1024) == '15 KB'
    assert format_bytes_human(None) == '0 B'
    assert format_bytes_human(-10) == '0 B'

def test_format_speed_human():
    assert format_speed_human(18.4 * 1024 * 1024) == '18.4 MB/s'
    assert format_speed_human(500 * 1024) == '500.0 KB/s'
    assert format_speed_human(0) == ''
    assert format_speed_human(None) == ''

def test_format_eta_human():
    assert format_eta_human(48) == 'ETA 48s'
    assert format_eta_human(90) == 'ETA 1m 30s'
    assert format_eta_human(3665) == 'ETA 1h 01m'
    assert format_eta_human(None) == ''
    assert format_eta_human(-5) == ''

def test_progress_tracker_exact_string():
    tracker = DownloadProgressTracker()
    d = {
        'status': 'downloading',
        'downloaded_bytes': 324 * 1024 * 1024,
        'total_bytes': 1200 * 1024 * 1024,
        'speed': 18.4 * 1024 * 1024,
        'eta': 48,
    }
    pct, stage = tracker.update(d)
    assert pct == 27.0
    assert stage == 'Downloading — 324 MB / 1.2 GB — 27% — 18.4 MB/s — ETA 48s'

def test_progress_tracker_finished():
    tracker = DownloadProgressTracker()
    d = {
        'status': 'finished',
        'downloaded_bytes': int(1.2 * 1024 * 1024 * 1024),
        'total_bytes': int(1.2 * 1024 * 1024 * 1024),
    }
    pct, stage = tracker.update(d)
    assert pct == 100.0
    assert stage == 'Downloading — 1.2 GB / 1.2 GB — 100% — Complete'

def test_fragment_concurrency_config(monkeypatch):
    monkeypatch.delenv('VIRALCUT_DOWNLOAD_FRAGMENTS', raising=False)
    assert get_download_fragments() == 8

    monkeypatch.setenv('VIRALCUT_DOWNLOAD_FRAGMENTS', '4')
    assert get_download_fragments() == 4

    monkeypatch.setenv('VIRALCUT_DOWNLOAD_FRAGMENTS', '12')
    assert get_download_fragments() == 12

    # Excessive concurrency clamped/rejected back to default
    monkeypatch.setenv('VIRALCUT_DOWNLOAD_FRAGMENTS', '64')
    assert get_download_fragments() == 8

    # Non-integer handled safely
    monkeypatch.setenv('VIRALCUT_DOWNLOAD_FRAGMENTS', 'invalid')
    assert get_download_fragments() == 8

def test_throttled_rate_config(monkeypatch):
    monkeypatch.delenv('VIRALCUT_THROTTLED_RATE', raising=False)
    assert get_throttled_rate() == 102400

    monkeypatch.setenv('VIRALCUT_THROTTLED_RATE', '204800')
    assert get_throttled_rate() == 204800

def test_download_cache(tmp_path):
    url = 'https://www.youtube.com/watch?v=cache_test_123'
    dummy_video = tmp_path / 'sample.mp4'
    dummy_video.write_bytes(b'video-data-content-here')

    cached = save_to_download_cache(url, dummy_video)
    assert cached is not None
    assert cached.is_file()

    retrieved = get_cached_download(url)
    assert retrieved is not None
    assert retrieved.is_file()
    assert retrieved.read_bytes() == b'video-data-content-here'

def test_download_options_and_no_rate_limit(monkeypatch, tmp_path):
    captured_opts = {}
    class MockYoutubeDL:
        def __init__(self, opts):
            nonlocal captured_opts
            captured_opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def download(self, urls):
            out = tmp_path / 'download.mp4'
            out.write_bytes(b'valid-mp4-test-bytes')

    import yt_dlp
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', MockYoutubeDL)

    download('https://www.youtube.com/watch?v=test_opts', tmp_path)

    # Verification of optimizations
    assert captured_opts.get('concurrent_fragment_downloads') == 8
    assert captured_opts.get('ratelimit') is None
    assert captured_opts.get('throttledratelimit') == 102400
    assert captured_opts.get('retries') == 10
    assert captured_opts.get('fragment_retries') == 10
    assert captured_opts.get('continue_dl') is True
    assert captured_opts.get('nopart') is False
    assert captured_opts.get('writesubtitles') is False
    assert captured_opts.get('writethumbnail') is False
    assert captured_opts.get('writeinfojson') is False
    assert '<=1080' in captured_opts.get('format')

def test_download_cancellation(monkeypatch, tmp_path):
    class MockYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def download(self, urls):
            for hook in self.opts.get('progress_hooks', []):
                hook({'status': 'downloading', 'downloaded_bytes': 100, 'total_bytes': 1000})

    import yt_dlp
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', MockYoutubeDL)

    # Returning False from progress must trigger DownloadCancelled
    with pytest.raises(DownloadCancelled):
        download('https://www.youtube.com/watch?v=cancel_test', tmp_path, progress=lambda pct, stage: False)
