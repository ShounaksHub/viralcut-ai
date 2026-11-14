from pathlib import Path
from app.config import whisper_config

_pipeline = None
_pipeline_key = None

def transcribe(audio: Path, progress_callback=None, mode=None):
    """Transcribe once with batched CPU inference and real word timestamps."""
    global _pipeline, _pipeline_key
    try:
        from faster_whisper import WhisperModel, BatchedInferencePipeline
    except ImportError as exc:
        raise RuntimeError('faster-whisper is not installed. Install backend requirements.') from exc
    cfg = whisper_config(mode)
    key = tuple(cfg.items())
    if _pipeline is None or _pipeline_key != key:
        model = WhisperModel(cfg['whisper_model'], device=cfg['whisper_device'],
                             compute_type=cfg['whisper_compute'],
                             cpu_threads=cfg['whisper_cpu_threads'], num_workers=1)
        _pipeline = BatchedInferencePipeline(model=model)
        _pipeline_key = key
    segments, info = _pipeline.transcribe(
        str(audio),
        batch_size=cfg['whisper_batch_size'],
        beam_size=cfg['whisper_beam_size'],
        word_timestamps=True,
        vad_filter=cfg['whisper_vad'],
        condition_on_previous_text=True,
    )
    total_duration = max(float(getattr(info, 'duration', 0) or 0), 0.001)
    last_progress = -1
    data = {'language': info.language, 'language_probability': info.language_probability,
            'segments': [], 'words': [], 'processing_mode': cfg['mode']}
    for seg in segments:
        if progress_callback:
            pct = min(99, int((seg.end / total_duration) * 100))
            if pct != last_progress:
                progress_callback(pct, seg.end, total_duration)
                last_progress = pct
        record = {'start': round(seg.start, 3), 'end': round(seg.end, 3), 'text': seg.text.strip(),
                  'avg_logprob': seg.avg_logprob, 'no_speech_prob': seg.no_speech_prob}
        data['segments'].append(record)
        for word in (seg.words or []):
            data['words'].append({'start': round(word.start, 3), 'end': round(word.end, 3),
                                  'text': word.word, 'probability': word.probability, 'speaker': None})
    return data
