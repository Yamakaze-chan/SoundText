@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "SOUNDTEXT_ROOT=%~dp0"
set "MODELS_DIR=%~dp0models"
set "HF_HOME=%MODELS_DIR%\_hf_cache"
set "HF_HUB_CACHE=%HF_HOME%\hub"
set "HUGGINGFACE_HUB_CACHE=%HF_HUB_CACHE%"
set "HF_XET_CACHE=%HF_HOME%\xet"
set "HF_ASSETS_CACHE=%HF_HOME%\assets"
set "TORCH_HOME=%MODELS_DIR%\_torch_cache"
set "XDG_CACHE_HOME=%MODELS_DIR%\_cache"
set "PIP_NO_CACHE_DIR=1"
if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%"
set "PYTHON_EXE=python"
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"

:menu
cls
echo =============================================================
echo  SoundText - ONE launcher - models live in .\models
echo =============================================================
echo  1. Open SoundText Studio [choose Start in app; default: YOU + REMOTE]
echo  2. Open Studio [MIC selected; press Start to capture]
echo  3. Open Studio [REMOTE selected; press Start to capture]
echo  4. Check ALL required models and Python libraries [quick]
echo  5. List audio devices
echo  6. Test microphone [12 seconds]
echo  7. Recover an existing session
echo  8. Download missing ASR models to .\models
echo  9. Download CPU speaker models to .\models [MIC + REMOTE]
echo 10. Prepare NLLB ONNX model in .\models [required]
echo 11. Install Python dependencies [current environment]
echo 12. Run with manual audio channel settings
echo 13. Remove temporary Python caches [NOT models/sessions]
echo 14. Deep check: import packages / detect broken DLLs
echo 15. Check current ASR model loading [slow]
echo 16. Test a Japanese sentence with NLLB
echo 17. Run WITHOUT diarization [lowest latency]
echo 18. Run speaker detection WITHOUT early splitting [compare latency]
echo 19. Install CustomTkinter interface only
echo  0. Exit
echo.
set "ACTION="
set /p "ACTION=Choose an option: "
if "%ACTION%"=="1" goto run
if "%ACTION%"=="2" goto you
if "%ACTION%"=="3" goto remote
if "%ACTION%"=="4" goto check
if "%ACTION%"=="5" goto devices
if "%ACTION%"=="6" goto probe
if "%ACTION%"=="7" goto recover
if "%ACTION%"=="8" goto prepare_models
if "%ACTION%"=="9" goto prepare_speakers
if "%ACTION%"=="10" goto prepare_nllb
if "%ACTION%"=="11" goto install
if "%ACTION%"=="12" goto channel
if "%ACTION%"=="13" goto clean
if "%ACTION%"=="14" goto deep_check
if "%ACTION%"=="15" goto asr_check
if "%ACTION%"=="16" goto test_nllb
if "%ACTION%"=="17" goto no_speakers
if "%ACTION%"=="18" goto no_early_speaker_split
if "%ACTION%"=="19" goto install_ctk
if "%ACTION%"=="0" exit /b 0
echo Invalid selection.
pause
goto menu

:run
call :python main.py run --config config_stt_vi_ja.json
goto finished
:you
call :python main.py run --config config_stt_vi_ja.json --audio-mode you
goto finished
:remote
call :python main.py run --config config_stt_vi_ja.json --audio-mode remote
goto finished
:check
call :python scripts\diagnose.py
goto finished
:deep_check
call :python scripts\diagnose.py --imports
goto finished
:asr_check
call :python main.py check --config config_stt_vi_ja.json
goto finished
:devices
call :python main.py devices
goto finished
:probe
call :python main.py probe --config config_stt_vi_ja.json --probe-source mic --seconds 12
goto finished
:recover
set "SESSION_PATH="
set /p "SESSION_PATH=Path to session folder (e.g. output\meeting_YYYYMMDD_HHMMSS): "
if not defined SESSION_PATH goto menu
call :python main.py recover --config config_stt_vi_ja.json --session "%SESSION_PATH%"
goto finished
:prepare_models
call :python scripts\prepare_models.py
goto finished
:prepare_speakers
call :python scripts\prepare_speakers.py
goto finished
:prepare_nllb
call :python scripts\prepare_translation_onnx.py
goto finished
:install
call :python -m pip install --no-cache-dir -r requirements.txt
goto finished
:channel
echo.
echo Audio source: 1=YOU/microphone  2=REMOTE/system
set "CHANNEL_SOURCE="
set /p "CHANNEL_SOURCE=Choose source: "
echo Channel mode: 1=left  2=right  3=mean  4=loudest
echo NOTE: Normal speech modes from the config are recommended first.
set "CHANNEL_MODE="
set /p "CHANNEL_MODE=Choose channel: "
set "CHANNEL_VALUE="
if "%CHANNEL_MODE%"=="1" set "CHANNEL_VALUE=left"
if "%CHANNEL_MODE%"=="2" set "CHANNEL_VALUE=right"
if "%CHANNEL_MODE%"=="3" set "CHANNEL_VALUE=mean"
if "%CHANNEL_MODE%"=="4" set "CHANNEL_VALUE=loudest"
if not defined CHANNEL_VALUE goto menu
if "%CHANNEL_SOURCE%"=="1" call :python main.py run --config config_stt_vi_ja.json --audio-mode you --mic-channel-mode %CHANNEL_VALUE%
if "%CHANNEL_SOURCE%"=="2" call :python main.py run --config config_stt_vi_ja.json --audio-mode remote --system-channel-mode %CHANNEL_VALUE%
goto finished
:test_nllb
call :python scripts\test_translation.py --backend nllb_onnx
goto finished
:no_speakers
call :python main.py run --config config_stt_vi_ja.json --no-speakers
goto finished
:no_early_speaker_split
call :python main.py run --config config_stt_vi_ja.json --no-early-speaker-split
goto finished
:install_ctk
call :python -m pip install --no-cache-dir "customtkinter>=5.2,<6" "Pillow>=10,<13"
goto finished
:clean
echo Deleting Python caches ONLY; keeping .\models and .\output untouched.
call :python scripts\clean_temp.py
goto finished

:finished
echo.
echo [SoundText] Finished. Exit code: %errorlevel%
pause
goto menu

:python
"%PYTHON_EXE%" %*
exit /b %errorlevel%
