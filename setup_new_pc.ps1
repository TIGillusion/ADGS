<#
ADGS 一键环境安装脚本 (Windows)
================================
用途: 在新电脑上把 ADGS 达尔文进化系统跑起来所需的全部环境装好
  1. 检查/安装 Python 3.13（无则自动 winget 安装）
  2. 安装 Hermes Agent CLI（官方指令: irm https://res1.hermesagent.org.cn/install.ps1 | iex）
  3. 确保 python 命令在用户 PATH（hermes 节点跑 anysearch 脚本必需）
  4. 创建虚拟环境 .venv 并安装 requirements.txt 全部依赖
     （无 NVIDIA 显卡时自动装 CPU 版 PyTorch，省 ~2GB 下载）
  5. 预下载 embedding 模型 bge-small-zh-v1.5 到 ./models（走 hf-mirror 国内镜像）
  6. 校验 hermes 命令 + 三个模型 API 端点连通性

用法:
  powershell -ExecutionPolicy Bypass -File setup_new_pc.ps1
  或直接双击同目录 setup_new_pc.bat

参数:
  -SkipHermes     跳过 Hermes 安装（已装过时用）
  -SkipEmbedding  跳过 embedding 模型预下载
  -WithGPU        强制安装默认(CUDA)版 PyTorch（默认装 CPU 版：ADGS 的
                  torch 只做 embedding 推理，CPU 足够，省 ~2GB 下载）
  -NoTuna         不用清华 PyPI 镜像（国外机器用）
