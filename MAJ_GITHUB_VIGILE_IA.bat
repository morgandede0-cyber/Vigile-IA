@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title VIGILE IA - GITHUB V5
set "REPO=https://github.com/morgandede0-cyber/Vigile-IA.git"
echo ===============================================
echo          VIGILE IA - GITHUB V5
echo ===============================================
echo Dossier : %CD%
echo.
where git >nul 2>&1 || (echo ERREUR : Git non installe.& goto :fail)
if not exist "bot.py" (echo ERREUR : bot.py absent. Placez ce script dans le dossier du projet.& goto :fail)
if not exist ".git" (echo [1/7] Initialisation Git...& git init || goto :fail)
for /f "delims=" %%U in ('git remote get-url origin 2^>nul') do set "ORIGIN=%%U"
if not defined ORIGIN (
  git remote add origin "%REPO%" || goto :fail
) else (
  if /i not "%ORIGIN%"=="%REPO%" (
    echo Remote actuel : %ORIGIN%
    echo Remote attendu : %REPO%
    choice /C ON /M "Corriger origin vers le depot attendu"
    if errorlevel 2 goto :fail
    git remote set-url origin "%REPO%" || goto :fail
  )
)
echo [2/7] Protection des fichiers sensibles...
if not exist ".gitignore" (
  >".gitignore" echo .env
  >>".gitignore" echo .env.*
  >>".gitignore" echo !.env.example
  >>".gitignore" echo data/
  >>".gitignore" echo *.sqlite3
  >>".gitignore" echo *.db
  >>".gitignore" echo __pycache__/
  >>".gitignore" echo *.pyc
)
for %%F in (.env .env.local) do (
  git ls-files --error-unmatch "%%F" >nul 2>&1 && (echo ERREUR : %%F est deja suivi par Git. Retirez-le de l'index avant l'envoi.& goto :fail)
)
echo [3/7] Recuperation de GitHub...
git fetch origin || goto :fail
echo [4/7] Verification de la branche...
git branch --show-current | findstr /x /c:"main" >nul
if errorlevel 1 (
  git branch -M main || goto :fail
)
echo [5/7] Sauvegarde locale...
git add -A || goto :fail
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Mise a jour Vigile IA" || goto :fail
) else (
  echo Aucun changement local a enregistrer.
)
echo [6/7] Synchronisation des historiques...
git show-ref --verify --quiet refs/remotes/origin/main
if not errorlevel 1 (
  git merge-base HEAD origin/main >nul 2>&1
  if errorlevel 1 (
    echo Les historiques sont independants. Fusion sans ecrasement force.
    git merge --no-edit --allow-unrelated-histories origin/main
  ) else (
    git merge --no-edit origin/main
  )
  if errorlevel 1 (
    echo.
    echo CONFLIT OU ERREUR DE FUSION.
    echo Aucun push effectue. Vos fichiers locaux restent presents.
    echo Lancez git status pour identifier les conflits.
    echo Apres resolution : git add -A puis git commit, et relancez ce script.
    goto :fail
  )
)
echo [7/7] Envoi vers GitHub...
git push -u origin main || goto :fail
echo.
echo SUCCES : projet synchronise sur GitHub.
pause
exit /b 0
:fail
echo.
echo ECHEC : aucun push force n'a ete effectue.
pause
exit /b 1
