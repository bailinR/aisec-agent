$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $ProjectRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "Git was not found. Install Git for Windows before using automatic update."
}

$branch = (& git branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
  throw "The repository is not on a named branch."
}

$changes = @(& git status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
  throw "Unable to inspect the Git worktree."
}
if ($changes.Count -gt 0) {
  Write-Host "Local files have uncommitted changes:" -ForegroundColor Yellow
  $changes | ForEach-Object { Write-Host "  $_" }
  throw "Update stopped to avoid overwriting local files. Commit or remove these changes first."
}

Write-Host "Fetching origin/$branch ..."
& git fetch --prune origin
if ($LASTEXITCODE -ne 0) { throw "git fetch failed." }

& git pull --ff-only origin $branch
if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed." }

& git fetch origin $branch
if ($LASTEXITCODE -ne 0) { throw "Final remote verification fetch failed." }

$localCommit = (& git rev-parse HEAD).Trim()
$remoteCommit = (& git rev-parse "origin/$branch").Trim()
if ($localCommit -ne $remoteCommit) {
  throw "Code verification failed. Local=$localCommit Remote=$remoteCommit"
}

$changes = @(& git status --porcelain --untracked-files=all)
if ($changes.Count -gt 0) {
  throw "The worktree is not clean after update."
}

Write-Host "Code is fully synchronized: $localCommit" -ForegroundColor Green
& (Join-Path $PSScriptRoot "deploy-start.ps1") -Restart -Open
if ($LASTEXITCODE -ne 0) { throw "Project startup failed." }
