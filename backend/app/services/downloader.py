import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

class DownloadError(ValueError):
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

def download(url: str, destination: Path, progress=None):
    validate_url(url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError('yt-dlp is missing. Install backend requirements.') from exc
    destination.mkdir(parents=True, exist_ok=True)
    def hook(d):
        if progress and d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total:
                if progress(round(100 * d.get('downloaded_bytes', 0) / total, 1)) is False:
                    raise DownloadError('Video download cancelled by user.')
    options = {'format': 'bestvideo*+bestaudio/best', 'outtmpl': str(destination / 'download.%(ext)s'),
               'merge_output_format': 'mp4', 'noplaylist': True, 'quiet': True,
               'max_filesize': 8 * 1024**3, 'progress_hooks': [hook],
               'ignoreerrors': False, 'cachedir': False,
               'retries': 2, 'fragment_retries': 2, 'socket_timeout': 20}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
        candidates = [p for p in destination.glob('download.*') if p.is_file()]
        if not candidates:
            raise DownloadError('The download produced no video file.')
        return max(candidates, key=lambda x: x.stat().st_size)
    except Exception as exc:
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError(f'Video download failed: {str(exc)[:350]}') from exc
