param(
  [string]$HostAddress = "0.0.0.0",
  [int]$Port = 7860,
  [int]$RedisPort = 6389,
  [switch]$SkipGitUpdate,
  [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $ProjectRoot

if (-not [Environment]::Is64BitOperatingSystem) {
  throw "aisec-agent requires 64-bit Windows."
}

$EnvFile = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
  Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
  Write-Host "Created .env from .env.example. Fill required API credentials in .env." -ForegroundColor Yellow
}

$savedStash = ""
$savedStashCommit = ""
$LauncherRelativePaths = @(
  "start.bat",
  "stop.bat",
  "scripts/start.ps1",
  "scripts/stop.ps1"
)
$LegacyCmdNames = @(
  "部署并启动.cmd",
  "更新并启动.cmd",
  "启动项目.cmd",
  "停止项目.cmd"
)

function Remove-LegacyLaunchers {
  foreach ($name in $LegacyCmdNames) {
    $path = Join-Path $ProjectRoot $name
    if (Test-Path -LiteralPath $path) {
      Remove-Item -LiteralPath $path -Force
      Write-Host "Removed legacy launcher: $name" -ForegroundColor Yellow
    }
  }
}

function Restore-LauncherFilesFromStash {
  param([string]$StashRef)

  if ([string]::IsNullOrWhiteSpace($StashRef)) {
    return
  }

  $untrackedTree = "${StashRef}^3"
  & git rev-parse --verify $untrackedTree 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    return
  }

  $toRestore = @()
  foreach ($relativePath in $LauncherRelativePaths) {
    $destination = Join-Path $ProjectRoot $relativePath
    if (Test-Path -LiteralPath $destination) {
      continue
    }
    & git cat-file -e "${untrackedTree}:${relativePath}" 2>$null
    if ($LASTEXITCODE -eq 0) {
      $toRestore += $relativePath
    }
  }
  if ($toRestore.Count -eq 0) {
    return
  }
  & git checkout $untrackedTree -- $toRestore
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Failed to restore launcher files from $untrackedTree"
    return
  }
  foreach ($relativePath in $toRestore) {
    Write-Host "Restored launcher file from stash: $relativePath" -ForegroundColor Green
  }
}

function Test-LauncherTrackedInHead {
  & git cat-file -e "HEAD:scripts/start.ps1" 2>$null
  return ($LASTEXITCODE -eq 0)
}

function Update-FromGit {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warning "Git was not found. Skipping code update and continuing with the local copy."
    return
  }

  $inside = (& git rev-parse --is-inside-work-tree 2>$null)
  if ($LASTEXITCODE -ne 0 -or "$inside".Trim() -ne "true") {
    Write-Warning "This directory is not a Git worktree. Skipping code update."
    return
  }

  if (-not (Test-LauncherTrackedInHead)) {
    Write-Warning "start.bat/scripts/start.ps1 are not on origin yet. Skipping git update so local launchers are not stashed away."
    Remove-LegacyLaunchers
    return
  }

  $branch = (& git branch --show-current).Trim()
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
    throw "The repository is not on a named branch."
  }

  $remoteUrl = (& git remote get-url origin 2>$null)
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remoteUrl)) {
    Write-Warning "Git remote 'origin' was not found. Skipping code update."
    return
  }

  Write-Host "Updating from origin ($remoteUrl) branch $branch ..." -ForegroundColor Cyan

  $changes = @(& git status --porcelain --untracked-files=all)
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the Git worktree."
  }
  if ($changes.Count -gt 0) {
    Write-Host "Saving local uncommitted files before update:" -ForegroundColor Yellow
    $changes | ForEach-Object { Write-Host "  $_" }
    $stashMessage = "automatic backup before updating $branch at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    & git stash push --include-untracked --message $stashMessage
    if ($LASTEXITCODE -ne 0) {
      throw "Unable to save local changes with git stash. Startup was not started."
    }
    $script:savedStash = (& git stash list -1 --format="%gd").Trim()
    if ([string]::IsNullOrWhiteSpace($script:savedStash)) {
      throw "Local changes were reported, but the automatic stash could not be verified."
    }
    $script:savedStashCommit = (& git rev-parse $script:savedStash).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($script:savedStashCommit)) {
      throw "The automatic stash exists, but its commit could not be resolved."
    }
    Write-Host "Local changes saved as $script:savedStash ($script:savedStashCommit)." -ForegroundColor Green
  }

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

  Restore-LauncherFilesFromStash -StashRef $script:savedStash
  Remove-LegacyLaunchers

  $allowedDirtyPaths = @(
    "start.bat",
    "stop.bat",
    "scripts/start.ps1",
    "scripts/stop.ps1",
    "部署并启动.cmd",
    "更新并启动.cmd",
    "启动项目.cmd",
    "停止项目.cmd"
  )
  $changes = @(& git status --porcelain --untracked-files=all)
  $unexpected = @($changes | Where-Object {
    $path = ($_ -replace '^.. ', '').Trim('"')
    $path -notin $allowedDirtyPaths
  })
  if ($unexpected.Count -gt 0) {
    throw "The worktree is not clean after update:`n$($unexpected -join "`n")"
  }

  Write-Host "Code is fully synchronized: $localCommit" -ForegroundColor Green
}

try {
  Remove-LegacyLaunchers

  if (-not $SkipGitUpdate) {
    Update-FromGit
  }

  & (Join-Path $PSScriptRoot "install-runtime.ps1")
  if ($LASTEXITCODE -ne 0) {
    throw "Runtime installation failed."
  }

  $startArgs = @{
    HostAddress = $HostAddress
    Port = $Port
    RedisPort = $RedisPort
    Restart = $true
  }
  if (-not $NoOpen) { $startArgs.Open = $true }

  & (Join-Path $PSScriptRoot "start-portable.ps1") @startArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Web, worker, or Redis startup failed."
  }
} finally {
  if (-not [string]::IsNullOrWhiteSpace($savedStashCommit)) {
    Write-Host ""
    Write-Host "Your previous local changes remain safely stored in $savedStash ($savedStashCommit)." -ForegroundColor Yellow
    Write-Host "Review:  git stash show --stat $savedStashCommit"
    Write-Host "Restore: git stash apply $savedStashCommit"
    Write-Host "The changes are not merged automatically, so the running code stays synchronized with origin."
  }
}
