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

function Get-AisecServiceProcesses {
  $projectPrefix = "$ProjectRoot*"
  @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) |
    Where-Object {
      $_.Name -in @("python.exe", "pythonw.exe") -and
      (
        $_.CommandLine -like "*aisec_agent.web*" -or
        $_.CommandLine -like "*aisec_agent.worker.douyin_dm_worker*"
      ) -and (
        $_.ExecutablePath -eq $Python -or
        $_.CommandLine -like "*$projectPrefix*"
      )
    }
}

function Stop-LegacyAisecProcesses {
  param([int]$WebPort)

  $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $targetIds = [System.Collections.Generic.HashSet[int]]::new()

  foreach ($process in @(Get-AisecServiceProcesses)) {
    [void]$targetIds.Add([int]$process.ProcessId)
  }

  do {
    $added = $false
    foreach ($process in $allProcesses) {
      if (
        $process.Name -in @("python.exe", "pythonw.exe") -and
        $targetIds.Contains([int]$process.ParentProcessId) -and
        (
          $process.CommandLine -like "*aisec_agent.web*" -or
          $process.CommandLine -like "*aisec_agent.worker.douyin_dm_worker*"
        )
      ) {
        if ($targetIds.Add([int]$process.ProcessId)) { $added = $true }
      }
    }
  } while ($added)

  $targets = @($allProcesses | Where-Object { $targetIds.Contains([int]$_.ProcessId) })
  foreach ($target in $targets) {
    Write-Host "Stopping legacy aisec-agent process PID $($target.ProcessId): $($target.CommandLine)" -ForegroundColor Yellow
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
  }
  foreach ($target in $targets) {
    try { Wait-Process -Id $target.ProcessId -Timeout 10 -ErrorAction SilentlyContinue } catch {}
  }

  Remove-Item -LiteralPath (Join-Path $StateRoot "web.pid") -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $StateRoot "worker.pid") -Force -ErrorAction SilentlyContinue
  $remainingListener = Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($remainingListener) {
    $remainingProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($remainingListener.OwningProcess)" -ErrorAction SilentlyContinue
    $remainingDetail = if ($remainingProcess) { "$($remainingProcess.Name) $($remainingProcess.CommandLine)" } else { "unknown process" }
    throw "Port $WebPort is occupied by a process that was not identified as this project's Web service. PID $($remainingListener.OwningProcess): $remainingDetail"
  }
}

function Stop-ProjectRedisProcesses {
  $targets = Get-CimInstance Win32_Process -Filter "Name = 'redis-server.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $RedisServer }
  foreach ($target in $targets) {
    Write-Host "Stopping project Redis PID $($target.ProcessId)." -ForegroundColor Yellow
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    try { Wait-Process -Id $target.ProcessId -Timeout 10 -ErrorAction SilentlyContinue } catch {}
  }
}

function Wait-PortReleased {
  param([int]$LocalPort, [int]$TimeoutSeconds = 15)

  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) {
      return
    }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $deadline)

  $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  $process = if ($listener) { Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue } else { $null }
  $detail = if ($process) { "$($process.Name) $($process.CommandLine)" } else { "unknown process" }
  throw "Port $LocalPort was not released. PID $($listener.OwningProcess): $detail"
}

