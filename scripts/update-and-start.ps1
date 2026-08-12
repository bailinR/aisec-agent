param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 7860,
  [int]$RedisPort = 6389,
  [switch]$NoOpen
)

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
$savedStash = ""
$savedStashCommit = ""
if ($changes.Count -gt 0) {
  Write-Host "Saving local uncommitted files before update:" -ForegroundColor Yellow
  $changes | ForEach-Object { Write-Host "  $_" }
  $stashMessage = "automatic backup before updating $branch at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  & git stash push --include-untracked --message $stashMessage
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to save local changes with git stash. Update was not started."
  }
  $savedStash = (& git stash list -1 --format="%gd").Trim()
  if ([string]::IsNullOrWhiteSpace($savedStash)) {
    throw "Local changes were reported, but the automatic stash could not be verified."
  }
  $savedStashCommit = (& git rev-parse $savedStash).Trim()
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($savedStashCommit)) {
    throw "The automatic stash exists, but its commit could not be resolved."
  }
  Write-Host "Local changes saved as $savedStash ($savedStashCommit)." -ForegroundColor Green
}

try {
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
  $startArgs = @{
    HostAddress = $HostAddress
    Port = $Port
    RedisPort = $RedisPort
    Restart = $true
  }
  if (-not $NoOpen) { $startArgs.Open = $true }

  & (Join-Path $PSScriptRoot "deploy-start.ps1") @startArgs
  if ($LASTEXITCODE -ne 0) { throw "Project startup failed." }
} finally {
  if (-not [string]::IsNullOrWhiteSpace($savedStashCommit)) {
    Write-Host ""
    Write-Host "Your previous local changes remain safely stored in $savedStash ($savedStashCommit)." -ForegroundColor Yellow
    Write-Host "Review:  git stash show --stat $savedStashCommit"
    Write-Host "Restore: git stash apply $savedStashCommit"
    Write-Host "The changes are not merged automatically, so the running code stays synchronized with origin/$branch."
  }
}
