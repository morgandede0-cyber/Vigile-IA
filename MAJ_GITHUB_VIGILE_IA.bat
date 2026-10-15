@echo off
setlocal EnableExtensions DisableDelayedExpansion
title VIGILE IA - ENVOI GITHUB
cd /d "%~dp0"
set "REPO=https://github.com/morgandede0-cyber/Vigile-IA.git"
echo ==============================================
echo        VIGILE IA - MISE A JOUR GITHUB
echo ==============================================
echo Dossier : %CD%
echo.
where git >nul 2>&1
if errorlevel 1 (
 echo ERREUR : Git est absent. Installez Git for Windows.
 goto :error
)
if not exist "bot.py" (
 echo ERREUR : bot.py introuvable. Placez ce BAT a cote de bot.py.
 goto :error
)
if not exist "Dockerfile" (
 echo ERREUR : Dockerfile introuvable.
 goto :error
)
echo [1/6] Preparation du depot...
if not exist ".git" (
 echo Initialisation Git dans le dossier actuel...
 git init
 if errorlevel 1 goto :error
)
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :error
set "ORIGIN="
for /f "delims=" %%U in ('git remote get-url origin 2^>nul') do set "ORIGIN=%%U"
if not defined ORIGIN (
 git remote add origin "%REPO%"
 if errorlevel 1 goto :error
) else (
 if /i not "%ORIGIN%"=="%REPO%" if /i not "%ORIGIN%"=="https://github.com/morgandede0-cyber/Vigile-IA" if /i not "%ORIGIN%"=="git@github.com:morgandede0-cyber/Vigile-IA.git" (
  echo ERREUR : origin pointe vers un autre depot : %ORIGIN%
  goto :error
 )
)
echo [2/6] Protection des fichiers prives...
rem Les exclusions sont aussi ajoutees au fichier .git/info/exclude local.
if not exist ".git\info" mkdir ".git\info"
(
 echo .env
 echo .env.*
 echo !.env.example
 echo data/
 echo *.sqlite
 echo *.sqlite3
 echo *.db
 echo *.log
 echo *.pem
 echo *.key
 echo *.zip
 echo __pycache__/
 echo .venv/
 echo venv/
 echo .pytest_cache/
) >> ".git\info\exclude"
rem Ne jamais publier des fichiers sensibles deja suivis.
for /f "delims=" %%F in ('git ls-files') do call :check_file "%%F"
if defined SENSITIVE (
 echo ERREUR : un fichier sensible est deja suivi par Git.
 echo Retirez-le du suivi avec git rm --cached avant de continuer.
 goto :error
)
echo [3/6] Ajout des fichiers...
git add -A -- . ":(exclude,glob)**/.env" ":(exclude,glob)**/.env.*" ":(exclude,glob)**/data/**" ":(exclude,glob)**/*.sqlite*" ":(exclude,glob)**/*.db" ":(exclude,glob)**/*.log" ":(exclude,glob)**/*.pem" ":(exclude,glob)**/*.key" ":(exclude,glob)**/*.zip" ":(exclude,glob)**/__pycache__/**" ":(exclude,glob)**/.venv/**" ":(exclude,glob)**/venv/**"
if errorlevel 1 goto :error
if exist ".env.example" git add -f -- ".env.example"
git diff --cached --name-only
echo.
echo [4/6] Commit local...
git diff --cached --quiet
if errorlevel 1 (
 git -c core.autocrlf=false commit -m "Mise a jour Vigile IA"
 if errorlevel 1 (
  echo Configurez votre identite Git si necessaire :
  echo git config --global user.name "Votre nom"
  echo git config --global user.email "votre-email"
  goto :error
 )
) else (
 echo Aucun nouveau fichier a enregistrer.
)
echo [5/6] Synchronisation avec GitHub...
git fetch origin main
if errorlevel 1 goto :error
rem Si le depot distant contient deja des commits, fusionner sans ecraser.
git show-ref --verify --quiet refs/remotes/origin/main
if errorlevel 1 goto :no_remote_main
git merge-base HEAD origin/main >nul 2>&1
if errorlevel 1 (
 echo Historiques differents : fusion initiale securisee...
 git -c core.editor=true merge --no-edit --allow-unrelated-histories origin/main
) else (
 git -c core.editor=true merge --no-edit origin/main
)
if errorlevel 1 (
 echo CONFLIT : aucun push effectue. Corrigez les conflits Git.
 goto :error
)
:no_remote_main
git branch -M main
if errorlevel 1 goto :error
echo [6/6] Envoi vers GitHub...
git push -u origin main
if errorlevel 1 goto :error
echo.
echo ==============================================
echo SUCCES : VIGILE IA MIS A JOUR SUR GITHUB
 echo ==============================================
pause
exit /b 0
:check_file
set "FILE=%~1"
for %%X in ("%FILE%") do set "NAME=%%~nxX"
if /i "%NAME%"==".env.example" exit /b 0
if /i "%NAME%"==".env" set "SENSITIVE=1"
if /i "%NAME:~0,5%"==".env." set "SENSITIVE=1"
if /i "%NAME:~-3%"==".db" set "SENSITIVE=1"
if /i "%NAME:~-4%"==".key" set "SENSITIVE=1"
if /i "%NAME:~-4%"==".pem" set "SENSITIVE=1"
if /i "%NAME:~-7%"==".sqlite" set "SENSITIVE=1"
if /i "%NAME:~-8%"==".sqlite3" set "SENSITIVE=1"
if /i "%FILE:~0,5%"=="data/" set "SENSITIVE=1"
exit /b 0
:error
echo.
echo ECHEC : aucun push force n'a ete utilise.
echo Lisez le message ci-dessus avant de relancer.
pause
exit /b 1
