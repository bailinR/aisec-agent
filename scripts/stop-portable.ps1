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
      if ([IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath($ExpectedExecutable)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        try { Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue } catch {}
      }
    } catch {}
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

Stop-OwnedProcess "web" $Python
Stop-OwnedProcess "worker" $Python
Stop-OwnedProcess "redis" $RedisServer

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

Write-Host "aisec-agent portable runtime stopped." -ForegroundColor Green
