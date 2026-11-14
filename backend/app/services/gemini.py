"""Gemini REST client. API key is kept on the local backend, never embedded in frontend JS."""
import json
import os
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from app.config import GEMINI_SETTINGS, GEMINI_API_KEY, get_gemini_model

class GeminiError(RuntimeError):
    pass

def get_key():
    key = os.environ.get('GEMINI_API_KEY', '').strip() or GEMINI_API_KEY
    if key:
        return key
    try:
        return json.loads(GEMINI_SETTINGS.read_text(encoding='utf-8')).get('api_key', '').strip()
    except (OSError, ValueError, AttributeError):
        return ''

def save_key(key):
    key = key.strip()
    if not (10 <= len(key) <= 512) or any(ch.isspace() for ch in key):
        raise ValueError('Enter a valid Gemini API key without spaces.')
    GEMINI_SETTINGS.parent.mkdir(parents=True,exist_ok=True)
    temp = GEMINI_SETTINGS.with_suffix('.tmp')
    temp.write_text(json.dumps({'api_key': key}), encoding='utf-8')
    try:os.chmod(temp, 0o600)
    except OSError:pass
    temp.replace(GEMINI_SETTINGS)

def clear_key():
    GEMINI_SETTINGS.unlink(missing_ok=True)

def _delay(headers, attempt):
    value=headers.get('Retry-After')
    if value:
        try:return min(30, max(0, float(value)))
        except ValueError:
            try:return min(30,max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
            except (TypeError,ValueError,OverflowError):pass
    return min(30, 2 ** (attempt + 2))

def model_name():
    return get_gemini_model()

def analyze(prompt, timeout=120, retries=2):
    key=get_key()
    if not key:raise GeminiError('Gemini API key missing. Save it in the API key settings on the website before generating clips.')
    model=model_name()
    if not model or not all(c.isalnum() or c in '-_.' for c in model):
        raise GeminiError('Invalid Gemini model name.')
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    payload={'contents':[{'role':'user','parts':[{'text':prompt}]}],
             'generationConfig':{'responseMimeType':'application/json','temperature':0.2,'maxOutputTokens':8192}}
    body=json.dumps(payload).encode('utf-8')
    for attempt in range(retries+1):
        req=Request(url, data=body, headers={'Content-Type':'application/json','x-goog-api-key':key},method='POST')
        try:
            with urlopen(req,timeout=timeout) as resp:
                result=json.load(resp)
            parts=result['candidates'][0]['content']['parts']
            text=''.join(p.get('text','') for p in parts)
            if not text.strip():raise GeminiError('Gemini returned an empty response. Check model limits and safety settings.')
            return json.loads(text)
        except HTTPError as exc:
            if exc.code in (429,500,502,503,504) and attempt<retries:
                time.sleep(_delay(exc.headers,attempt))
                continue
            try:detail=json.loads(exc.read().decode('utf-8'))['error'].get('message','')
            except (ValueError,KeyError,UnicodeError):detail=''
            if exc.code==429:
                raise GeminiError('Gemini free-tier rate limit or quota exhausted. Try later or check quotas in Google AI Studio. '+detail[:180]) from exc
            if exc.code==404:
                raise GeminiError(
                    f'Gemini model "{model}" is unavailable for this API key or endpoint. '
                    f'Choose an available model with VIRALCUT_GEMINI_MODEL and retry; the current transcript cache is preserved. '
                    f'{detail[:180]}'
                ) from exc
            if exc.code in (400,401,403):
                raise GeminiError(f'Gemini rejected the request (HTTP {exc.code}). Check your API key, model access and region. {detail[:180]}') from exc
            raise GeminiError(f'Gemini HTTP {exc.code}: {detail[:200]}') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GeminiError(f'Cannot connect to Gemini. Check internet connection: {exc}') from exc
        except (ValueError,KeyError,TypeError,IndexError) as exc:
            raise GeminiError('Gemini returned an invalid structured response. Try again.') from exc
