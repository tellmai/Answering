@echo off
cd /d "%~dp0"
if exist "dist\WebLoginAssistant\WebLoginAssistant.exe" (
    start "" "dist\WebLoginAssistant\WebLoginAssistant.exe"
) else (
    if exist ".venv\Scripts\pythonw.exe" (
        start "" ".venv\Scripts\pythonw.exe" "main.py"
    ) else (
        echo 请先安装源码运行环境，或使用已打包的软件文件夹。
        pause
    )
)
