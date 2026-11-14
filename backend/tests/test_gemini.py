import json
from io import BytesIO
from urllib.error import HTTPError
import pytest
from app.services import gemini
from app.services.highlights import select_clips

class FakeResponse:
    def __init__(self, data): self.data=data
    def __enter__(self):return BytesIO(json.dumps(self.data).encode())
    def __exit__(self,*_):pass

def test_gemini_json_request(monkeypatch):
    monkeypatch.setattr(gemini,'get_key',lambda:'test-not-a-real-api-key')
    monkeypatch.delenv('VIRALCUT_GEMINI_MODEL', raising=False)
    def fake_open(request,timeout):
        assert 'gemini-3.5-flash-lite:generateContent' in request.full_url
        assert request.get_header('X-goog-api-key')=='test-not-a-real-api-key'
        request_data=json.loads(request.data)
        assert request_data['generationConfig']['responseMimeType']=='application/json'
        return FakeResponse({'candidates':[{'content':{'parts':[{'text':'{"clips":[]}'}]}}]})
    monkeypatch.setattr(gemini,'urlopen',fake_open)
    assert gemini.analyze('hello')['clips']==[]

def test_gemini_model_can_be_overridden(monkeypatch):
    monkeypatch.setenv('VIRALCUT_GEMINI_MODEL', 'gemini-test-model')
    assert gemini.model_name() == 'gemini-test-model'

def test_gemini_json_flows_into_highlight_selection(monkeypatch):
    monkeypatch.setattr(gemini,'get_key',lambda:'test-not-a-real-api-key')
    response={'candidates':[{'content':{'parts':[{'text':json.dumps({'clips':[{
        'start':0,'end':23,'title':'A complete moment','reason':'A useful standalone point',
        'ratings':{'hook':8,'content':8,'emotion':6,'standalone':9,'payoff':7,'clarity':9,'visual':5}
    }]})}]}}]}
    monkeypatch.setattr(gemini,'urlopen',lambda request,timeout: FakeResponse(response))
    segments=[{'start':float(i*2),'end':float(i*2+1.8),'text':f'Important point {i}.'} for i in range(14)]
    result=select_clips({'segments':segments}, 28, count=1, analyzer=lambda prompt: gemini.analyze(prompt))
    assert len(result)==1 and result[0]['start'] >= 0 and result[0]['duration'] >= 20

def test_gemini_no_key(monkeypatch):
    monkeypatch.setattr(gemini,'get_key',lambda:'')
    with pytest.raises(gemini.GeminiError,match='API key missing'):
        gemini.analyze('hello')

def test_gemini_429_error(monkeypatch):
    monkeypatch.setattr(gemini,'get_key',lambda:'test-not-a-real-api-key')
    def bad_request(*args,**kwargs):
        raise HTTPError('https://generativelanguage.googleapis.com/',429,'Too many requests',{},BytesIO(b'{"error":{"message":"Quota exceeded"}}'))
    monkeypatch.setattr(gemini,'urlopen',bad_request)
    with pytest.raises(gemini.GeminiError, match='rate limit or quota'):
        gemini.analyze('hello',retries=0)

def test_gemini_404_error_is_actionable(monkeypatch):
    monkeypatch.setattr(gemini,'get_key',lambda:'test-not-a-real-api-key')
    def bad_request(*args,**kwargs):
        raise HTTPError('https://generativelanguage.googleapis.com/',404,'Not found',{},BytesIO(
            b'{"error":{"message":"model is no longer available"}}'))
    monkeypatch.setattr(gemini,'urlopen',bad_request)
    with pytest.raises(gemini.GeminiError, match='unavailable'):
        gemini.analyze('hello',retries=0)

def test_api_key_save_and_no_echo(client,tmp_path,monkeypatch):
    settings=tmp_path/'secret-settings.json'
    monkeypatch.setattr(gemini,'GEMINI_SETTINGS',settings)
    monkeypatch.setattr(gemini,'GEMINI_API_KEY','')
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    key='dummy-gemini-test-key-123'
    r=client.post('/api/settings/gemini',json={'api_key':key})
    assert r.status_code==200 and r.json()=={'configured':True}
    assert json.loads(settings.read_text())['api_key']==key
    status=client.get('/api/settings/gemini')
    assert status.json()['configured'] is True
    assert key not in status.text
    assert client.delete('/api/settings/gemini').status_code==200
    assert not settings.exists()
