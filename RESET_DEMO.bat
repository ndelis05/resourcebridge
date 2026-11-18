@echo off
cd /d "%~dp0"
echo Resetting ResourceBridge demo data...
where python >nul 2>nul
if %errorlevel%==0 (
  python reset_db.py
) else (
  py reset_db.py
)
echo.
pause
