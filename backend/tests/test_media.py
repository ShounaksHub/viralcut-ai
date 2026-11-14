from app.services.media import probe,extract_audio,MediaError
import wave
import pytest

def test_real_media_probe(video):
    meta=probe(video)
    assert meta['width']==320 and meta['height']==180
    assert 1.9<meta['duration']<2.2
    assert meta['has_audio'] and meta['fps']==25

def test_extract_real_audio(video,tmp_path):
    output=extract_audio(video,tmp_path/'audio.wav')
    with wave.open(str(output),'rb') as wav:
        assert wav.getframerate()==16000 and wav.getnchannels()==1
        assert wav.getnframes()>30000

def test_invalid_media(tmp_path):
    p=tmp_path/'broken.mp4';p.write_bytes(b'not a video')
    with pytest.raises(MediaError):probe(p)
