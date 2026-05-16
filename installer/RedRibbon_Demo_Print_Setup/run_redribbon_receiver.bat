@echo off
cd /d C:\RedRibbonDemo
python "%~dp0print_receiver\receiver_engine.py" --config "%~dp0print_receiver\config.json"
