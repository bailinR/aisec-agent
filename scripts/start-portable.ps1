param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 7860,
  [int]$RedisPort = 6389,
  [switch]$Restart,
  [switch]$Open
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot "runtime"
$Python = Join-Path $RuntimeRoot "python\python.exe"
$RedisServer = Join-Path $RuntimeRoot "redis\redis-server.exe"
$RedisCli = Join-Path $RuntimeRoot "redis\redis-cli.exe"
$RedisConfig = Join-Path $ProjectRoot "deploy\redis.windows.portable.conf"
$BrowserRoot = Join-Path $RuntimeRoot "ms-playwright"
$StateRoot = Join-Path $RuntimeRoot "state"
$LogRoot = Join-Path $RuntimeRoot "logs"
$RedisData = Join-Path $RuntimeRoot "data\redis"
$TempRoot = Join-Path $RuntimeRoot "temp"

foreach ($requiredFile in @($Python, $RedisServer, $RedisCli, $RedisConfig)) {
  if (-not (Test-Path $requiredFile)) {
    throw "Project runtime is incomplete. Missing: $requiredFile. Run scripts\install-runtime.ps1."
  }
}
New-Item -ItemType Directory -Force $StateRoot, $LogRoot, $RedisData, $TempRoot | Out-Null

function Get-OwnedProcess {
  param([string]$Name, [string]$ExpectedExecutable)

  $pidFile = Join-Path $StateRoot "$Name.pid"
  if (-not (Test-Path $pidFile)) {
    return $null
  }
  $savedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
  $process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
  if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    return $null
  }
  try {
    if ([IO.Path]::GetFullPath($process.Path) -ne [IO.Path]::GetFullPath($ExpectedExecutable)) {
      return $null
    }
  } catch {
    return $null
  }
  return $process
}

function Stop-OwnedProcess {
  param([string]$Name, [string]$ExpectedExecutable)

  $process = Get-OwnedProcess $Name $ExpectedExecutable
  if ($process) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    try { Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue } catch {}
  }
  Remove-Item -LiteralPath (Join-Path $StateRoot "$Name.pid") -Force -ErrorAction SilentlyContinue
}

function Assert-PortAvailable {
  param([int]$LocalPort, [string]$ServiceName)

  $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
  if ($listener) {
    throw "$ServiceName cannot start because port $LocalPort is already in use by PID $($listener[0].OwningProcess)."
  }
}

if ($Restart) {
  Stop-OwnedProcess "web" $Python
  Stop-OwnedProcess "worker" $Python
  Stop-OwnedProcess "redis" $RedisServer
}

$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:AISEC_DM_PLAYWRIGHT_PROFILE_ROOT = Join-Path $ProjectRoot "content\playwright_profiles"
$env:REDIS_URL = "redis://127.0.0.1:$RedisPort/11"
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PATH = @(
  (Join-Path $RuntimeRoot "python"),
  (Join-Path $RuntimeRoot "redis"),
  (Join-Path $env:SystemRoot "System32"),
  $env:SystemRoot,
  (Join-Path $env:SystemRoot "System32\Wbem"),
  (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0")
) -join ";"

$redisProcess = Get-OwnedProcess "redis" $RedisServer
if (-not $redisProcess) {
  Assert-PortAvailable $RedisPort "Redis"
  $redisArguments = @(
    "`"$RedisConfig`"",
    "--port", [string]$RedisPort,
    "--dir", "`"$RedisData`""
  )
  $redisProcess = Start-Process -FilePath $RedisServer `
    -ArgumentList $redisArguments `
    -WorkingDirectory (Split-Path $RedisServer) `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogRoot "redis.log") `
    -RedirectStandardError (Join-Path $LogRoot "redis.err.log") `
    -PassThru
  Set-Content -LiteralPath (Join-Path $StateRoot "redis.pid") -Value $redisProcess.Id -Encoding ASCII
}

$redisReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
  try {
    $pong = & $RedisCli -h 127.0.0.1 -p $RedisPort ping 2>$null
    if ($pong -eq "PONG") {
      $redisReady = $true
      break
    }
  } catch {}
  Start-Sleep -Milliseconds 250
}
if (-not $redisReady) {
  throw "Portable Redis did not become ready. Check runtime\logs\redis.err.log."
}

$webProcess = Get-OwnedProcess "web" $Python
$workerProcess = Get-OwnedProcess "worker" $Python
if ($webProcess -or $workerProcess) {
  throw "Portable Web or worker is already running. Use -Restart to restart it."
}
Assert-PortAvailable $Port "Web"

$webProcess = Start-Process -FilePath $Python `
  -ArgumentList @("-m", "aisec_agent.web", "--host", $HostAddress, "--port", [string]$Port, "--no-open") `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogRoot "web.log") `
  -RedirectStandardError (Join-Path $LogRoot "web.err.log") `
  -PassThru
Set-Content -LiteralPath (Join-Path $StateRoot "web.pid") -Value $webProcess.Id -Encoding ASCII

$workerProcess = Start-Process -FilePath $Python `
  -ArgumentList @("-m", "aisec_agent.worker.douyin_dm_worker", "--mode", "send") `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogRoot "worker.log") `
  -RedirectStandardError (Join-Path $LogRoot "worker.err.log") `
  -PassThru
Set-Content -LiteralPath (Join-Path $StateRoot "worker.pid") -Value $workerProcess.Id -Encoding ASCII

$healthUrl = "http://127.0.0.1:$Port/api/health"
$healthReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
  try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($health.code -eq 0 -or $health.ok -eq $true) {
      $healthReady = $true
      break
    }
  } catch {}
  if ($webProcess.HasExited -or $workerProcess.HasExited) {
    break
  }
  Start-Sleep -Milliseconds 500
}

if (-not $healthReady) {
  Write-Host "Web startup failed. Recent log output:" -ForegroundColor Red
  Get-Content -LiteralPath (Join-Path $LogRoot "web.err.log") -Tail 40 -ErrorAction SilentlyContinue
  Stop-OwnedProcess "web" $Python
  Stop-OwnedProcess "worker" $Python
  throw "Health check failed: $healthUrl"
}

Write-Host "aisec-agent portable runtime started." -ForegroundColor Green
Write-Host "Web:     http://127.0.0.1:$Port"
Write-Host "Health:  $healthUrl"
Write-Host "Redis:   127.0.0.1:$RedisPort (project-local)"
Write-Host "Logs:    $LogRoot"
if ($HostAddress -eq "0.0.0.0") {
  $ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -ExpandProperty IPAddress
  foreach ($ip in $ips) {
    Write-Host "LAN:     http://$ip`:$Port"
  }
}

if ($Open) {
  Start-Process "http://127.0.0.1:$Port"
}
