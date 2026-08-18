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

function Write-Step {
  param([string]$Message)
  Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -ForegroundColor Cyan
}

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
  Write-Step "Downloading $Url"
  Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

function Invoke-PythonPackageCommand {
  param(
    [string[]]$Arguments,
    [string]$Label
  )

  foreach ($indexUrl in $PackageIndexes) {
    Write-Step "$Label (index: $indexUrl)"
    & $Python @Arguments --index-url $indexUrl --retries 3 --timeout 60
    if ($LASTEXITCODE -eq 0) {
      Write-Host "$Label completed." -ForegroundColor Green
      return
    }
    Write-Warning "$Label failed from $indexUrl. Trying the next index."
  }
  throw "$Label failed from all configured indexes."
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

function Test-PythonPackagesReady {
  if (-not (Test-Path $Python)) {
    return $false
  }
  $env:PYTHONHOME = $null
  $env:PYTHONPATH = $null
  $env:PYTHONNOUSERSITE = "1"
  & $Python -c "import fitz, openpyxl, pandas, playwright.sync_api, pypdf, redis, requests, werkzeug; import aisec_agent.config" 1>$null 2>$null
  return ($LASTEXITCODE -eq 0)
}

function Test-BrowserReady {
  return [bool](Get-ChildItem -LiteralPath $BrowserRoot -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-RedisReady {
  return (Test-Path $RedisServer)
}

function Test-RuntimeCurrent {
  if ($Force -or -not (Test-Path $ManifestPath) -or -not (Test-PythonPackagesReady) -or -not (Test-RedisReady) -or -not (Test-BrowserReady)) {
    return $false
  }
  try {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.python_version -ne $PythonVersion) { return $false }
    if ($manifest.redis_version -ne $RedisVersion) { return $false }
    if ($manifest.requirements_sha256 -ne $RequirementsHash) { return $false }
    return $true
  } catch {
    return $false
  }
}

function Write-RuntimeManifest {
  $manifest = [ordered]@{
    python_version = $PythonVersion
    redis_version = $RedisVersion
    requirements_sha256 = $RequirementsHash
    installed_at = (Get-Date).ToString("o")
  }
  $manifest | ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}

function Install-PythonRuntime {
  Write-Step "Installing project-local Python $PythonVersion"
  $PythonZip = Join-Path $DownloadRoot "python-$PythonVersion-embed-amd64.zip"
  $GetPip = Join-Path $DownloadRoot "get-pip.py"
  Get-CachedFile "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $PythonZip
  Get-CachedFile "https://bootstrap.pypa.io/get-pip.py" $GetPip

  Remove-RuntimeDirectory $PythonRoot
  New-Item -ItemType Directory -Force $PythonRoot | Out-Null
  Write-Step "Extracting Python embed package"
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

  # Do not inherit a machine-wide pip mirror.
  $env:PIP_CONFIG_FILE = "nul"
  $env:PIP_INDEX_URL = $null
  $env:PIP_EXTRA_INDEX_URL = $null
  $env:PIP_TRUSTED_HOST = $null
  $env:PIP_NO_INDEX = $null
  $env:PIP_FIND_LINKS = $null
  $env:PIP_REQUIRE_VIRTUALENV = $null

  Invoke-PythonPackageCommand @($GetPip, "--no-warn-script-location") "Installing pip via get-pip.py"
  Invoke-PythonPackageCommand @(
    "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-warn-script-location",
    "-r", $RequirementsPath
  ) "Installing Python requirements"
  Write-Step "Installing local form_validate package"
  Invoke-Checked $Python @(
    "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-warn-script-location",
    "--no-build-isolation", "--no-deps", (Join-Path $ProjectRoot "form_validate")
  )
}

function Install-PlaywrightBrowser {
  Write-Step "Installing Playwright Chromium into runtime\ms-playwright"
  Remove-RuntimeDirectory $BrowserRoot
  New-Item -ItemType Directory -Force $BrowserRoot | Out-Null
  $previousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
  $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
  try {
    Invoke-Checked $Python @("-m", "playwright", "install", "chromium")
  } finally {
    $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserPath
  }
}

function Install-RedisRuntime {
  Write-Step "Installing project-local Redis $RedisVersion"
  $RedisZip = Join-Path $DownloadRoot "Redis-x64-$RedisVersion.zip"
  Get-CachedFile "https://github.com/tporadowski/redis/releases/download/v$RedisVersion/Redis-x64-$RedisVersion.zip" $RedisZip
  Remove-RuntimeDirectory $RedisRoot
  New-Item -ItemType Directory -Force $RedisRoot | Out-Null
  Write-Step "Extracting Redis package"
  Expand-Archive -LiteralPath $RedisZip -DestinationPath $RedisRoot -Force
  New-Item -ItemType Directory -Force (Join-Path $RuntimeRoot "data\redis") | Out-Null
}

if (Test-RuntimeCurrent) {
  Write-Host "Project-local runtime is current." -ForegroundColor Green
  exit 0
}

Write-Host "Preparing the project-local Windows runtime. Missing pieces will be installed; this can take several minutes." -ForegroundColor Cyan
New-Item -ItemType Directory -Force $RuntimeRoot, $CacheRoot, $DownloadRoot | Out-Null

$stopScript = Join-Path $PSScriptRoot "stop.ps1"
if (-not (Test-Path $stopScript)) {
  $stopScript = Join-Path $PSScriptRoot "stop-portable.ps1"
}
if (Test-Path $stopScript) {
  Write-Step "Stopping existing aisec-agent processes before runtime changes"
  & $stopScript
}

$needPython = $Force -or -not (Test-PythonPackagesReady)
$needBrowser = $Force -or -not (Test-BrowserReady)
$needRedis = $Force -or -not (Test-RedisReady)

if ($needPython) {
  Install-PythonRuntime
} else {
  Write-Host "Python packages already usable; skipping Python reinstall." -ForegroundColor Green
}

if ($needBrowser) {
  if (-not (Test-Path $Python)) {
    throw "Python runtime is missing; cannot install Playwright Chromium."
  }
  Install-PlaywrightBrowser
} else {
  Write-Host "Playwright Chromium already present; skipping browser download." -ForegroundColor Green
}

if ($needRedis) {
  Install-RedisRuntime
} else {
  Write-Host "Redis runtime already present; skipping Redis extract." -ForegroundColor Green
}

Write-Step "Verifying runtime imports"
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = "1"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
Invoke-Checked $Python @(
  "-c",
  "import fitz, openpyxl, pandas, playwright.sync_api, pypdf, redis, requests, werkzeug; import aisec_agent.config; print('runtime imports OK')"
)

if (-not (Test-RedisReady)) {
  throw "Redis runtime is incomplete after install: missing $RedisServer"
}
if (-not (Test-BrowserReady)) {
  throw "Playwright Chromium is missing after install under $BrowserRoot"
}

Write-RuntimeManifest
Write-Host "Project-local runtime installed successfully." -ForegroundColor Green
