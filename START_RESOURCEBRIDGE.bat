@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   ResourceBridge
 echo   CHW Resource Knowledge Platform
echo ========================================
echo.
where python >nul 2>nul
if %errorlevel%==0 (
  start "" "http://127.0.0.1:4173"
  python server.py
) else (
  where py >nul 2>nul
  if %errorlevel%==0 (
    start "" "http://127.0.0.1:4173"
    py server.py
  ) else (
    echo Python was not found on this computer.
    echo Install Python or open this folder in PowerShell and run python server.py
    pause
  )
)
