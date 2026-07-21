param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 7860,
  [switch]$Restart,
  [switch]$Open
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  $Python = "python"
}

function Stop-AisecProcess {
  $targets = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object {
      $_.CommandLine -like "*aisec_agent.web*" -or
      $_.CommandLine -like "*douyin_dm_worker*"
    }

  foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

if ($Restart) {
  Stop-AisecProcess
  Start-Sleep -Seconds 2
}

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listeners -and -not $Restart) {
  Write-Host "Port $Port is already listening. Use -Restart to stop existing aisec_agent web/worker first." -ForegroundColor Yellow
  $listeners | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize
  exit 1
}

$WebOut = Join-Path $ProjectRoot "session-rag-chat.log"
$WebErr = Join-Path $ProjectRoot "session-rag-chat.err.log"
$WorkerOut = Join-Path $ProjectRoot "douyin-dm-worker.log"
$WorkerErr = Join-Path $ProjectRoot "douyin-dm-worker.err.log"

Start-Process -FilePath $Python `
  -ArgumentList @("-m", "aisec_agent.web", "--host", $HostAddress, "--port", [string]$Port, "--no-open") `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $WebOut `
  -RedirectStandardError $WebErr

Start-Process -FilePath $Python `
  -ArgumentList @("-m", "aisec_agent.worker.douyin_dm_worker", "--mode", "send") `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $WorkerOut `
  -RedirectStandardError $WorkerErr

Start-Sleep -Seconds 4

$processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object {
    $_.CommandLine -like "*aisec_agent.web*" -or
    $_.CommandLine -like "*douyin_dm_worker*"
  } |
  Select-Object ProcessId,ParentProcessId,CommandLine

$activeListeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess

Write-Host "Started aisec-agent web and worker." -ForegroundColor Green
Write-Host "Web:    http://127.0.0.1:$Port"

if ($HostAddress -eq "0.0.0.0") {
  $ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -ExpandProperty IPAddress

  foreach ($ip in $ips) {
    Write-Host "LAN:    http://$ip`:$Port"
  }
}

Write-Host "Logs:   $WebErr"
Write-Host "Worker: $WorkerErr"
Write-Host ""
Write-Host "Processes:"
$processes | Format-Table -AutoSize
Write-Host "Listeners:"
$activeListeners | Format-Table -AutoSize

try {
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 8
  if ($health.code -eq 0 -or $health.ok -eq $true) {
    Write-Host "Health check OK." -ForegroundColor Green
  } else {
    Write-Host "Health check returned an unexpected response." -ForegroundColor Yellow
  }
} catch {
  Write-Host "Health check failed. Check session-rag-chat.err.log." -ForegroundColor Yellow
}

if ($Open) {
  Start-Process "http://127.0.0.1:$Port"
}
