@echo off
echo ========================================================
echo   Project Big Tester - Development Environment
echo ========================================================
echo.

echo [1/2] Starting Backend (FastAPI)...
start "Project Big Tester - Backend" cmd /k "cd backend\src && ..\venv\Scripts\activate && uvicorn main:app --reload --host 127.0.0.1 --port 8000"

echo [2/2] Starting Frontend (Next.js)...
start "Project Big Tester - Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo ========================================================
echo   Startup process initiated!
echo   Backend and Frontend are opening in separate windows.
echo.
echo   Waiting a few seconds for the servers to start...
echo ========================================================
echo.
timeout /t 5 /nobreak > nul
start http://localhost:3000

echo Close the newly opened command prompt windows to stop the servers.
echo.
pause
