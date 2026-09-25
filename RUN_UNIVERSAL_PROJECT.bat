@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo Universal Medical PDF Diabetes Tracker
 echo ================================================

echo.
echo Installing/checking Python dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Check Python and internet access.
  pause
  exit /b 1
)

echo.
echo Starting backend at http://127.0.0.1:5000 ...
start "Diabetes Tracker Backend" cmd /k "cd /d "%~dp0" && python diabetes_tracker_backend.py"

timeout /t 2 /nobreak >nul

echo Starting frontend at http://127.0.0.1:8000 ...
start "Diabetes Tracker Frontend" cmd /k "cd /d "%~dp0" && python -m http.server 8000 --bind 127.0.0.1"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000/diabetes_tracker_pdf_auto_fill.html"

echo.
echo Project started.
echo Keep both command windows open while using the application.
endlocal
