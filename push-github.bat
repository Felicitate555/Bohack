@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Syncing Bohack to GitHub...
echo Starting GitHub sync.>push-github.log
where git >nul 2>nul || (echo Git was not found. Install Git and try again. & goto :fail)
where python >nul 2>nul || (echo Python was not found. It is needed for the local key check. & goto :fail)
for /f "delims=" %%R in ('git remote get-url origin 2^>nul') do set "BOHACK_REMOTE=%%R"
if /i not "%BOHACK_REMOTE%"=="https://github.com/Felicitate555/Bohack.git" (echo Unexpected origin remote. Nothing was pushed. & goto :fail)
echo Fetching the current main branch...
git fetch origin main >>push-github.log 2>&1
if errorlevel 1 (echo Could not fetch GitHub. Check your connection and GitHub login. & goto :fail)
git rev-parse --verify HEAD >nul 2>nul
if errorlevel 1 goto :initialize
git merge-base --is-ancestor FETCH_HEAD HEAD
if errorlevel 1 (echo The remote contains changes not in this local branch. Nothing was pushed. & goto :fail)
goto :stage
:initialize
rem The branch has no commit. Resetting the index preserves all working files.
git reset --mixed FETCH_HEAD >>push-github.log 2>&1
if errorlevel 1 (echo Could not attach the existing remote history. & goto :fail)
:stage
echo Checking project files...
git add -A >>push-github.log 2>&1
if errorlevel 1 (echo Could not stage project files. & goto :fail)
python scripts\check_staged_secrets.py >>push-github.log 2>&1
if errorlevel 1 goto :fail
git diff --cached --quiet
if not errorlevel 1 goto :push
echo Creating the project commit...
git -c user.name=Felicitate555 -c user.email=249768894+Felicitate555@users.noreply.github.com commit -m "Add VibeLab project" >>push-github.log 2>&1
if errorlevel 1 (echo Commit failed. Details are in push-github.log. & goto :fail)
:push
echo Pushing to GitHub. Sign in if prompted...
git push origin HEAD:refs/heads/main >>push-github.log 2>&1
if errorlevel 1 (echo Push failed. Check your GitHub login and remote changes. & goto :fail)
echo Success: project pushed to https://github.com/Felicitate555/Bohack
echo Success: project pushed.>>push-github.log
echo The log is saved as push-github.log.
pause >nul
exit /b 0
:fail
echo Sync stopped. Diagnostic log:
type push-github.log
echo.
echo The log is saved as push-github.log. Press any key to close.
pause >nul
exit /b 1
