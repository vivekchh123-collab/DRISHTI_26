@echo off
REM DRISHTI - start the application (Windows).
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.9 or newer and retry.
  exit /b 1
)

python -c "import fastapi, uvicorn, numpy" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies...
  python -m pip install -r requirements.txt || exit /b 1
)

if not exist "frontendendor\leaflet.js" (
  echo Vendoring front-end assets ^(one time, needs network^)...
  python scriptsendor_assets.py
)

echo.
echo   DRISHTI running at http://127.0.0.1:8000
echo   API documentation   http://127.0.0.1:8000/docs
echo   Press Ctrl+C to stop.
echo.
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
