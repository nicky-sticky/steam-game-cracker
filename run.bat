@echo off
setlocal
cd /d "%~dp0"

if not defined PYTHON if exist .python set /p PYTHON=<.python
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" launch.py %*
exit /b %errorlevel%
