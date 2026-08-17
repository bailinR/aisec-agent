param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 7860,
  [int]$RedisPort = 6389,
  [switch]$NoOpen
)

# Compatibility wrapper. Prefer start.bat / scripts\start.ps1.
$ErrorActionPreference = "Stop"
$argsMap = @{
  HostAddress = $HostAddress
  Port = $Port
  RedisPort = $RedisPort
}
if ($NoOpen) { $argsMap.NoOpen = $true }
& (Join-Path $PSScriptRoot "start.ps1") @argsMap
