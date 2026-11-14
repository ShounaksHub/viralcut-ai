from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
STORAGE = Path(os.getenv('VIRALCUT_STORAGE', str(ROOT / 'storage'))).resolve()
for folder in ('uploads', 'audio', 'transcripts', 'clips', 'temp'):
    (STORAGE / folder).mkdir(parents=True, exist_ok=True)
DB_PATH = STORAGE / 'jobs.sqlite3'
MAX_UPLOAD_GB = float(os.getenv('VIRALCUT_MAX_UPLOAD_GB', '8'))
MIN_CLIPS_PER_JOB = int(os.getenv('VIRALCUT_MIN_CLIPS', '4'))
MAX_CLIPS_PER_JOB = int(os.getenv('VIRALCUT_MAX_CLIPS', '20'))
WHISPER_MODEL = os.getenv('VIRALCUT_WHISPER_MODEL', 'small')
WHISPER_DEVICE = os.getenv('VIRALCUT_WHISPER_DEVICE', 'auto').lower()
WHISPER_COMPUTE = os.getenv('VIRALCUT_WHISPER_COMPUTE', 'auto').lower()
WHISPER_BEAM_SIZE = int(os.getenv('VIRALCUT_WHISPER_BEAM_SIZE', '3'))
WHISPER_VAD = os.getenv('VIRALCUT_WHISPER_VAD', '1').lower() not in ('0', 'false', 'no')
# Central Gemini default. Override with VIRALCUT_GEMINI_MODEL when needed.
GEMINI_MODEL = 'gemini-3.5-flash-lite'
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_SETTINGS = STORAGE / 'gemini_settings.json'
ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mpeg', '.mpg'}

def get_gemini_model():
    return os.getenv('VIRALCUT_GEMINI_MODEL', GEMINI_MODEL).strip() or GEMINI_MODEL

def runtime_config(detect_gpu=True):
    """Resolve safe defaults without importing CUDA libraries at app startup."""
    device = WHISPER_DEVICE
    compute = WHISPER_COMPUTE
    if device == 'auto' and not detect_gpu:
        device = 'cpu'
    if device == 'auto' and detect_gpu:
        try:
            import ctranslate2
            device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
        except Exception:
            device = 'cpu'
    if compute == 'auto':
        compute = 'float16' if device == 'cuda' else 'int8'
    return {'whisper_model': WHISPER_MODEL, 'whisper_device': device, 'whisper_compute': compute,
            'whisper_beam_size': WHISPER_BEAM_SIZE, 'whisper_vad': WHISPER_VAD}

