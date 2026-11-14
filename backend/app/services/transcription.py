from pathlib import Path
from app.config import runtime_config

_model = None

def transcribe(audio: Path, progress_callback=None):
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError('faster-whisper is not installed. Install backend requirements.') from exc
    if _model is None:
        cfg = runtime_config()
        _model = WhisperModel(cfg['whisper_model'], device=cfg['whisper_device'], compute_type=cfg['whisper_compute'])
    cfg = runtime_config()
    segments, info = _model.transcribe(str(audio), beam_size=cfg['whisper_beam_size'], word_timestamps=True,
                                       vad_filter=cfg['whisper_vad'], condition_on_previous_text=True)
    total_duration = max(float(getattr(info, 'duration', 0) or 0), 0.001)
    last_progress = -1
    data = {'language': info.language, 'language_probability': info.language_probability,
            'segments': [], 'words': []}
    for seg in segments:
        if progress_callback:
            pct = min(99, int((seg.end / total_duration) * 100))
            if pct != last_progress:
                progress_callback(pct, seg.end, total_duration)
                last_progress = pct
        record = {'start': round(seg.start, 3), 'end': round(seg.end, 3), 'text': seg.text.strip(),
                  'avg_logprob': seg.avg_logprob, 'no_speech_prob': seg.no_speech_prob}
        data['segments'].append(record)
        for w in (seg.words or []):
            data['words'].append({'start': round(w.start, 3), 'end': round(w.end, 3),
                                  'text': w.word, 'probability': w.probability, 'speaker': None})
    return data

