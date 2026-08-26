param(
  [ValidateSet("lite", "full")]
  [string]$Mode = "lite",
  [string]$OutputDir = "",
  [switch]$SkipRuntimeVerify
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildRoot = Join-Path $ProjectRoot ".portable_build"
$StagingRoot = Join-Path $BuildRoot "staging"
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $ProjectRoot ".deploy_build"
}

function Write-Step {
  param([string]$Message)
  Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -ForegroundColor Cyan
}

function Get-GitShortHead {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    return "nogit"
  }
  Push-Location $ProjectRoot
  try {
    $inside = (& git rev-parse --is-inside-work-tree 2>$null)
    if ($LASTEXITCODE -ne 0 -or "$inside".Trim() -ne "true") {
      return "nogit"
    }
    return (& git rev-parse --short HEAD).Trim()
  } finally {
    Pop-Location
  }
}

function Test-FullRuntimeReady {
  $python = Join-Path $ProjectRoot "runtime\python\python.exe"
  $redis = Join-Path $ProjectRoot "runtime\redis\redis-server.exe"
  $browserRoot = Join-Path $ProjectRoot "runtime\ms-playwright"
  $manifest = Join-Path $ProjectRoot "runtime\manifest.json"
  if (-not (Test-Path $python) -or -not (Test-Path $redis) -or -not (Test-Path $manifest)) {
    return $false
  }
  $browser = Get-ChildItem -LiteralPath $browserRoot -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue | Select-Object -First 1
  return [bool]$browser
}

