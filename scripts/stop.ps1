param(
  [int]$Port = 7860
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot "runtime"
$StateRoot = Join-Path $RuntimeRoot "state"
$Python = Join-Path $RuntimeRoot "python\python.exe"
$RedisServer = Join-Path $RuntimeRoot "redis\redis-server.exe"

function Stop-OwnedProcess {
  param([string]$Name, [string]$ExpectedExecutable)

  $pidFile = Join-Path $StateRoot "$Name.pid"
  if (-not (Test-Path $pidFile)) {
    return
  }
  $savedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
  $process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
  if ($process) {
    try {
      if (
        [string]::IsNullOrWhiteSpace($ExpectedExecutable) -or
        -not (Test-Path $ExpectedExecutable) -or
        [IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath($ExpectedExecutable)
      ) {
        Write-Host "Stopping $Name PID $($process.Id)." -ForegroundColor Yellow
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        try { Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue } catch {}
      }
    } catch {}
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

function Stop-PortListeners {
  param([int]$LocalPort)

  $listeners = @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
  $pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
  foreach ($processId in $pids) {
    if (-not $processId -or $processId -le 0) { continue }
    $detail = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    $summary = if ($detail) { "$($detail.Name) $($detail.CommandLine)" } else { "unknown process" }
    Write-Host "Stopping listener on port $LocalPort PID ${processId}: $summary" -ForegroundColor Yellow
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    try { Wait-Process -Id $processId -Timeout 10 -ErrorAction SilentlyContinue } catch {}
  }

  $deadline = [DateTime]::UtcNow.AddSeconds(15)
  do {
    $remaining = @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) { return }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $deadline)

  $remaining = @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
  if ($remaining.Count -gt 0) {
    $processId = $remaining[0].OwningProcess
    $detail = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    $summary = if ($detail) { "$($detail.Name) $($detail.CommandLine)" } else { "unknown process" }
    throw "Port $LocalPort is still occupied after stop. PID ${processId}: $summary"
  }
}

Stop-OwnedProcess "web" $Python
Stop-OwnedProcess "worker" $Python
Stop-OwnedProcess "redis" $RedisServer

$projectPrefix = "$ProjectRoot*"
$allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$targetIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($process in $allProcesses) {
  if ($process.Name -notin @("python.exe", "pythonw.exe")) { continue }
  $isAisecProcess =
    $process.CommandLine -like "*aisec_agent.web*" -or
    $process.CommandLine -like "*aisec_agent.worker.douyin_dm_worker*"
  $isProjectProcess =
    $process.ExecutablePath -eq $Python -or
    $process.CommandLine -like "*$projectPrefix*"
  if ($isAisecProcess -and $isProjectProcess) {
    [void]$targetIds.Add([int]$process.ProcessId)
  }
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

foreach ($legacyProcess in @($allProcesses | Where-Object { $targetIds.Contains([int]$_.ProcessId) })) {
  Write-Host "Stopping aisec-agent process PID $($legacyProcess.ProcessId)." -ForegroundColor Yellow
  Stop-Process -Id $legacyProcess.ProcessId -Force -ErrorAction SilentlyContinue
  try { Wait-Process -Id $legacyProcess.ProcessId -Timeout 10 -ErrorAction SilentlyContinue } catch {}
}

$redisProcesses = Get-CimInstance Win32_Process -Filter "Name = 'redis-server.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.ExecutablePath -eq $RedisServer }
foreach ($redisProcess in $redisProcesses) {
  Write-Host "Stopping project Redis PID $($redisProcess.ProcessId)." -ForegroundColor Yellow
  Stop-Process -Id $redisProcess.ProcessId -Force -ErrorAction SilentlyContinue
  try { Wait-Process -Id $redisProcess.ProcessId -Timeout 10 -ErrorAction SilentlyContinue } catch {}
}

Remove-Item -LiteralPath (Join-Path $StateRoot "redis.port") -Force -ErrorAction SilentlyContinue
Stop-PortListeners -LocalPort $Port

# Persistent Playwright contexts can outlive their parent after a forced stop.
$profileRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "content\playwright_profiles"))
$browserRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "ms-playwright"))
$browserProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -in @("chrome.exe", "headless_shell.exe") -and
    ($_.ExecutablePath -like "$browserRoot*" -or $_.CommandLine -like "*$profileRoot*")
  }
foreach ($browserProcess in $browserProcesses) {
  Stop-Process -Id $browserProcess.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "aisec-agent stopped. Port $Port is clear." -ForegroundColor Green
