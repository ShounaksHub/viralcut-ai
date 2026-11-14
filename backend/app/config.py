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
WHISPER_MODE = os.getenv('VIRALCUT_WHISPER_MODE', 'BALANCED').upper()
WHISPER_MODEL = os.getenv('VIRALCUT_WHISPER_MODEL', 'small')
WHISPER_DEVICE = os.getenv('VIRALCUT_WHISPER_DEVICE', 'cpu').lower()
WHISPER_COMPUTE = os.getenv('VIRALCUT_WHISPER_COMPUTE', 'int8').lower()
WHISPER_BEAM_SIZE = int(os.getenv('VIRALCUT_WHISPER_BEAM_SIZE', '3'))
WHISPER_BATCH_SIZE = int(os.getenv('VIRALCUT_WHISPER_BATCH_SIZE', '6'))
WHISPER_CPU_THREADS = int(os.getenv('VIRALCUT_WHISPER_CPU_THREADS', str(max(4, min(12, (os.cpu_count() or 8) - 2)))) )
WHISPER_VAD = os.getenv('VIRALCUT_WHISPER_VAD', '1').lower() not in ('0', 'false', 'no')
# Central Gemini default. Override with VIRALCUT_GEMINI_MODEL when needed.
GEMINI_MODEL = 'gemini-3.5-flash-lite'
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_SETTINGS = STORAGE / 'gemini_settings.json'
ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mpeg', '.mpg'}

def get_gemini_model():
    return os.getenv('VIRALCUT_GEMINI_MODEL', GEMINI_MODEL).strip() or GEMINI_MODEL

WHISPER_PRESETS = {
    'FAST': {'model': 'base', 'compute_type': 'int8', 'batch_size': 8, 'beam_size': 2},
    'BALANCED': {'model': 'small', 'compute_type': 'int8', 'batch_size': 6, 'beam_size': 3},
    'HIGH_ACCURACY': {'model': 'small', 'compute_type': 'int8', 'batch_size': 4, 'beam_size': 5},
}

def whisper_config(mode=None):
    selected = (mode or WHISPER_MODE).upper()
    if selected not in WHISPER_PRESETS:
        selected = 'BALANCED'
    preset = WHISPER_PRESETS[selected]
    return {
        'mode': selected,
        'whisper_model': WHISPER_MODEL if os.getenv('VIRALCUT_WHISPER_MODEL') else preset['model'],
        'whisper_device': WHISPER_DEVICE,
        'whisper_compute': WHISPER_COMPUTE if os.getenv('VIRALCUT_WHISPER_COMPUTE') else preset['compute_type'],
        'whisper_batch_size': int(os.getenv('VIRALCUT_WHISPER_BATCH_SIZE', preset['batch_size'])),
        'whisper_beam_size': int(os.getenv('VIRALCUT_WHISPER_BEAM_SIZE', preset['beam_size'])),
        'whisper_cpu_threads': WHISPER_CPU_THREADS,
        'whisper_vad': WHISPER_VAD,
    }

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
    return {**whisper_config(), 'whisper_device': device, 'whisper_compute': compute}