function Invoke-RobocopyMirror {
  param(
    [string]$Source,
    [string]$Destination,
    [string[]]$ExcludeDirs = @(),
    [string[]]$ExcludeFiles = @()
  )

  if (-not (Test-Path $Source)) {
    throw "Source path does not exist: $Source"
  }
  New-Item -ItemType Directory -Force $Destination | Out-Null

  $xd = @()
  foreach ($dir in $ExcludeDirs) {
    if (-not [string]::IsNullOrWhiteSpace($dir)) { $xd += $dir }
  }
  $xf = @()
  foreach ($file in $ExcludeFiles) {
    if (-not [string]::IsNullOrWhiteSpace($file)) { $xf += $file }
  }

  $args = @($Source, $Destination, "/MIR", "/MT:8", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
  if ($xd.Count -gt 0) { $args += "/XD"; $args += $xd }
  if ($xf.Count -gt 0) { $args += "/XF"; $args += $xf }

  & robocopy @args | Out-Null
  if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE while copying $Source"
  }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$shortHead = Get-GitShortHead
$packageName = "aisec-agent-portable-$Mode-$timestamp-$shortHead"
$stagingDir = Join-Path $StagingRoot $packageName

Write-Host ""
Write-Host "aisec-agent portable package builder" -ForegroundColor Green
Write-Host "Mode: $Mode  Commit: $shortHead" -ForegroundColor Green
Write-Host ""

if ($Mode -eq "full" -and -not $SkipRuntimeVerify) {
  if (-not (Test-FullRuntimeReady)) {
    throw @"
Full/offline package requires a ready local runtime.
Run this on a machine that has already started successfully once:
  .\start.bat
Or force-install runtime:
  powershell -File .\scripts\install-runtime.ps1
Then rebuild with -Mode full.
"@
  }
}

Write-Step "Cleaning staging directory"
if (Test-Path $stagingDir) {
  Remove-Item -LiteralPath $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Force $stagingDir | Out-Null
New-Item -ItemType Directory -Force $OutputDir | Out-Null

$excludeDirs = @(
  ".git",
  ".venv",
  "runtime",
  ".deploy_build",
  ".portable_build",
  "__pycache__",
  ".pytest_cache",
  ".cursor",
  ".idea",
  ".vscode",
  ".dbg",
  "playwright_profiles",
  "douyin_dm_artifacts",
  "export",
  "index",
  "vector_store",
  "vectorstore",
  "embedding"
)

$excludeFiles = @(
  ".env",
  ".env.local",
  "*.log",
  "*.pyc",
  "*.pyo",
  "Thumbs.db",
  ".DS_Store"
)

Write-Step "Copying project files"
Invoke-RobocopyMirror -Source $ProjectRoot -Destination $stagingDir -ExcludeDirs $excludeDirs -ExcludeFiles $excludeFiles

if ($Mode -eq "full") {
  Write-Step "Copying portable runtime (python / redis / chromium)"
  $runtimeTargets = @("python", "redis", "ms-playwright")
  foreach ($name in $runtimeTargets) {
    Invoke-RobocopyMirror `
      -Source (Join-Path $ProjectRoot "runtime\$name") `
      -Destination (Join-Path $stagingDir "runtime\$name")
  }
  Copy-Item -LiteralPath (Join-Path $ProjectRoot "runtime\manifest.json") -Destination (Join-Path $stagingDir "runtime\manifest.json") -Force
  New-Item -ItemType Directory -Force (Join-Path $stagingDir "runtime\logs") | Out-Null
  New-Item -ItemType Directory -Force (Join-Path $stagingDir "runtime\state") | Out-Null
  New-Item -ItemType Directory -Force (Join-Path $stagingDir "runtime\data\redis") | Out-Null
}

$packageInfo = [ordered]@{
  name = "aisec-agent-portable"
  mode = $Mode
  built_at = (Get-Date).ToString("o")
  git_commit = $shortHead
  startup = "Double-click start.bat"
  web_url = "http://127.0.0.1:7860"
  health_url = "http://127.0.0.1:7860/api/health"
  notes = if ($Mode -eq "full") {
    "Offline-ready package with bundled runtime. First start should not need internet."
  } else {
    "Lite package. First start downloads Python/Redis/Chromium and may take several minutes."
  }
}
$packageInfo | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stagingDir "PACKAGE_INFO.json") -Encoding UTF8

$readmeTemplate = Join-Path $ProjectRoot "deploy\PORTABLE_部署说明.txt"
if (Test-Path $readmeTemplate) {
  Copy-Item -LiteralPath $readmeTemplate -Destination (Join-Path $stagingDir "部署说明.txt") -Force
} else {
  @"
aisec-agent 离线部署包
====================

1. 解压整个文件夹到任意目录（建议 D:\aisec-agent，路径尽量不含空格）
2. 双击 start.bat 启动 Web + worker
3. 浏览器访问 http://127.0.0.1:7860
4. 首次启动会自动从 .env.example 生成本机 .env，请填入模型/API 等配置

停止服务：双击 stop.bat
"@ | Set-Content -LiteralPath (Join-Path $stagingDir "部署说明.txt") -Encoding UTF8
}

if ($Mode -eq "full") {
  @"

【完整离线包】
本包已内置 Python / Redis / Playwright Chromium，解压后可直接 start.bat 启动。
若启动失败，请检查 Windows 防火墙是否允许 7860 端口。
"@ | Add-Content -LiteralPath (Join-Path $stagingDir "部署说明.txt") -Encoding UTF8
} else {
  @"

【轻量包】
首次 start.bat 需要联网下载运行时（约 5-10 分钟），请确保能访问 python.org / pypi / github。
下载完成后再次启动会快很多。
"@ | Add-Content -LiteralPath (Join-Path $stagingDir "部署说明.txt") -Encoding UTF8
}

Write-Step "Creating archive"
$archiveBase = Join-Path $OutputDir "$packageName"
$zipPath = "$archiveBase.zip"
$shaPath = "$archiveBase.sha256"
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

Push-Location $StagingRoot
try {
  & tar.exe -acf $zipPath $packageName
  if ($LASTEXITCODE -ne 0) {
    throw "tar failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}

$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
Set-Content -LiteralPath $shaPath -Value "$hash  $(Split-Path $zipPath -Leaf)" -Encoding ASCII

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "Package created successfully." -ForegroundColor Green
Write-Host "  Mode : $Mode"
Write-Host "  File : $zipPath"
Write-Host "  Size : ${sizeMb} MB"
Write-Host "  SHA256: $hash"
Write-Host ""
Write-Host "Send the zip to another Windows x64 PC, unzip, then double-click start.bat." -ForegroundColor Yellow
