@echo off
REM ===========================================================================
REM taifex_vix daily update - intended for Windows Task Scheduler (15:15 Mon-Fri)
REM   1. cli daily --wait 60 : fetch + compute VIX, update 0050 close
REM   2. build_dashboard     : regenerate docs\index.html from the CSV
REM
REM ASCII ONLY - DO NOT put Chinese (or any non-ASCII) text in this file.
REM cmd.exe reads .bat using the OEM code page (Big5 on zh-TW); UTF-8 bytes get
REM mis-decoded and corrupt command parsing - the `cd` line silently breaks and
REM the whole script does nothing while still exiting 0. Do NOT add `chcp`
REM either: switching code page mid-file makes cmd re-read the remaining bytes
REM at a misaligned offset. Explanations belong in README.md, not here.
REM
REM Optional environment variables:
REM   TAIFEX_VIX_PYTHON  full path to python.exe  (default: python on PATH)
REM   TAIFEX_VIX_DATA    where data is written    (default: D:\taifex_vix)
REM ===========================================================================

REM Repo root = parent of this script's folder, so the checkout can live
REM anywhere. %~dp0 already ends with a backslash.
cd /d "%~dp0.."

REM Make Python write UTF-8 to the log (its messages are Chinese).
set PYTHONIOENCODING=utf-8

if "%TAIFEX_VIX_PYTHON%"=="" (set PY=python) else (set PY=%TAIFEX_VIX_PYTHON%)
if "%TAIFEX_VIX_DATA%"=="" (set LOGDIR=D:\taifex_vix) else (set LOGDIR=%TAIFEX_VIX_DATA%)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\daily.log

echo.>> "%LOG%"
echo ===== %date% %time% =====>> "%LOG%"

REM -u = unbuffered; without it the --wait progress only lands in the log
REM when the process exits, so a 60-minute wait shows nothing meanwhile.
"%PY%" -u -m taifex_vix.cli daily --wait 60 >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

REM Only rebuild the dashboard if the data step succeeded, so a failed run
REM never overwrites a good chart with a half-updated one.
if "%RC%"=="0" (
  "%PY%" -u -m taifex_vix.build_dashboard docs\index.html >> "%LOG%" 2>&1
) else (
  echo [skip] daily failed rc=%RC%, dashboard not rebuilt>> "%LOG%"
)

exit /b %RC%
