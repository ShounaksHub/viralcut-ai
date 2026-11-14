# ViralCut AI — local end-to-end AI clip maker

Paste a supported public URL or upload a video and produce genuine 20–60 second vertical H.264/AAC MP4 clips, with Gemini-selected highlights, word-timed captions, previews, edit/re-render and ZIP exports. No Ollama; uses the Gemini API free tier (subject to Google quotas). **No guarantee of virality. The key stays on your PC but the transcript excerpts are transmitted to Google for AI analysis; review Google free-tier data practices and do not submit sensitive content without permission.**

## Windows setup

1. Install **Python 3.11 or 3.12** and ensure `python` is on PATH.
2. Install **FFmpeg with FFprobe and libass subtitle support** (`winget install Gyan.FFmpeg`), restart terminal; run `ffmpeg -filters` and check `subtitles` is available.
3. Have a Gemini Developer API key from https://aistudio.google.com/apikey. You **do not need Ollama**. The default model is `gemini-3.5-flash-lite`, which has availability subject to your account, region, and limits. To change the model, set `VIRALCUT_GEMINI_MODEL` in your Windows environment.
4. Download and unzip the project; double-click **start_windows.bat**. It creates `.venv` and installs Python dependencies on first launch. The Whisper model is downloaded during first transcription.
5. Open **http://127.0.0.1:8000**, paste your Gemini API key into **Gemini AI settings** and click **Save API key**. The key is stored on your PC only in `storage/gemini_settings.json` (not included in the ZIP, not displayed later). Alternatively set the environment variable `GEMINI_API_KEY`. Then upload or paste a supported public URL and click **Generate my clips**. Processing may be slow on CPU. The status updates are real stages, not guessed percentages.

By default, ViralCut creates one batch of quality clips per video. It targets at least 4 clips when the source is long enough to contain four independent 20-second moments, increases the target for longer videos, and caps the batch at 20 clips by default. Set `VIRALCUT_MIN_CLIPS` or `VIRALCUT_MAX_CLIPS` to adjust the policy. Very short videos cannot honestly produce four non-overlapping 20-second clips.

Whisper processing modes are selectable before submission: `FAST` uses `base`, INT8, batch 8, beam 2; `BALANCED` uses `small`, INT8, batch 6, beam 3; `HIGH_ACCURACY` uses `small`, INT8, batch 4, beam 5. Word timestamps, VAD, and captions remain enabled in every mode.

Balanced CPU transcription uses Faster-Whisper's `BatchedInferencePipeline`, conservative CPU thread settings, and batch size 6. FFmpeg remains on software H.264 by default for reliable ASS caption burn-in; `/api/health` reports whether the installed FFmpeg advertises Intel `h264_qsv`. Render timing is logged per stage for hardware-specific tuning.

**Requirements:** 4–8+ GB RAM recommended depending on the Whisper model; no local language model is required. CPU-only operation is supported but can take significant time. Enough disk space for input + extracted WAV + output MP4s. For low-memory devices set `VIRALCUT_WHISPER_MODEL=base` or `tiny` for lighter transcription. Hindi/Bengali caption rendering requires a compatible Unicode font available to FFmpeg/libass; install Noto Sans Bengali/Devanagari if needed and configure font selection in render.py.

## Workflow

1. Allowlisted public HTTPS URL with `yt-dlp`, or chunked file upload, with FFprobe validation.
2. Extract mono 16 kHz WAV; transcribe all speech using Faster-Whisper and real word timestamps.
3. Analyze timestamped transcript in overlapping windows using the Gemini API with structured JSON; candidates must be 20–60 seconds and snapped to spoken segment boundaries. Weighted scoring: hook 25%, content 20%, emotion 15%, self-containment 15%, payoff 10%, speech clarity 10%, visual proxy 5%. Overlap removal prevents near-duplicates. Scores are qualitative model estimates, not measured audience retention.
4. Sample face positions when OpenCV is installed (stable median position), otherwise center crop; export true 1080×1920 H.264 MP4 at 30fps with AAC audio normalization.
5. Word-synchronized ASS captions burned into MP4; 4 styles: professional, yellow, minimal, karaoke. Multi-language content stays in the source language.
6. Preview or download individual videos or ZIP. Edit start/end (20–60s), caption theme, crop center and re-render **without retranscribing**.

