@echo off
title Claude Maestro - Servidor (mantenha aberto)
cd /d "%~dp0"
echo Preparando o Claude Maestro...
python -m pip install -q -r requirements.txt
python -m maestro serve
pause
