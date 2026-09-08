@echo off
REM DRISHTI - start the application and open it in the browser (Windows).
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.9 or newer and retry.
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, numpy" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies...
  python -m pip install -r requirements.txt || (pause & exit /b 1)
)

if not exist "frontend\vendor\leaflet.js" (
  echo Vendoring front-end assets ^(one time, needs network^)...
  python scripts\vendor_assets.py
)

set DRISHTI_PORT=8093
set DRISHTI_URL=http://127.0.0.1:%DRISHTI_PORT%

REM Open the browser as soon as the server actually answers, rather than
REM guessing a fixed delay - a cold start with the live board pre-warming can
REM take longer than any delay it would be safe to guess, and a delay too
REM short just opens a spinner. Runs detached so it does not block uvicorn,
REM which needs the foreground below to keep this window (and Ctrl+C) live.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command ^
  "for ($i=0; $i -lt 60; $i++) {" ^
  "  try {" ^
  "    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%DRISHTI_URL%/healthz';" ^
  "    if ($r.StatusCode -eq 200) { Start-Process '%DRISHTI_URL%'; break }" ^
  "  } catch {}" ^
  "  Start-Sleep -Seconds 1" ^
  "}"

echo.
echo   DRISHTI starting at %DRISHTI_URL%
echo   Your browser will open automatically once it is ready.
echo   API documentation   %DRISHTI_URL%/docs
echo   Press Ctrl+C to stop.
echo.
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port %DRISHTI_PORT%