function Disable-LegacyDmWatchdog {
  $taskName = "AisecDmWatchdog"
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if (-not $task) { return }

  $watchdogScripts = @(
    $task.Actions |
      ForEach-Object {
        if ($_.Arguments -match '(?i)-File\s+(?:"([^"]+)"|([^\s]+))') {
          if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
        }
      } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )

  if ($task.State -ne "Disabled") {
    try {
      Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
      Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
    } catch {
      throw "Scheduled task $taskName is still enabled and could not be disabled. Run this startup once as Administrator to prevent recurring PowerShell windows. $($_.Exception.Message)"
    }
  }

  if ($watchdogScripts.Count -gt 0) {
    $watchdogProcesses = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
      Where-Object {
        $commandLine = $_.CommandLine
        @($watchdogScripts | Where-Object { $commandLine -like "*$_*" }).Count -gt 0
      }
    foreach ($watchdogProcess in $watchdogProcesses) {
      Stop-Process -Id $watchdogProcess.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }

  Write-Host "Legacy scheduled task $taskName is disabled; recurring PowerShell windows are prevented." -ForegroundColor Green
}

function Assert-PortAvailable {
  param([int]$LocalPort, [string]$ServiceName)

  $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
  if ($listener) {
    throw "$ServiceName cannot start because port $LocalPort is already in use by PID $($listener[0].OwningProcess)."
  }
}

function Find-AvailablePort {
  param(
    [int]$PreferredPort,
    [int]$MaximumAttempts = 100
  )

  for ($candidate = $PreferredPort; $candidate -lt ($PreferredPort + $MaximumAttempts); $candidate++) {
    $listener = Get-NetTCPConnection -LocalPort $candidate -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) {
      return $candidate
    }
  }
  throw "No available Redis port was found between $PreferredPort and $($PreferredPort + $MaximumAttempts - 1)."
}

Disable-LegacyDmWatchdog

if ($Restart) {
  Stop-OwnedProcess "web" $Python
  Stop-OwnedProcess "worker" $Python
  Stop-OwnedProcess "redis" $RedisServer
  Stop-LegacyAisecProcesses -WebPort $Port
  Stop-ProjectRedisProcesses
  Remove-Item -LiteralPath (Join-Path $StateRoot "redis.port") -Force -ErrorAction SilentlyContinue
  Wait-PortReleased -LocalPort $Port
}

$requestedRedisPort = $RedisPort
$redisProcess = Get-OwnedProcess "redis" $RedisServer
if ($redisProcess) {
  $savedRedisPortFile = Join-Path $StateRoot "redis.port"
  if (Test-Path $savedRedisPortFile) {
    $savedRedisPort = [int](Get-Content -LiteralPath $savedRedisPortFile -Raw)
    if ($savedRedisPort -ge 1 -and $savedRedisPort -le 65535) {
      $RedisPort = $savedRedisPort
    }
  }
} else {
  $RedisPort = Find-AvailablePort -PreferredPort $RedisPort
  if ($RedisPort -ne $requestedRedisPort) {
    $occupiedListener = Get-NetTCPConnection -LocalPort $requestedRedisPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $occupiedPid = if ($occupiedListener) { $occupiedListener.OwningProcess } else { "unknown" }
    Write-Warning "Redis port $requestedRedisPort is occupied by PID $occupiedPid. Using available port $RedisPort for this project."
  }
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

if (-not $redisProcess) {
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
Set-Content -LiteralPath (Join-Path $StateRoot "redis.port") -Value $RedisPort -Encoding ASCII

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

$serviceProcesses = @(Get-AisecServiceProcesses)
$webProcesses = @($serviceProcesses | Where-Object { $_.CommandLine -like "*aisec_agent.web*" })
$workerProcesses = @($serviceProcesses | Where-Object { $_.CommandLine -like "*douyin_dm_worker*" })
$webListeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
$listenerPids = @($webListeners | Select-Object -ExpandProperty OwningProcess -Unique)

if ($webProcesses.Count -ne 1 -or $workerProcesses.Count -ne 1) {
  Stop-LegacyAisecProcesses -WebPort $Port
  throw "Single-instance verification failed. Web=$($webProcesses.Count), worker=$($workerProcesses.Count)."
}
if ($listenerPids.Count -ne 1 -or $listenerPids[0] -ne $webProcess.Id) {
  Stop-LegacyAisecProcesses -WebPort $Port
  throw "Port verification failed. Port $Port is not owned exclusively by the new Web process."
}

Write-Host "aisec-agent portable runtime started." -ForegroundColor Green
Write-Host "Web:     http://127.0.0.1:$Port"
Write-Host "Health:  $healthUrl"
Write-Host "Redis:   127.0.0.1:$RedisPort (project-local)"
Write-Host "Workers: 1"
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
