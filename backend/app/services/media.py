import json
import subprocess
import shutil
from pathlib import Path

class MediaError(ValueError):
    pass

def ffmpeg_encoder_available(encoder):
    """Check encoder advertisement without assuming hardware initialization works."""
    if not shutil.which('ffmpeg'):
        return False
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], text=True,
                                capture_output=True, timeout=15, check=True)
        return encoder in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False

def qsv_available():
    return ffmpeg_encoder_available('h264_qsv')

def run(args, timeout=None):
    try:
        return subprocess.run(args, text=True, capture_output=True, check=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError(f'Missing executable: {args[0]}. Install FFmpeg and add it to PATH.') from exc
    except subprocess.CalledProcessError as exc:
        raise MediaError((exc.stderr or exc.stdout or 'FFmpeg failed')[-1500:]) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f'Command timed out: {args[0]}') from exc

def probe(path: Path):
    res = run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], timeout=120)
    try:
        info = json.loads(res.stdout)
        video = next(s for s in info['streams'] if s.get('codec_type') == 'video')
        audio = next((s for s in info['streams'] if s.get('codec_type') == 'audio'), None)
        duration = float(info.get('format', {}).get('duration') or video.get('duration') or 0)
        if duration <= 0 or int(video.get('width', 0)) <= 0 or int(video.get('height', 0)) <= 0:
            raise ValueError('Invalid metadata')
        fr = video.get('avg_frame_rate', '0/1').split('/')
        fps = float(fr[0]) / float(fr[1]) if len(fr) == 2 and float(fr[1]) else 0
        return {'duration': round(duration, 3), 'width': video['width'], 'height': video['height'],
                'fps': round(fps, 3), 'video_codec': video.get('codec_name'),
                'audio_codec': audio.get('codec_name') if audio else None,
                'has_audio': bool(audio), 'size_bytes': path.stat().st_size}
    except (KeyError, ValueError, StopIteration, TypeError) as exc:
        raise MediaError('The file is corrupted or does not contain a valid video stream.') from exc

def extract_audio(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-vn',
         '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(destination)])
    if not destination.exists() or destination.stat().st_size < 44:
        raise MediaError('No usable audio stream was extracted.')
    return destination
