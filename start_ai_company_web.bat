@echo off
cd /d "%~dp0"
set AI_COMPANY_PORT=8788
set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" company_web_app.py
) else (
  py company_web_app.py
)
pause
