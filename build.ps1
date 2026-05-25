# build.ps1 — Cross-build FFmpeg and produce a deployable archive
# Usage:  .\build.ps1                       # prints usage
#         .\build.ps1 -Arch x64
#         .\build.ps1 -Arch arm64
#         .\build.ps1 -Arch armhf
#         .\build.ps1 -Arch win64
#         .\build.ps1 -Arch both            # x64 + arm64
#         .\build.ps1 -Arch all             # x64 + arm64 + armhf + win64
#         .\build.ps1 -Arch x64 -FfmpegVer 8.1 -OutDir .\out
#         .\build.ps1 -Arch arm64 -NoCache
#
# NOTE: --progress=plain is built in; do not pass it on the command line.
#       Use -Progress auto to suppress verbose output.
# NOTE: win64 requires 7-Zip (7z.exe) to be on PATH or at the default install path.

[CmdletBinding()]
param(
    [ValidateSet("armhf","arm64","x64","win64","both","all")]
    [string]$Arch        = "",
    [string]$FfmpegVer   = "8.1",
    [string]$OutDir      = "$PSScriptRoot\out",
    [string]$ImageTag    = "",      # auto-derived from Arch if empty
    [ValidateSet("plain","auto")]
    [string]$Progress    = "plain",
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"

if ($Arch -eq "") {
    Write-Host ""
    Write-Host "Usage:  .\build.ps1 -Arch <arch> [options]" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Targets (-Arch):"
    Write-Host "  x64     linux-x86_64 (Debian Buster, glibc 2.28)  VA-API, NVENC/NVDEC, Vulkan, AMF"
    Write-Host "  arm64   linux-arm64  (Debian Buster, glibc 2.28)  V4L2 M2M, Rockchip MPP, NVENC/NVDEC, Vulkan"
    Write-Host "  armhf   linux-armhf  (Debian Buster, glibc 2.28)  V4L2 M2M"
    Write-Host "  win64   windows-x64  (MinGW-w64 cross)             D3D11VA, DXVA2, NVENC/NVDEC, AMF"
    Write-Host "  both    x64 + arm64"
    Write-Host "  all     x64 + arm64 + armhf + win64"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -FfmpegVer <ver>       FFmpeg version                (default: 8.1)"
    Write-Host "  -OutDir    <path>      Output directory              (default: .\out)"
    Write-Host "  -Progress  plain|auto  Docker build output verbosity (default: plain)"
    Write-Host "  -NoCache               Force full rebuild (no Docker layer cache)"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\build.ps1 -Arch x64"
    Write-Host "  .\build.ps1 -Arch win64"
    Write-Host "  .\build.ps1 -Arch all -FfmpegVer 8.1"
    Write-Host ""
    exit 0
}

# Map Arch param to the Docker TARGET build-arg and output archive name
function Get-ArchConfig($a) {
    switch ($a) {
        "x64"   { @{ Target = "x86_64"; Archive = "ffmpeg${FfmpegVer}-linux-x86_64.tar.xz";  OS = "linux"   } }
        "arm64" { @{ Target = "arm64";  Archive = "ffmpeg${FfmpegVer}-linux-arm64.tar.xz";   OS = "linux"   } }
        "armhf" { @{ Target = "armhf";  Archive = "ffmpeg${FfmpegVer}-linux-armhf.tar.xz";   OS = "linux"   } }
        "win64" { @{ Target = "win64";  Archive = "ffmpeg${FfmpegVer}-windows-x64.7z";        OS = "windows" } }
    }
}

$archList = switch ($Arch) {
    "both" { @("x64","arm64") }
    "all"  { @("x64","arm64","armhf","win64") }
    default { @($Arch) }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

# Locate 7-Zip once (needed for win64)
$7z = $null
if ($archList -contains "win64") {
    $7z = Get-Command "7z" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (-not $7z) { $7z = "C:\Program Files\7-Zip\7z.exe" }
    if (-not (Test-Path $7z)) { throw "7-Zip not found. Install 7-Zip or add 7z.exe to PATH." }
}

foreach ($a in $archList) {
    $cfg         = Get-ArchConfig $a
    $target      = $cfg.Target
    $archiveName = $cfg.Archive
    $archivePath = Join-Path $OutDir $archiveName
    $tag         = if ($ImageTag) { $ImageTag } else { "ffmpeg-${target}-build" }

    Write-Host ""
    Write-Host "==> Building $a  (Docker TARGET=$target, image: $tag)" -ForegroundColor Cyan

    $buildArgs = @(
        "build",
        "--target", "builder",
        "--progress", $Progress,
        "--build-arg", "FFMPEG_VER=$FfmpegVer",
        "--build-arg", "TARGET=$target",
        "-t", $tag
    )
    if ($NoCache) { $buildArgs += "--no-cache" }
    $buildArgs += "."

    $start = Get-Date
    docker @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "docker build failed for $a (exit $LASTEXITCODE)" }

    $elapsed = (Get-Date) - $start
    Write-Host "    Build completed in $([math]::Round($elapsed.TotalMinutes, 1)) min" -ForegroundColor Green

    Write-Host ""
    Write-Host "==> Creating archive: $archivePath" -ForegroundColor Cyan

    if ($cfg.OS -eq "windows") {
        $TempDir     = Join-Path ([System.IO.Path]::GetTempPath()) "ffmpeg-win64-$(New-Guid | Select-Object -ExpandProperty Guid)"
        $ContainerId = (docker create $tag).Trim()
        try {
            New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
            docker cp "${ContainerId}:/opt/ffmpeg/bin/." $TempDir
            if ($LASTEXITCODE -ne 0) { throw "docker cp failed (exit $LASTEXITCODE)" }

            if (Test-Path $archivePath) { Remove-Item $archivePath }
            & $7z a -t7z -mx=9 -mmt=on $archivePath "$TempDir\*.dll" "$TempDir\ffmpeg.exe"
            if ($LASTEXITCODE -ne 0) { throw "7z failed (exit $LASTEXITCODE)" }
        } finally {
            docker rm $ContainerId | Out-Null
            Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        }
    } else {
        $psi = [System.Diagnostics.ProcessStartInfo]::new("docker")
        $psi.Arguments              = "run --rm $tag tar -cJf - -C /opt/ffmpeg bin lib"
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.UseShellExecute        = $false
        $proc = [System.Diagnostics.Process]::Start($psi)
        $outFile = [System.IO.File]::OpenWrite($archivePath)
        try   { $proc.StandardOutput.BaseStream.CopyTo($outFile) }
        finally { $outFile.Close() }
        $proc.WaitForExit()
        if ($proc.ExitCode -ne 0) {
            $err = $proc.StandardError.ReadToEnd()
            throw "tar extraction failed for ${a}: $err"
        }
    }

    $size = (Get-Item $archivePath).Length / 1MB
    Write-Host "    $archivePath  ($([math]::Round($size, 1)) MB)" -ForegroundColor Green
}

Write-Host ""
Write-Host "==> Done." -ForegroundColor Cyan
Write-Host ""
