@echo off
REM build.bat — builds PDF Toolbox into dist\PDFToolbox\PDFToolbox.exe
REM
REM Run this from the project root (same folder as main.py, requirements.txt,
REM and PDFToolbox.spec) by double-clicking it, or from a terminal:
REM     build.bat
REM
REM Safe to re-run any time — it rebuilds from a clean state each time so
REM you never end up testing a stale mix of old and new files.

setlocal enabledelayedexpansion

echo.
echo ============================================
echo  PDF Toolbox - build
echo ============================================
echo.

REM --- sanity checks -------------------------------------------------

if not exist "main.py" (
    echo [ERROR] main.py not found in this folder.
    echo Run build.bat from the project root - the same folder main.py is in.
    goto :fail
)

if not exist "PDFToolbox.spec" (
    echo [ERROR] PDFToolbox.spec not found in this folder.
    echo Put PDFToolbox.spec next to main.py before running this script.
    goto :fail
)

if not exist "app_icon.ico" (
    echo [ERROR] app_icon.ico not found in this folder.
    echo PDFToolbox.spec expects it right next to main.py.
    goto :fail
)

REM --- activate the venv, if there is one -----------------------------
REM Adjust this path first if your virtual environment folder isn't
REM called "venv" - e.g. change to ".venv\Scripts\activate.bat".

if exist "venv\Scripts\activate.bat" (
    echo Activating venv...
    call "venv\Scripts\activate.bat"
) else (
    echo [WARNING] No venv\Scripts\activate.bat found - continuing with
    echo           whatever Python is currently active on PATH.
)

REM --- make sure pyinstaller is available -----------------------------

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo pyinstaller not found - installing it now...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install pyinstaller. Check your internet
        echo         connection / pip setup and try again.
        goto :fail
    )
)

REM --- make sure the app's own dependencies are installed -------------
REM Cheap to run every time; pip skips anything already satisfied.

if exist "requirements.txt" (
    echo Checking requirements.txt is satisfied...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install one or more requirements.
        goto :fail
    )
)

REM --- clean previous build output ------------------------------------
REM Prevents a broken build from silently reusing stale files left over
REM from a previous, different build attempt.

echo Cleaning previous build output...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM --- build -----------------------------------------------------------

echo.
echo Building with PyInstaller (this can take a minute or two)...
echo.
pyinstaller PDFToolbox.spec

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed - see the PyInstaller output above for
    echo         the actual error.
    goto :fail
)

if not exist "dist\PDFToolbox\PDFToolbox.exe" (
    echo.
    echo [ERROR] Build reported success but dist\PDFToolbox\PDFToolbox.exe
    echo         wasn't produced. Check the output above.
    goto :fail
)

echo.
echo ============================================
echo  Build succeeded:
echo  dist\PDFToolbox\PDFToolbox.exe
echo ============================================
echo.
echo Reminder: test this on a machine (or user account) that does NOT
echo have your dev venv active, to catch anything PyInstaller missed.
echo.
pause
exit /b 0

:fail
echo.
echo Build did not complete. See the messages above.
echo.
pause
exit /b 1
