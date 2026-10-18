$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm 'WebLoginAssistant.spec'
if ($LASTEXITCODE -ne 0) { throw 'EXE 打包失败' }
Copy-Item -LiteralPath '使用说明.md' -Destination 'dist\WebLoginAssistant\使用说明.md' -Force
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination 'dist\WebLoginAssistant\THIRD_PARTY_NOTICES.md' -Force
Write-Output '已生成 dist\WebLoginAssistant\WebLoginAssistant.exe'
