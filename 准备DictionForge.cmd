@echo off
chcp 65001 >nul
echo 正在准备声句工坊的独立环境和模型资产，请保留此窗口。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" -Worker -Assets
if errorlevel 1 goto finish
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\doctor.ps1" -Assets
:finish
echo 完成后请查看上方结果，再双击“启动DictionForge.cmd”。
pause
