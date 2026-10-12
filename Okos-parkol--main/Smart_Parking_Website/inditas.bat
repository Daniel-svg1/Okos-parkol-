@echo off
setlocal
cd /d "%~dp0"

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
".venv\Scripts\python.exe" -c "import flask" >nul 2>nul
if not errorlevel 1 goto run
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

:run
echo.
echo Smart Parking - http://127.0.0.1:5000
echo Megnyitas a bongeszoben...
echo Leallitas: Ctrl+C vagy az ablak bezarasa.
echo.

explorer "http://127.0.0.1:5000"

".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Nem sikerult elinditani az oldalt. A hiba fent olvashato.
echo Az elso inditashoz Python 3.9 vagy ujabb es internet szukseges.
pause
exit /b 1