#!/usr/bin/env pwsh
#
# loreholm BYODB uninstall script (Windows/PowerShell)
# Usage: irm __APP_DOMAIN__/uninstall.ps1 | iex
#
param(
    [Parameter(Mandatory=$false)]
    [string]$InstallDir = "$env:USERPROFILE\.loreholm",

    [Parameter(Mandatory=$false)]
    [switch]$Yes,

    [Parameter(Mandatory=$false)]
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$script:Colors = @{
    Red = "Red"
    Green = "Green"
    Yellow = "Yellow"
    Blue = "Cyan"
}

function Write-Log {
    param([string]$Message)
    Write-Host "[loreholm] " -ForegroundColor $script:Colors.Blue -NoNewline
    Write-Host $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "[✓] " -ForegroundColor $script:Colors.Green -NoNewline
    Write-Host $Message
}

function Write-Warn-Custom {
    param([string]$Message)
    Write-Host "[!] " -ForegroundColor $script:Colors.Yellow -NoNewline
    Write-Host $Message
}

function Show-Usage {
@"
Usage: .\uninstall.ps1 [options]

Options:
  -InstallDir <path>  Install directory (default: $InstallDir)
  -Yes                Skip confirmation prompt
  -Help               Show this help message
"@
    exit 0
}

function Confirm-Uninstall {
    if ($Yes) {
        return
    }

    Write-Host ""
    Write-Warn-Custom "This will remove loreholm containers, loreholm-* Docker volumes, and:"
    Write-Warn-Custom "  $InstallDir"
    $answer = Read-Host "Continue uninstall? [y/N]"
    if ($answer -notmatch '^(?i:y|yes)$') {
        Write-Log "Uninstall canceled."
        exit 0
    }
}

function Get-ComposeArgs {
    $composeFile = Join-Path $InstallDir "docker-compose.yml"
    $chatCompose = Join-Path $InstallDir "docker-compose.chat.yml"
    $args = @("-f", $composeFile)
    if (Test-Path $chatCompose -PathType Leaf) {
        $args += @("-f", $chatCompose)
    }
    return $args
}

function Stop-ComposeStack {
    $composeFile = Join-Path $InstallDir "docker-compose.yml"
    if (-not (Test-Path $composeFile -PathType Leaf)) {
        return
    }

    Write-Log "Stopping loreholm compose services..."
    Push-Location $InstallDir
    try {
        $args = Get-ComposeArgs
        docker compose @args down -v --remove-orphans 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            docker-compose @args down -v --remove-orphans 2>$null | Out-Null
        }
    } catch {
        Write-Warn-Custom "Could not run compose teardown; continuing cleanup."
    } finally {
        Pop-Location
    }
}

function Remove-LoreholmContainers {
    Write-Log "Removing any remaining loreholm-* containers..."
    try {
        $ids = docker ps -a --filter "name=^/loreholm-" --format "{{.ID}}" 2>$null
        foreach ($id in $ids) {
            if ($id) {
                docker rm -f $id 2>$null | Out-Null
            }
        }
    } catch {
        Write-Warn-Custom "Could not remove one or more containers."
    }
}

function Remove-LoreholmVolumes {
    Write-Log "Removing loreholm-* Docker volumes..."
    try {
        $volumes = docker volume ls --format "{{.Name}}" 2>$null | Where-Object { $_ -like "loreholm-*" }
        foreach ($volume in $volumes) {
            if ($volume) {
                docker volume rm $volume 2>$null | Out-Null
            }
        }
    } catch {
        Write-Warn-Custom "Could not remove one or more volumes."
    }
}

function Remove-InstallDirectory {
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Success "Removed install directory: $InstallDir"
    } else {
        Write-Warn-Custom "Install directory not found: $InstallDir"
    }
}

function Invoke-Main {
    if ($Help) {
        Show-Usage
    }

    Confirm-Uninstall

    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Stop-ComposeStack
        Remove-LoreholmContainers
        Remove-LoreholmVolumes
    } else {
        Write-Warn-Custom "Docker not found; removing local files only."
    }

    Remove-InstallDirectory
    Write-Success "loreholm uninstall complete."
}

Invoke-Main
