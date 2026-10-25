@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" goto missing_config

if exist ".venv\Scripts\python.exe" goto check_flask

where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto check_flask

:use_python
python -m venv .venv
if errorlevel 1 goto failed

:check_flask
".venv\Scripts\python.exe" -c "import flask, supabase, dotenv" >nul 2>nul
if not errorlevel 1 goto run
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

:run
echo.
echo Smart Parking inditasa es varakozas a szerverre...
echo Leallitas: Ctrl+C vagy az ablak bezarasa.
echo.

start /b "" ".venv\Scripts\python.exe" app.py
set /a parking_wait_count=0

:wait_server
timeout /t 1 /nobreak >nul
curl -s http://127.0.0.1:5000 >nul 2>nul
if not errorlevel 1 goto open_browser
set /a parking_wait_count+=1
if %parking_wait_count% geq 30 goto failed
goto wait_server

:open_browser
start http://127.0.0.1:5000

pause >nul
exit /b 0

:missing_config
echo Masold a .env.example fajlt .env neven, es toltsd ki a beallitasokat.
echo Reszletes leiras: a projekt README.md fajljaban.
pause
exit /b 1

:failed
echo.
echo Nem sikerult elinditani az oldalt. A hiba fent olvashato.
echo Az elso inditashoz Python 3.11 vagy ujabb es internet szukseges.
pause
exit /b 1
