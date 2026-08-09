param(
  [string]$PythonVersion = "3.12.10",
  [string]$RedisVersion = "5.0.14.1",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot "runtime"
$PythonRoot = Join-Path $RuntimeRoot "python"
$RedisRoot = Join-Path $RuntimeRoot "redis"
$BrowserRoot = Join-Path $RuntimeRoot "ms-playwright"
$CacheRoot = Join-Path $RuntimeRoot "cache"
$DownloadRoot = Join-Path $CacheRoot "downloads"
$ManifestPath = Join-Path $RuntimeRoot "manifest.json"
$RequirementsPath = Join-Path $ProjectRoot "deploy\requirements-windows-portable.txt"
$Python = Join-Path $PythonRoot "python.exe"
$RedisServer = Join-Path $RedisRoot "redis-server.exe"
$RequirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash
$PackageIndexes = @(
  "https://mirrors.aliyun.com/pypi/simple",
  "https://pypi.org/simple"
)

function Invoke-Checked {
  param([string]$FilePath, [string[]]$Arguments)

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
  }
}

function Get-CachedFile {
  param([string]$Url, [string]$Destination)

  if (Test-Path $Destination) {
    Write-Host "Using cached download: $Destination"
    return
  }
  Write-Host "Downloading $Url"
  Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

function Invoke-PythonPackageCommand {
  param([string[]]$Arguments)

  foreach ($indexUrl in $PackageIndexes) {
    Write-Host "Using Python package index: $indexUrl"
    & $Python @Arguments --index-url $indexUrl --retries 8 --timeout 120
    if ($LASTEXITCODE -eq 0) {
      return
    }
    Write-Warning "Python package installation failed from $indexUrl. Trying the next index."
  }
  throw "Python package installation failed from all configured indexes."
}

function Remove-RuntimeDirectory {
  param([string]$Target)

  $resolvedRuntime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') + '\'
  $resolvedTarget = [IO.Path]::GetFullPath($Target)
  if (-not $resolvedTarget.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe runtime cleanup target: $resolvedTarget"
  }
  if (Test-Path $resolvedTarget) {
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
  }
}

function Test-RuntimeCurrent {
  if ($Force -or -not (Test-Path $ManifestPath) -or -not (Test-Path $Python) -or -not (Test-Path $RedisServer)) {
    return $false
  }
  try {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.python_version -ne $PythonVersion) { return $false }
    if ($manifest.redis_version -ne $RedisVersion) { return $false }
    if ($manifest.requirements_sha256 -ne $RequirementsHash) { return $false }
    if (-not (Get-ChildItem -LiteralPath $BrowserRoot -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue | Select-Object -First 1)) {
      return $false
    }
    return $true
  } catch {
    return $false
  }
}

if (Test-RuntimeCurrent) {
  Write-Host "Project-local runtime is current." -ForegroundColor Green
  exit 0
}

Write-Host "Preparing the project-local Windows runtime. The first install can take several minutes." -ForegroundColor Cyan
New-Item -ItemType Directory -Force $RuntimeRoot, $CacheRoot, $DownloadRoot | Out-Null

if (Test-Path (Join-Path $PSScriptRoot "stop-portable.ps1")) {
  & (Join-Path $PSScriptRoot "stop-portable.ps1")
}

$PythonZip = Join-Path $DownloadRoot "python-$PythonVersion-embed-amd64.zip"
$GetPip = Join-Path $DownloadRoot "get-pip.py"
$RedisZip = Join-Path $DownloadRoot "Redis-x64-$RedisVersion.zip"
Get-CachedFile "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $PythonZip
Get-CachedFile "https://bootstrap.pypa.io/get-pip.py" $GetPip
Get-CachedFile "https://github.com/tporadowski/redis/releases/download/v$RedisVersion/Redis-x64-$RedisVersion.zip" $RedisZip

Remove-RuntimeDirectory $PythonRoot
New-Item -ItemType Directory -Force $PythonRoot | Out-Null
Expand-Archive -LiteralPath $PythonZip -DestinationPath $PythonRoot -Force

$PythonMinor = (($PythonVersion.Split(".")[0..1]) -join "")
$PthFile = Join-Path $PythonRoot "python$PythonMinor._pth"
@(
  "python$PythonMinor.zip",
  ".",
  "..\..",
  "Lib",
  "Lib\site-packages",
  "import site"
) | Set-Content -LiteralPath $PthFile -Encoding ASCII

# Do not inherit a machine-wide pip mirror. A stale global mirror makes every
# retry fail in exactly the same way on otherwise clean deployment machines.
$env:PIP_CONFIG_FILE = "nul"
$env:PIP_INDEX_URL = $null
$env:PIP_EXTRA_INDEX_URL = $null
$env:PIP_TRUSTED_HOST = $null
$env:PIP_NO_INDEX = $null
$env:PIP_FIND_LINKS = $null
$env:PIP_REQUIRE_VIRTUALENV = $null

Invoke-PythonPackageCommand @($GetPip, "--no-warn-script-location")
Invoke-PythonPackageCommand @(
  "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-warn-script-location",
  "-r", $RequirementsPath
)
Invoke-Checked $Python @(
  "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-warn-script-location",
  "--no-build-isolation", "--no-deps", (Join-Path $ProjectRoot "form_validate")
)

Remove-RuntimeDirectory $BrowserRoot
New-Item -ItemType Directory -Force $BrowserRoot | Out-Null
$previousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
try {
  Invoke-Checked $Python @("-m", "playwright", "install", "chromium")
} finally {
  $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserPath
}

Remove-RuntimeDirectory $RedisRoot
New-Item -ItemType Directory -Force $RedisRoot | Out-Null
Expand-Archive -LiteralPath $RedisZip -DestinationPath $RedisRoot -Force
New-Item -ItemType Directory -Force (Join-Path $RuntimeRoot "data\redis") | Out-Null

$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = "1"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
Invoke-Checked $Python @(
  "-c",
  "import fitz, openpyxl, pandas, playwright.sync_api, pypdf, redis, requests, werkzeug; import aisec_agent.config; print('runtime imports OK')"
)

$manifest = [ordered]@{
  python_version = $PythonVersion
  redis_version = $RedisVersion
  requirements_sha256 = $RequirementsHash
  installed_at = (Get-Date).ToString("o")
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
Write-Host "Project-local runtime installed successfully." -ForegroundColor Green
