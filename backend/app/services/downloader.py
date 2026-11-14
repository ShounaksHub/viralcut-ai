import hashlib
import ipaddress
import os
import shutil
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
from app.config import STORAGE, get_download_fragments, get_throttled_rate

class DownloadError(ValueError):
    pass

class DownloadCancelled(DownloadError):
    pass

# yt-dlp supports thousands of sites. Limit to known public video hosting sites for a safer local deployment.
ALLOWED_HOSTS = ('youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com', 'twitch.tv')

def validate_url(url: str):
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise DownloadError('Use a public HTTPS video URL without credentials or custom ports.')
        if not any(host == d or host.endswith('.' + d) for d in ALLOWED_HOSTS):
            raise DownloadError('Unsupported video provider. Supported: YouTube, Vimeo, Dailymotion, Twitch.')
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise DownloadError('Video URL does not resolve to a public address.')
    except (ValueError, OSError, OverflowError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError('Video URL is invalid or unreachable.') from exc
    return url

def format_bytes_human(num_bytes: float | int | None) -> str:
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    num = float(num_bytes)
    if num >= 1024**3:
        return f"{num / (1024**3):.1f} GB"
    if num >= 1024**2:
        mb = num / (1024**2)
        return f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB"
    if num >= 1024:
        return f"{num / 1024:.0f} KB"
    return f"{int(num)} B"

def format_speed_human(speed: float | int | None) -> str:
    if not speed or speed <= 0:
        return ""
    s = float(speed)
    if s >= 1024**2:
        return f"{s / (1024**2):.1f} MB/s"
    if s >= 1024:
        return f"{s / 1024:.1f} KB/s"
    return f"{s:.0f} B/s"

def format_eta_human(eta: float | int | None) -> str:
    if eta is None or eta < 0:
        return ""
    eta_sec = int(eta)
    if eta_sec < 60:
        return f"ETA {eta_sec}s"
    m, s = divmod(eta_sec, 60)
    if m < 60:
        return f"ETA {m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"ETA {h}h {m:02d}m"

class DownloadProgressTracker:
    def __init__(self):
        self.start_time = None
        self.last_time = None
        self.last_bytes = 0
        self.smoothed_speed = None

    def update(self, d):
        now = time.perf_counter()
        if self.start_time is None:
            self.start_time = now
            self.last_time = now

        status = d.get('status')
        downloaded = d.get('downloaded_bytes') or 0
        total = d.get('total_bytes') or d.get('total_bytes_estimate')

        if status == 'finished':
            final_bytes = total or downloaded
            return 100.0, f"Downloading — {format_bytes_human(final_bytes)} / {format_bytes_human(final_bytes)} — 100% — Complete"

        speed = d.get('speed')
        if (speed is None or speed <= 0) and self.last_time and (now - self.last_time) >= 0.2:
            dt = now - self.last_time
            db_bytes = downloaded - self.last_bytes
            if db_bytes >= 0 and dt > 0:
                instant_speed = db_bytes / dt
                if self.smoothed_speed is None:
                    self.smoothed_speed = instant_speed
                else:
                    self.smoothed_speed = 0.7 * self.smoothed_speed + 0.3 * instant_speed
            self.last_time = now
            self.last_bytes = downloaded

        active_speed = speed if (speed and speed > 0) else self.smoothed_speed

        eta = d.get('eta')
        if (eta is None or eta < 0) and total and downloaded and active_speed and active_speed > 0:
            remaining = max(0, total - downloaded)
            eta = remaining / active_speed

        if total and total > 0:
            pct = max(0.0, min(100.0, 100.0 * downloaded / total))
        elif d.get('fragment_count') and d.get('fragment_index') is not None:
            pct = max(0.0, min(100.0, 100.0 * d.get('fragment_index') / d.get('fragment_count')))
        else:
            pct = 0.0

        downloaded_str = format_bytes_human(downloaded)
        total_str = format_bytes_human(total) if total else "unknown size"
        pct_str = f"{pct:.0f}%"

        parts = ["Downloading", f"{downloaded_str} / {total_str}", pct_str]
        speed_str = format_speed_human(active_speed)
        if speed_str:
            parts.append(speed_str)
        eta_str = format_eta_human(eta)
        if eta_str:
            parts.append(eta_str)

        return round(pct, 1), " — ".join(parts)

def url_cache_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode('utf-8')).hexdigest()

