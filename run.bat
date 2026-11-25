@echo off
title Cisco Switch Manager
cd /d "%~dp0"

:: Tenta executar com o inicializador 'py' ou 'python'
py --version >nul 2>&1
if %errorlevel% equ 0 (
    py main.py
    goto end
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    python main.py
    goto end
)

echo [ERRO] Python nao foi encontrado no PATH do Windows.
echo Instale o Python 3.8+ em https://www.python.org e marque 'Add Python to PATH'.
pause

:end
