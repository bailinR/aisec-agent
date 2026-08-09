param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 7860,
  [int]$RedisPort = 6389,
  [switch]$Restart,
  [switch]$Open
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvFile = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"

if (-not [Environment]::Is64BitOperatingSystem) {
  throw "aisec-agent requires 64-bit Windows."
}

if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
  Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
  Write-Host "Created .env from .env.example. Fill required API credentials in .env." -ForegroundColor Yellow
}

& (Join-Path $PSScriptRoot "install-runtime.ps1")
if ($LASTEXITCODE -ne 0) {
  throw "Runtime installation failed."
}

$startArgs = @{
  HostAddress = $HostAddress
  Port = $Port
  RedisPort = $RedisPort
}
if ($Restart) { $startArgs.Restart = $true }
if ($Open) { $startArgs.Open = $true }

& (Join-Path $PSScriptRoot "start-portable.ps1") @startArgs
if ($LASTEXITCODE -ne 0) {
  throw "Web, worker, or Redis startup failed."
}
