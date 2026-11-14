@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python not found. Install Python 3.11 or 3.12. & pause & exit /b 1)
where ffmpeg >nul 2>&1 || (echo FFmpeg not found. Add FFmpeg bin to PATH. & pause & exit /b 1)
where ffprobe >nul 2>&1 || (echo FFprobe not found. Install the full FFmpeg package and add its bin folder to PATH. & pause & exit /b 1)
if not exist .venv (python -m venv .venv || exit /b 1)
if not exist .venv\.requirements.ok (
  .venv\Scripts\python.exe -m pip install -r backend\requirements.txt || (pause & exit /b 1)
  type nul > .venv\.requirements.ok
)
if not exist frontend\dist\index.html (
  where npm >nul 2>&1 || (echo Node.js and npm required. & pause & exit /b 1)
  cd frontend
  call npm install || (pause & exit /b 1)
  call npm run build || (pause & exit /b 1)
  cd ..
)
echo ViralCut AI at http://127.0.0.1:8000
echo No Ollama needed. Open the website and save your Gemini API key in Settings.
echo First transcription downloads Whisper model once. Press Ctrl+C to stop.
set PYTHONPATH=%CD%\backend
set PYTHONUTF8=1
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
