@echo off
cd /d "%~dp0"
where docker >nul 2>nul || (echo Docker was not found. Install and start Docker Desktop first. & pause & exit /b 1)
docker compose up -d --build
if errorlevel 1 (echo Start failed. Make sure Docker Desktop is running. & pause & exit /b 1)
start http://localhost:3000
echo VibeLab is starting at http://localhost:3000
pause
