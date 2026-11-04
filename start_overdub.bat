@echo off
call "%~dp0start.bat" --overdub --track 1 %*
exit /b %errorlevel%
