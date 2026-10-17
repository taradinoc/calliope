@echo off
python "%~dp0tools\build.py" %*
exit /b %errorlevel%
