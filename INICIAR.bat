@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (echo Falha ao instalar dependencias & pause & exit /b 1)
start "" "http://127.0.0.1:8014"
py -m uvicorn app:app --host 127.0.0.1 --port 8014
pause
