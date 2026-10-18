@echo off
title Crypto Analyzer Terminal - Launcher
color 0B

echo ======================================================================
echo           CRYPTO ANALYZER INSTITUTIONAL TRADING TERMINAL
echo ======================================================================
echo.

:: Ensure we are in the project root directory
cd /d "%~dp0"

echo [*] Checking Python and Node.js environments...
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python launcher py was not found in PATH!
    echo Please ensure Python is installed and added to PATH.
    pause
    exit /b 1
)

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm was not found in PATH!
    echo Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)

echo [OK] Python and Node.js environments verified.
echo.

:: Step 1: Start FastAPI Backend
echo [1/2] Starting FastAPI Backend on http://127.0.0.1:8008 ...
start "Crypto Analyzer - Backend" /D "%~dp0" cmd /k "title Crypto Analyzer Backend && py -m uvicorn server:app --host 127.0.0.1 --port 8008"

:: Give backend 2 seconds to bind port
ping 127.0.0.1 -n 3 >nul

:: Step 2: Start React Vite Frontend
echo [2/2] Starting Vite Frontend on http://localhost:5174 ...
start "Crypto Analyzer - Frontend" /D "%~dp0frontend" cmd /k "title Crypto Analyzer Frontend && npm run dev"

:: Give frontend 3 seconds to spin up, then open browser
ping 127.0.0.1 -n 4 >nul

echo.
echo ======================================================================
echo   ALL SYSTEMS ONLINE:
echo   - Backend API:  http://127.0.0.1:8008
echo   - Frontend UI:  http://localhost:5174
echo ======================================================================
echo.
echo Opening browser to http://localhost:5174 ...
start http://localhost:5174

echo.
echo Press any key to close this launcher window (services keep running)...
pause >nul
