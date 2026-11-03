@echo off
rem Double-click to build the site from data\ and open it in your browser.
rem Leave this window open while you use the site; close it (or press Ctrl+C) to stop.
cd /d "%~dp0"
where uv >nul 2>nul || (
  echo uv is not installed. Install it from https://docs.astral.sh/uv/ and try again.
  pause
  exit /b 1
)
uv run who-is-running serve %*
if errorlevel 1 pause
