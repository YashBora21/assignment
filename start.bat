@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo Starting TenderExtractPro from "%ROOT%"
echo.

set "PY=%ROOT%venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] Python virtual environment not found at "%ROOT%venv".
    echo.
    echo Create it once with:
    echo     python -m venv venv
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Backend dependencies are missing from the venv.
    echo Install them with:
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm was not found on PATH. Install Node.js 20+ and retry.
    pause
    exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
    echo [setup] Frontend dependencies not installed. Running npm install...
    pushd "%ROOT%frontend"
    call npm install
    if errorlevel 1 (
        popd
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    popd
)

echo [1/2] Starting FastAPI backend on port 8000...
start "TenderExtractPro API" cmd /k "venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000 --log-level info"
timeout /t 4 /nobreak >nul

echo [2/2] Starting React frontend on port 5173...
start "TenderExtractPro UI" cmd /k "cd frontend && npm run dev"
timeout /t 2 /nobreak >nul

echo.
echo  ==========================================
echo   TenderExtractPro is starting up...
echo  ==========================================
echo   API  : http://localhost:8000
echo   UI   : http://localhost:5173
echo   Docs : http://localhost:8000/docs
echo  ==========================================
echo.
echo  Wait ~30s for the embedding model to load on first run.
echo  (Subsequent runs are faster due to the HuggingFace cache.)
echo.
endlocal
