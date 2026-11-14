"""Transcript-grounded Gemini selection, with deterministic validations and ranking."""
import math
from app.services.gemini import analyze as gemini_analyze
from app.config import MIN_CLIPS_PER_JOB, MAX_CLIPS_PER_JOB

WEIGHTS = {'hook':25,'content':20,'emotion':15,'standalone':15,'payoff':10,'clarity':10,'visual':5}
CRITERIA = tuple(WEIGHTS)

class AnalysisError(RuntimeError): pass

def desired_clip_count(duration, minimum=MIN_CLIPS_PER_JOB, maximum=MAX_CLIPS_PER_JOB):
    """Choose a quality-oriented batch size from source duration.

    A 20-second minimum clip means very short sources cannot honestly produce
    four independent clips. Longer sources receive more candidates, with a
    conservative cap for a 16 GB machine.
    """
    duration = max(0.0, float(duration))
    physically_possible = math.floor(duration / 20.0)
    if physically_possible <= 0:
        return 0
    target = math.ceil(duration / 90.0)
    return min(maximum, physically_possible, max(minimum, target))

def chunks(segments, max_chars=8500, overlap=3):
    batch=[]; count=0
    for segment in segments:
        line=f"[{segment['start']:.2f}-{segment['end']:.2f}] {segment['text']}"
        if batch and count+len(line)>max_chars:
            yield batch
            batch=batch[-overlap:] if len(batch)>overlap else batch[-1:]
            count=sum(len(x[1]) for x in batch)
        batch.append((segment,line));count+=len(line)
    if batch:yield batch

def score(values,weights=WEIGHTS):
    values={key:max(0,min(10,float(values.get(key,0)))) for key in CRITERIA}
    return round(sum(values[k]*weights[k] for k in CRITERIA)/sum(weights.values())*10,1)

def _nearest_boundary(time,boundaries):
    return min(boundaries,key=lambda b:abs(b-time))

def _semantic_similarity(left, right):
    def tokens(item):
        text=' '.join(str(item.get(k,'')) for k in ('title','hook','reason','description')).lower()
        return {token for token in ''.join(ch if ch.isalnum() else ' ' for ch in text).split() if len(token)>2}
    a,b=tokens(left),tokens(right)
    return len(a & b)/max(1,len(a | b))

def select_clips(transcript,duration,count=None,analyzer=gemini_analyze,weights=WEIGHTS):
    count = desired_clip_count(duration) if count is None else max(1, int(count))
    segments=[s for s in transcript['segments'] if s.get('text','').strip()]
    if not segments:raise AnalysisError('No speech was detected, so meaningful speech-based highlights cannot be selected.')
    boundaries_start=[float(s['start']) for s in segments];boundaries_end=[float(s['end']) for s in segments]
    candidates=[]
    for batch in chunks(segments):
        content='\n'.join(line for _,line in batch)
        candidate_limit=max(8, min(16, count * 2))
        prompt=f'''You are a discerning short-form video editor. Choose genuinely compelling, self-contained 20-60 second moments from THIS timestamped transcript only. Prioritize complete thoughts, opening hooks, stories and payoffs. Do NOT invent times, facts, or speech. If this part has no good highlight return an empty list.
Return JSON object {{"clips":[{{"start":number,"end":number,"title":string,"hook":string,"reason":string,"description":string,"hashtags":[string],"alternative_titles":[string,string,string],"ratings":{{"hook":0-10,"content":0-10,"emotion":0-10,"standalone":0-10,"payoff":0-10,"clarity":0-10,"visual":0-10}}}}]}}.
Scores must be reasoned, not uniformly high. Visual is uncertain from transcript; score 5 unless known. Return at most {candidate_limit} candidate clips. Only select moments in these timestamps. Preserve the language spoken. Video length {duration:.1f}s. The full video target is {count} final clips, so prefer distinct moments rather than near-duplicates.
TRANSCRIPT:\n{content}'''
        answer=analyzer(prompt)
        if not isinstance(answer,dict) or not isinstance(answer.get('clips'),list):raise AnalysisError('Gemini analysis missing clips array.')
        lo=float(batch[0][0]['start']);hi=float(batch[-1][0]['end'])
        for item in answer['clips']:
            try:
                start=float(item['start']);end=float(item['end'])
                if not math.isfinite(start) or not math.isfinite(end) or start<lo-1 or end>hi+1:continue
                start=_nearest_boundary(max(lo,start),boundaries_start)
                end=_nearest_boundary(min(hi,end),boundaries_end)
                if end-start<20 or end-start>60:continue
                ratings=item.get('ratings',{})
                if not isinstance(ratings,dict):continue
                engagement=score(ratings,weights)
                title=str(item.get('title','Highlight')).strip()[:100]
                candidates.append({'start':round(start,3),'end':round(end,3),'duration':round(end-start,3),
                  'title':title,'hook':str(item.get('hook',''))[:200], 'reason':str(item.get('reason',''))[:400],
                  'description':str(item.get('description',''))[:500],
                  'hashtags':[str(t)[:40] for t in item.get('hashtags',[]) if isinstance(t,str)][:8],
                  'alternative_titles':[str(t)[:100] for t in item.get('alternative_titles',[]) if isinstance(t,str)][:3],
                  'ratings':ratings,'score':engagement})
            except (ValueError,TypeError,KeyError,OverflowError):continue
    if not candidates:
        raise AnalysisError('The model could not find a valid self-contained 20–60 second highlight. Try a longer speech-based video or another model.')
    chosen=[]
    for candidate in sorted(candidates,key=lambda c:c['score'],reverse=True):
        def distinct(existing):
            overlap=max(0,min(candidate['end'],existing['end'])-max(candidate['start'],existing['start'])) / min(candidate['duration'],existing['duration'])
            semantic=_semantic_similarity(candidate,existing)
            # Temporal overlap is the primary guard; semantic similarity only
            # strengthens a genuinely overlapping duplicate, not adjacent moments.
            return overlap < 0.35 or not (semantic >= 0.95 and overlap >= 0.35)
        if all(distinct(x) for x in chosen):
            chosen.append(candidate)
        if len(chosen)>=count:break
    return chosen
