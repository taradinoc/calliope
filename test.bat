@echo off
cd /d "%~dp0"
python -B -m unittest discover -s tests -v
exit /b %errorlevel%