def get_cached_download(url: str) -> Path | None:
    cache_dir = STORAGE / 'cache'
    key = url_cache_key(url)
    candidates = list(cache_dir.glob(f"{key}.*"))
    for cand in candidates:
        if cand.is_file() and not cand.name.endswith('.tmp') and cand.stat().st_size > 0:
            return cand
    return None

def save_to_download_cache(url: str, file_path: Path) -> Path | None:
    if not file_path or not file_path.is_file() or file_path.stat().st_size == 0:
        return None
    cache_dir = STORAGE / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = url_cache_key(url)
    target = cache_dir / f"{key}{file_path.suffix.lower()}"
    try:
        if target.exists() and target.samefile(file_path):
            return target
    except (OSError, ValueError):
        pass
    tmp_target = target.with_suffix('.tmp')
    try:
        shutil.copy2(file_path, tmp_target)
        tmp_target.replace(target)
        return target
    except Exception:
        tmp_target.unlink(missing_ok=True)
        return None

def download(url: str, destination: Path, progress=None, fragments=None):
    validate_url(url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError('yt-dlp is missing. Install backend requirements.') from exc
    destination.mkdir(parents=True, exist_ok=True)

    def report_progress(pct, stage_text):
        if not progress:
            return True
        try:
            res = progress(pct, stage_text)
        except TypeError:
            res = progress(pct)
        if res is False:
            raise DownloadCancelled('Video download cancelled by user.')
        return True

    tracker = DownloadProgressTracker()

    def hook(d):
        status = d.get('status')
        if status in ('downloading', 'finished'):
            pct, stage_str = tracker.update(d)
            report_progress(pct, stage_str)

    def postprocessor_hook(d):
        if d.get('status') == 'started':
            report_progress(100.0, 'Finalizing and merging video...')

    # Initial cancellation / status report
    report_progress(0.0, 'Connecting to video source...')

    concurrent_fragments = fragments if fragments is not None else get_download_fragments()
    throttled_rate = get_throttled_rate()

    # Format selector: prefer <=1080p source with good audio, avoid huge 4K/8K formats
    format_selector = (
        'bestvideo[height<=1080]+bestaudio/'
        'bestvideo[width<=1080]+bestaudio/'
        'best[height<=1080]/'
        'best[width<=1080]/'
        'best'
    )

    options = {
        'format': format_selector,
        'outtmpl': str(destination / 'download.%(ext)s'),
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'max_filesize': 8 * 1024**3,
        'progress_hooks': [hook],
        'postprocessor_hooks': [postprocessor_hook],
        'ignoreerrors': False,
        'cachedir': False,
        'concurrent_fragment_downloads': concurrent_fragments,
        'ratelimit': None,
        'throttledratelimit': throttled_rate,
        'retries': 10,
        'fragment_retries': 10,
        'file_access_retries': 3,
        'extractor_retries': 3,
        'skip_unavailable_fragments': False,
        'continue_dl': True,
        'nopart': False,
        'socket_timeout': 20,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'writethumbnail': False,
        'writeinfojson': False,
        'writedescription': False,
        'writecomments': False,
        'getcomments': False,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        candidates = [
            p for p in destination.glob('download.*')
            if p.is_file() and not p.name.endswith('.part') and not p.name.endswith('.ytdl')
        ]
        if not candidates:
            raise DownloadError('The download produced no video file.')

        mp4_target = destination / 'download.mp4'
        if mp4_target.is_file():
            return mp4_target
        return max(candidates, key=lambda x: x.stat().st_size)
    except Exception as exc:
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError(f'Video download failed: {str(exc)[:350]}') from exc