#>
param(
    [switch]$SkipHermes,
    [switch]$SkipEmbedding,
    [switch]$WithGPU,
    [switch]$NoTuna
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$venvDir     = Join-Path $ProjectRoot '.venv'
$venvPy      = Join-Path $venvDir 'Scripts\python.exe'
$reqFile     = Join-Path $ProjectRoot 'requirements.txt'

# ============================================================
# 工具函数
# ============================================================
function Refresh-PathEnv {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u"
}

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Fail($msg) {
    Write-Host "    [FAIL] $msg" -ForegroundColor Red
}

function Test-Cmd([string]$name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Find-Python {
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    # 常见安装路径兜底（winget / python.org 默认位置）
    $cands = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313-64\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python313\python.exe"
    )
    foreach ($p in $cands) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ============================================================
# 0. 前置检查
# ============================================================
Write-Host "======================================================" -ForegroundColor DarkGray
Write-Host "  ADGS 环境安装程序" -ForegroundColor White
Write-Host "  项目目录: $ProjectRoot" -ForegroundColor DarkGray
Write-Host "======================================================" -ForegroundColor DarkGray
Refresh-PathEnv

if (-not (Test-Path $reqFile)) {
    throw "找不到 requirements.txt，请确认脚本放在 ADGS 项目根目录下运行"
}

# ============================================================
# 1. Python 3.13
# ============================================================
Write-Step "1/6 检查 Python"
$py = Find-Python
if (-not $py) {
    if (Test-Cmd winget) {
        Write-Host "    未检测到 Python，正在用 winget 安装 Python 3.13 ..." -ForegroundColor Yellow
        winget install --id Python.Python.3.13 -e --scope user `
            --accept-package-agreements --accept-source-agreements
        Refresh-PathEnv
        $py = Find-Python
    }
}
if (-not $py) {
    throw "未检测到 Python，且无法自动安装。请手动安装 Python 3.13（勾选 Add to PATH）后重跑: https://www.python.org/downloads/"
}
$pyVer = & $py --version 2>&1
Write-Ok "Python: $pyVer @ $py"

# 确保 python 在用户 PATH（hermes 节点执行 anysearch 脚本需要 `python` 命令可用）
$pyDir = Split-Path $py
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$pyDir*") {
    Write-Host "    把 Python 目录加入用户 PATH（hermes 节点调用 python 需要）..." -ForegroundColor Yellow
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$pyDir", 'User')
    $env:Path = "$env:Path;$pyDir"
    Write-Ok "已加入用户 PATH: $pyDir"
}

# ============================================================
# 2. Hermes Agent CLI
# ============================================================
Write-Step "2/6 检查 Hermes Agent CLI"
if ($SkipHermes) {
    Write-Host "    已通过 -SkipHermes 跳过" -ForegroundColor DarkGray
} elseif (Test-Cmd hermes) {
    $hver = hermes --version 2>&1 | Select-Object -First 1
    Write-Ok "hermes 已安装: $hver"
} else {
    Write-Host "    未检测到 hermes，正在执行官方一键安装: irm https://res1.hermesagent.org.cn/install.ps1 | iex" -ForegroundColor Yellow
    try {
        Invoke-Expression (Invoke-RestMethod 'https://res1.hermesagent.org.cn/install.ps1')
    } catch {
        throw "Hermes 安装命令执行失败: $_"
    }
    Refresh-PathEnv
    if (-not (Test-Cmd hermes)) {
        # 兜底：官方安装器常见落盘位置
        $alt = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
        if (Test-Path $alt) {
            Write-Fail "hermes 已装到 $alt 但不在 PATH"
            Write-Host "    请手动执行: [Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';$((Split-Path $alt))', 'User')" -ForegroundColor Yellow
            throw "Hermes 安装完成但 PATH 未生效，按上方提示添加后重跑本脚本（已装步骤会自动跳过）"
        }
        throw "hermes 安装失败或不在 PATH。请重启终端后重跑本脚本（已装步骤会自动跳过）"
    }
    $hver = hermes --version 2>&1 | Select-Object -First 1
    Write-Ok "hermes 安装完成: $hver"
}

# ============================================================
# 3. 创建虚拟环境
# ============================================================
Write-Step "3/6 创建虚拟环境 .venv"
if (Test-Path $venvPy) {
    Write-Ok ".venv 已存在，跳过创建"
} else {
    & $py -m venv $venvDir
    if (-not (Test-Path $venvPy)) { throw ".venv 创建失败" }
    Write-Ok ".venv 创建完成"
}

# ============================================================
# 4. 安装依赖
# ============================================================
Write-Step "4/6 安装 Python 依赖（首次约 5-15 分钟，视网速）"
& $venvPy -m pip install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip 升级失败" }

# pip 镜像源：默认清华（国内快）；-NoTuna 用官方源
$pipSrc = @()
if (-not $NoTuna) { $pipSrc = @('-i', 'https://pypi.tuna.tsinghua.edu.cn/simple') }

# torch 策略：默认 CPU 版（ADGS 的 torch 只服务 embedding，CPU 足够且下载小）；
# -WithGPU 时装默认(CUDA)版
if ($WithGPU) {
    Write-Host "    指定 -WithGPU，安装默认(CUDA 版) PyTorch（下载量 ~2.5GB）" -ForegroundColor DarkGray
} else {
    Write-Host "    安装 CPU 版 PyTorch（默认；ADGS embedding 推理 CPU 足够，省 ~2GB 下载）" -ForegroundColor DarkGray
    & $venvPy -m pip install torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "CPU 版 torch 安装失败，改回默认源安装（体积较大）"
        & $venvPy -m pip install torch @pipSrc 2>&1 | Select-Object -Last 3
        if ($LASTEXITCODE -ne 0) { throw "torch 安装失败" }
    }
}

Write-Host "    安装 requirements.txt 依赖 ..." -ForegroundColor DarkGray
& $venvPy -m pip install @pipSrc -r $reqFile 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { throw "requirements.txt 依赖安装失败" }
$importOk = & $venvPy -c "import openai, yaml, numpy, sentence_transformers; print('imports OK')" 2>&1
if ($LASTEXITCODE -ne 0) { throw "依赖导入校验失败: $importOk" }
Write-Ok "依赖安装完成: $importOk"

# ============================================================
# 5. 预下载 embedding 模型
# ============================================================
Write-Step "5/6 检查 embedding 模型 (bge-small-zh-v1.5, ~92MB)"
if ($SkipEmbedding) {
    Write-Host "    已通过 -SkipEmbedding 跳过" -ForegroundColor DarkGray
} else {
    Push-Location $ProjectRoot
    # 踩坑：PS 5.1 在 ErrorActionPreference=Stop 下会把 native stderr(如 tqdm 进度条)
    # 提升为终止错误，赋值语句形式必炸 → 用管道 + 局部 Continue 规避
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $modelDir = Join-Path $ProjectRoot 'models\models--BAAI--bge-small-zh-v1.5'
        if (Test-Path $modelDir) {
            # 缓存已存在 → 离线验证加载（huggingface_hub 联网 HEAD 检查在国内会被墙卡死）
            Write-Host "    检测到 models/ 模型缓存，离线验证加载 ..." -ForegroundColor DarkGray
            $env:HF_HUB_OFFLINE = '1'
            Remove-Item Env:HF_ENDPOINT -ErrorAction SilentlyContinue
            $out = (& $venvPy -c "from utils.embedding import get_embedding; v = get_embedding('环境自检'); print('embedding OK, dim =', len(v))" 2>&1 | Out-String)
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "embedding 验证通过 (dim=512, 缓存于 ./models)"
            } else {
                Write-Fail "模型加载失败: $($out.Trim())"
            }
        } else {
            # 无缓存 → 异常（分发包应内置 models/，多半被误删）。
            # 联网尝试补下载（走 hf-mirror 镜像），失败则提示拷贝源电脑的 models/
            Write-Host "    未检测到模型缓存（分发包应内置 models/，可能被误删）" -ForegroundColor Yellow
            Write-Host "    首次运行将尝试联网下载；若失败，从源电脑拷贝 models/ 目录过来即可（离线可用）" -ForegroundColor Yellow
        }
    } finally {
        Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
        Remove-Item Env:HF_ENDPOINT -ErrorAction SilentlyContinue
        $ErrorActionPreference = $prevEAP
        Pop-Location
    }
}

# ============================================================
# 6. 校验 API 连通性
# ============================================================
Write-Step "6/6 校验模型 API 端点连通性"
$cfgPath = Join-Path $ProjectRoot 'config\AI_config.json'
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    } catch {
        $cfg = $null
    }
    if ($cfg) {
        foreach ($g in @('ceo', 'brain', 'worker')) {
            $u = "$($cfg.$g.url)"
            if ($u -match '^https?://([^:/]+):?(\d+)?') {
                $hostname = $matches[1]
                $port = if ($matches[2]) { [int]$matches[2] } else { if ($u -like 'https*') { 443 } else { 80 } }
                $t = New-Object System.Net.Sockets.TcpClient
                try {
                    $t.Connect($hostname, $port) | Out-Null
                    Write-Ok "   $g : $hostname : $port 连通"
                } catch {
                    Write-Fail "   $g : $hostname : $port 无法连接 ($_)"
                } finally {
                    $t.Close()
                }
            }
        }
    } else {
        Write-Fail "config\AI_config.json 读取失败，跳过连通性校验"
    }
} else {
    Write-Fail "config\AI_config.json 不存在！模型地址/key 全在这个文件里，请从源电脑拷贝"
}

# ============================================================
# 完成
# ============================================================
Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  运行方式:"
Write-Host "    cd $ProjectRoot"
Write-Host "    .venv\Scripts\python main.py"
Write-Host ""
Write-Host "  首次运行会初始化 10 个节点（若 data\registry.json 为空）。"
Write-Host "  注意: config\AI_config.json 里是模型 API key，请勿外传。"
Write-Host "  如从其他电脑带种群过来，需同时拷贝:"
Write-Host "    - data\registry.json + data\archive\  (种群状态)"
Write-Host "    - %LOCALAPPDATA%\hermes\profiles\ags_*  (节点技能/记忆)"
Write-Host "======================================================" -ForegroundColor Green
