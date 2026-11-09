@echo off
cd /d "%~dp0"
echo Installing/updating PyInstaller...
python -m pip install --upgrade pyinstaller || goto :fail
echo Building IPV4Switcher.exe...
python -m PyInstaller --clean --noconfirm IPV4.spec || goto :fail
echo.
echo Done: %~dp0dist\IPV4Switcher.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
