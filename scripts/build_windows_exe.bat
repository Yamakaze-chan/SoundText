@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.."
echo [1/5] Checking Windows environment...
if not "%OS%"=="Windows_NT" (
  echo [ERROR] Build the Windows EXE on Windows x64, not Linux/macOS.
  exit /b 1
)
set "PYTHON_EXE=python"
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
"%PYTHON_EXE%" -c "import sys,struct; assert sys.version_info[:2] in [(3,10),(3,11),(3,12)] and struct.calcsize('P')==8, 'Use 64-bit Python 3.10-3.12 (3.11 recommended)'"
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" -c "import importlib.util as u; names = ('customtkinter', 'PIL', 'funasr', 'torch', 'onnxruntime', 'sherpa_onnx', 'pyaudiowpatch', 'optimum'); missing = [x for x in names if u.find_spec(x) is None]; assert not missing, 'Missing: '+', '.join(missing)"
if errorlevel 1 (
  echo [ERROR] Missing runtime libraries. Install requirements.txt in this Python environment first.
  exit /b 1
)

echo [2/5] Installing build tool...
"%PYTHON_EXE%" -m pip install -r requirements-exe.txt
if errorlevel 1 exit /b 1

echo [3/5] Preparing editable config and EXE branding...
"%PYTHON_EXE%" -m app.user_config
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" scripts\prepare_exe_icon.py
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" scripts\prepare_exe_metadata.py
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" -c "from pathlib import Path; p=Path('main.py'); assert p.is_file(), 'Missing main.py in project root'; import main; print('[OK] Source main.py importable')"
if errorlevel 1 (
  echo [ERROR] Could not import main.py from the current build environment.
  exit /b 1
)

echo [4/5] Creating onedir EXE. This can be large due to Torch and other runtime libraries...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean SoundText.spec
if errorlevel 1 exit /b 1

rem FunASR __init__.py reads version.txt at import time. The spec must
rem bundle the physical file into _internal\funasr on onedir builds.
if not exist "dist\SoundText\_internal\funasr\version.txt" (
  echo [ERROR] Missing frozen FunASR version.txt. Check SoundText.spec datas.
  exit /b 1
)
echo [OK] Frozen FunASR version.txt exists.

rem Verify the built EXE, not only the unbundled Python source. This command
rem must not open the UI or start audio/model processing.
echo [4/5] Verifying imports inside the generated EXE...
start "" /wait "dist\SoundText\SoundText.exe" --check-bundle
if errorlevel 1 (
  echo [ERROR] The EXE cannot import the application. See dist\SoundText\output\logs\startup.log
  exit /b 1
)

rem Safety: Do NOT automatically launch heavy Torch/FunASR/Optimum import
rem smoke checks after build. Running them back-to-back can exhaust RAM on
rem 8-16 GB Windows PCs. Perform diagnostic checks manually if needed.
echo [4/5] Heavy model/import checks SKIPPED for RAM safety.
echo [INFO] Optional diagnostics after verifying RAM availability:
echo   SoundText.exe --check-funasr-source
echo   SoundText.exe --check-translation-runtime

echo [5/5] Copying editable files (NOT AI model weights)...
copy /Y "branding.json" "dist\SoundText\branding.json" >nul
if errorlevel 1 exit /b 1
copy /Y "config_stt_vi_ja.json" "dist\SoundText\config_stt_vi_ja.json" >nul
if errorlevel 1 exit /b 1
xcopy /E /I /Y "assets\default-branding" "dist\SoundText\assets\default-branding" >nul
if errorlevel 1 exit /b 1
if exist "assets\branding" (
  xcopy /E /I /Y "assets\branding" "dist\SoundText\assets\branding" >nul
  if errorlevel 1 exit /b 1
)
if not exist "dist\SoundText\models" mkdir "dist\SoundText\models"
if not exist "dist\SoundText\output" mkdir "dist\SoundText\output"
copy /Y "models\README.txt" "dist\SoundText\models\README.txt" >nul
copy /Y "README_EXE.md" "dist\SoundText\README_EXE.md" >nul
copy /Y "branding.default.json" "dist\SoundText\branding.default.json" >nul
copy /Y "config_stt_vi_ja.default.json" "dist\SoundText\config_stt_vi_ja.default.json" >nul

echo.
echo [OK] EXE prepared: dist\SoundText\SoundText.exe
echo Copy the CONTENTS of existing models\ into dist\SoundText\models\.
echo Do NOT put models inside _internal, and do NOT copy only SoundText.exe.
echo Check output\logs\startup.log if startup fails.
pause
exit /b 0
