# 第二大脑 阶段一 一键启动脚本（Windows）
# 用法：PowerShell 中执行  .\scripts\start_dev.ps1
# 前置：MySQL(127.0.0.1:3306 root/root) 已启动

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# 目录层面保障（后端启动也会自动创建）
"raw","kb","input" | ForEach-Object { $p = Join-Path "data" $_; if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null } }

Write-Host "[1/2] 启动后端 (FastAPI, port 8000) ..."
Write-Host "     打开 http://localhost:8000/docs 可查看接口文档"
Push-Location backend
Start-Process -FilePath python -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" -WindowStyle Normal
Pop-Location

Write-Host "[2/2] 启动前端 (Vite, dev 5173) ..."
Write-Host "     打开 http://localhost:5173 查看导入区视图"
Push-Location frontend
if (-not (Test-Path "node_modules")) { npm install }
npm run dev
Pop-Location