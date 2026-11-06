# ============================================================
# ADGS · web3 最简启动脚本
#
# 一条命令得到可用的 web3 观测台：
#   [1/3] 若 dist 未构建 → 构建静态产物
#   [2/3] 若后端 8021 未运行 → 拉起 Flask 后端（后台常驻）
#   [3/3] 打开浏览器 http://127.0.0.1:8021/web3/
#
# 用法（任选其一）：
#   · 双击本文件（若已关联 PowerShell）
#   · 终端执行：
#       powershell -ExecutionPolicy Bypass -File D:\user_illusion\test\ADGS\start-web3.ps1
# ============================================================
$ErrorActionPreference = "Stop"

$ADGS = "D:\user_illusion\test\ADGS"
$WEB3 = "$ADGS\web3"
$URL  = "http://127.0.0.1:8021/web3/"

# ---- [1/3] 构建静态产物（已构建则跳过） ---------------------
if (-not (Test-Path "$WEB3\dist\index.html")) {
    Write-Host "[1/3] 构建 web3 …" -ForegroundColor Cyan
    $env:PATH = "C:\Program Files\nodejs;" + $env:PATH   # 仅本进程生效
    Push-Location $WEB3
    & cmd /c "set npm_config_cache=$WEB3\.npm-cache && npm run build"
    Pop-Location
    if (-not (Test-Path "$WEB3\dist\index.html")) {
        throw "构建失败：$WEB3\dist\index.html 不存在"
    }
} else {
    Write-Host "[1/3] dist 已存在，跳过构建" -ForegroundColor DarkGray
}

# ---- [2/3] 启动后端（8021 已在监听则跳过） ------------------
if (Get-NetTCPConnection -LocalPort 8021 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "[2/3] 后端 8021 已在运行" -ForegroundColor DarkGray
} else {
    Write-Host "[2/3] 启动后端 8021 …" -ForegroundColor Cyan
    Start-Process -FilePath "$ADGS\.venv\Scripts\python.exe" `
                  -ArgumentList "-u", "web_app.py" `
                  -WorkingDirectory $ADGS -WindowStyle Hidden
    # 最多等 30s 端口就绪
    for ($i = 0; $i -lt 30 -and
         -not (Get-NetTCPConnection -LocalPort 8021 -State Listen -ErrorAction SilentlyContinue); $i++) {
        Start-Sleep -Seconds 1
    }
    if (-not (Get-NetTCPConnection -LocalPort 8021 -State Listen -ErrorAction SilentlyContinue)) {
        throw "后端 30s 内未就绪，请手动检查：cd $ADGS ; .\.venv\Scripts\python.exe -u web_app.py"
    }
}

# ---- [3/3] 打开浏览器 --------------------------------------
Write-Host "[3/3] 打开 $URL" -ForegroundColor Cyan
Start-Process $URL
Write-Host "完成，访问地址：$URL" -ForegroundColor Green
