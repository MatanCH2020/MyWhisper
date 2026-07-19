@echo off
:: MyWhisper installer — double-click to install.
:: Downloads the project and sets everything up (Python 3.12, CUDA libs, shortcut).
title MyWhisper Installer
echo.
echo  === MyWhisper — Hebrew dictation for Windows ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/MatanCH2020/MyWhisper/main/scripts/install.ps1 | iex"
echo.
pause
