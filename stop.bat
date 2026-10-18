@echo off
title Crypto Analyzer Terminal - Stopper
color 0C

echo ======================================================================
echo           STOPPING CRYPTO ANALYZER TERMINAL SERVICES
echo ======================================================================
echo.

:: 1. Terminate process on Port 8008 (FastAPI Backend)
echo [*] Checking for Backend process on port 8008...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8008 ^| findstr LISTENING') do (
    echo [*] Terminating Backend PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)

:: 2. Terminate process on Port 5174 (Vite Frontend)
echo [*] Checking for Frontend process on port 5174...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5174 ^| findstr LISTENING') do (
    echo [*] Terminating Frontend PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)

:: 3. Close titled windows if still open
taskkill /F /FI "WINDOWTITLE eq Crypto Analyzer Backend*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Crypto Analyzer Frontend*" >nul 2>&1

echo.
echo ======================================================================
echo   All Crypto Analyzer services stopped successfully!
echo ======================================================================
echo.
ping 127.0.0.1 -n 3 >nul
