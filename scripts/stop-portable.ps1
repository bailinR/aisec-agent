param(
  [int]$Port = 7860
)

# Compatibility wrapper. Prefer stop.bat / scripts\stop.ps1.
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "stop.ps1") -Port $Port