## API

`GET /api/settings/gemini`; `POST /api/settings/gemini`; `DELETE /api/settings/gemini`; `POST /api/upload`; `POST /api/url`; `GET /api/health`; `GET /api/jobs`; `GET /api/jobs/{id}`; `POST /api/jobs/{id}/cancel`; `GET /api/jobs/{id}/transcript`; `GET /api/jobs/{id}/clips`; `GET /api/jobs/{id}/clips/{clip_id}/video`; `GET /api/jobs/{id}/clips/{clip_id}/download`; `GET /api/jobs/{id}/download-all`; `POST /api/jobs/{id}/clips/{clip_id}/rerender`; `POST /api/jobs/{id}/retry`; `DELETE /api/jobs/{id}`.

## Developers and tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH=(Join-Path (Get-Location) '.')
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`frontend/dist` is a ready-to-run standalone HTML/CSS/JS frontend. React/Vite source is also included in `frontend/src`, with basic clip preview/download/re-render controls. The prepackaged dependency-free `dist` interface is the **default recommended UI** and has the more detailed crop/theme editing panel. Rebuilding React requires npm dependencies and overwrites `dist`.

## Limitations

- Running the Whisper model, live yt-dlp downloads, and the *full Gemini highlight pipeline* require real credentials/network/hardware not available to automated tests in this environment; those conditions are **not claimed verified**. Render pipeline *was* tested using genuine local FFmpeg media. No mock AI scores or random timestamp fallback exists: if Gemini fails or finds no valid candidates, job fails with an explanatory error.
- Semantic selection is transcript-first, not full visual-semantic recognition. Visual score is LLM-estimated from spoken context; no object recognition or active-speaker diarization. Face crop uses multiple-frame face sampling, not reliable active-speaker tracking. Hindi/Bengali rendering depends on appropriate OS fonts. Caption positions are fixed mobile-safe lower-third; they do not yet automatically avoid faces.
- Job queue runs in-process (one worker). On startup, jobs left in active states are marked `interrupted` and can be retried or deleted. Designed for local personal use, **not safe as a public internet service**: add authorization, network-isolated URL download execution, quotas, and persistent workers before deployment. DNS re-resolution inside yt-dlp is a residual SSRF risk. Only download videos you have rights to process, respecting platform terms, access restrictions and copyright.
- URLs are restricted to public HTTPS YouTube, Vimeo, Dailymotion and Twitch. The extractor must be supported by your installed `yt-dlp`; region/private/DRM restrictions aren't bypassed.
- Speech-heavy video of at least about 30 seconds works best. Word-timestamp quality depends on speech, noise and language. A weak/short video can legitimately yield zero clips rather than fake results.

## Gemini free-tier notes

- **No Ollama installation or account is needed.** You only need your own Gemini API key and internet access while analyzing the transcript. FFmpeg clipping and Whisper transcription run locally.
- This release uses Google's `generateContent` REST API and defaults to `gemini-3.5-flash-lite`. Access and free-tier limits may vary by project and change over time. You can confirm model availability in Google AI Studio.
- For long videos, the transcript is divided into overlapping chunks; each chunk makes a separate Gemini request. You can hit free-tier request/token limits. HTTP 429 errors trigger limited retries, then clearly report quota exhaustion.
- If you use a billable key or switch to a paid model, **charges are possible**. ViralCut cannot enforce Google billing caps.
- The saved key is kept in `storage/gemini_settings.json` and is never returned by the settings endpoint. Treat that local file as a secret, do not upload or commit it. The app is intended only for access at `127.0.0.1:8000`, not public deployment.
- Google may use free-tier inputs to improve its products, subject to its policies. Do not submit content you are not authorized to share.
