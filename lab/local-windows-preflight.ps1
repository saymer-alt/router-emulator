# Requires Windows PowerShell 5.1 or PowerShell 7.
# Read-only inventory. Does not launch QEMU, WSL, download images or print credentials.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EmulatorRoot
)
$ErrorActionPreference = 'Stop'
try {
    $root = (Resolve-Path -LiteralPath $EmulatorRoot -ErrorAction Stop).Path
} catch {
    Write-Error 'EmulatorRoot does not exist. Point it to the folder that contains emulator.exe.'
    exit 2
}

function Show-Check {
    param([string]$Name, [bool]$Found, [string]$Detail = '')
    $status = if ($Found) { 'OK' } else { 'MISSING' }
    Write-Output ('[{0}] {1}{2}' -f $status, $Name, $(if ($Detail) { ': ' + $Detail } else { '' }))
}

$launcher = Test-Path -LiteralPath (Join-Path $root 'emulator.exe') -PathType Leaf
$qemu = Test-Path -LiteralPath (Join-Path $root 'qemu\qemu-system-aarch64.exe') -PathType Leaf
$presets = Test-Path -LiteralPath (Join-Path $root 'presets') -PathType Container
$nand = Test-Path -LiteralPath (Join-Path $root 'nand-wr3000p') -PathType Container
$wsl = $null -ne (Get-Command 'wsl.exe' -ErrorAction SilentlyContinue)

Write-Output 'Router Emulator / Windows — local read-only preflight'
Show-Check 'Windows GUI emulator.exe' $launcher
Show-Check 'Windows QEMU aarch64 binary' $qemu
Show-Check 'Board presets directory' $presets
Show-Check 'WR3000P NAND directory (optional)' $nand 'presence does not prove firmware identity'
Show-Check 'WSL command available' $wsl 'presence does not prove WSL2 Linux is configured'

if ($qemu) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $root 'qemu\qemu-system-aarch64.exe') -Algorithm SHA256).Hash
    Write-Output ('[INFO] Windows QEMU SHA256: ' + $hash)
}
Write-Output '[INFO] No configuration, flash, router, private key, or snapshot has been read.'
Write-Output '[INFO] This preflight proves files exist; it does NOT prove Ported boot, FE IRQ patch, Entware, or KAS acceptance.'
Write-Output '[NEXT] With the emulator powered off, use the Windows GUI on disposable OpenWrt NAND only.'
Write-Output '[NEXT] Keep WAN offline and LAN local-only; never bridge to a real home/work adapter.'
Write-Output '[NEXT] Run wsl --status manually to check WSL2 for the existing Linux native KAS lab.'

if (-not ($launcher -and $qemu -and $presets)) { exit 2 }
exit 0
