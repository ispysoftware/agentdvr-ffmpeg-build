# AgentDVR — FFmpeg cross-build

Builds **FFmpeg 8.1 GPL shared libraries** from source for all platforms AgentDVR ships, targeting [FFmpeg.AutoGen 8.1](https://github.com/Ruslan-B/FFmpeg.AutoGen). Linux and Windows builds run inside Docker on any x86_64 machine; macOS builds run natively on the target architecture.

## Why this exists

FFmpeg.AutoGen loads FFmpeg's shared libraries at runtime via P/Invoke. The libraries must be:

- **The right ABI version** — FFmpeg.AutoGen 8.1 expects the 8.1 ABI (avcodec 62, avformat 62, avutil 60, etc.).
- **glibc 2.28-compatible** (Linux) — AgentDVR targets Debian 10 (Buster), Ubuntu 18.10+, RHEL 8+, and Raspberry Pi OS Buster.
- **Hardware-acceleration-enabled** — each platform needs its own HW accel flags; no single binary covers all.

## Targets

| Target       | Build method            | OS floor      | Hardware acceleration                                        |
|--------------|-------------------------|---------------|--------------------------------------------------------------|
| `armhf`      | Docker / Debian Buster  | glibc 2.28    | V4L2 M2M (Pi 2/3/4 H.264 kernel decoder)                    |
| `arm64`      | Docker / Debian Buster  | glibc 2.28    | V4L2 M2M · Rockchip MPP (RK3566/RK3568/RK3588) · NVENC/NVDEC (Jetson) · Vulkan |
| `x86_64`     | Docker / Debian Buster  | glibc 2.28    | VA-API (Intel/AMD)¹ · NVENC/NVDEC (NVIDIA) · Vulkan · AMF (AMD) |
| `win64`      | Docker / MinGW-w64      | Windows 10+   | D3D11VA · DXVA2 · NVENC/NVDEC (NVIDIA) · AMF (AMD)          |
| `macos-arm64`| Native / macOS runner   | macOS 12+     | VideoToolbox · AudioToolbox                                  |
| `macos-x86_64`| Native / macOS runner  | macOS 12+     | VideoToolbox · AudioToolbox                                  |

¹ VA-API needs a host-provided GPU driver at runtime (libva is bundled, the driver is not). See [Bundled shared libraries](#bundled-shared-libraries-linux) and [Docker / VA-API](#docker--va-api).

## Codec libraries bundled

All dependencies are compiled statically into the FFmpeg `.so` / `.dylib` / `.dll` files — nothing extra to deploy alongside them. The two exceptions are the runtime loader libraries listed under [Bundled shared libraries](#bundled-shared-libraries-linux) below, which are shipped as `.so` files in the same directory.

| Library     | Version | Licence     | Purpose                          |
|-------------|---------|-------------|----------------------------------|
| libx264     | stable  | GPL 2+      | H.264 encoder                    |
| libvpx      | 1.14.1  | BSD         | VP8 / VP9 encoder + decoder      |
| dav1d       | 1.4.1   | BSD         | AV1 decoder                      |
| libopus     | 1.5.2   | BSD         | Opus audio codec                 |
| libvorbis   | 1.3.7   | BSD         | Vorbis audio codec               |
| libmp3lame  | 3.100   | LGPL 2      | MP3 encoder                      |
| OpenSSL     | 3.4.1   | Apache 2.0  | HTTPS / RTMPS transport          |
| zlib        | 1.3.1   | zlib        | Deflate (MKV/MP4 metadata)       |
| bzip2       | 1.0.8   | BSD-like    | bzip2 demux                      |
| liblzma/xz  | 5.6.2   | Public Domain | xz/lzma demux                  |

### arm64 only

| Library       | Licence     | Purpose                                            |
|---------------|-------------|----------------------------------------------------|
| Rockchip MPP  | Apache 2.0  | Rockchip VPU H.264/H.265 hardware codec. Deployed as `librockchip_mpp.so` alongside the FFmpeg libs. The driver (`rkvdec` / `mpp_service`) must be present in the target kernel — on non-Rockchip boards FFmpeg simply won't find it at runtime and falls back to software. |
| libdrm        | MIT         | DRM Prime buffer sharing (required by rkmpp)       |

> **Note on Rockchip MPP source.** The canonical `rockchip-linux/mpp` repo was DMCA-taken-down in December 2025. This build clones `nyanmisaka/rk-mirrors` (branch `jellyfin-mpp-next`) — the same fork Jellyfin uses in production.

## Bundled shared libraries (Linux)

A few libraries can't be statically linked because they are runtime loaders that must `dlopen` a driver provided by the host. These ship as `.so` files inside the tarball, next to the FFmpeg libs, each stamped with `RUNPATH=$ORIGIN` so they resolve from their own directory without `LD_LIBRARY_PATH`.

| Library    | Targets        | Why it's shipped, not static                                  |
|------------|----------------|---------------------------------------------------------------|
| libva (`libva.so.2`, `libva-drm.so.2`) | x86_64 | VA-API loader. **Built from source (2.23.0)**, not taken from the Buster base image. Buster's libva 2.4 (2019) only probes driver entry points up to `__vaDriverInit_1_4`; any current Mesa `radeonsi` / Intel `iHD` driver exports a higher VA-API minor and fails `vaInitialize()` with `EIO` (FFmpeg reports `ret=-5`) even when the driver is correctly installed. Bundling a current libva lets the tarball load whatever driver the host provides. |
| libvulkan  | arm64, x86_64  | Vulkan loader — links at build time but isn't present on minimal installs. |
| librockchip_mpp | arm64     | Rockchip VPU codec (see [arm64 only](#arm64-only) above).      |

> **The driver itself is not bundled.** The Mesa VA-API driver (`radeonsi_drv_video.so` for AMD, `iHD_drv_video.so` for Intel) pulls in `libLLVM` (~200 MB) and is coupled to the host kernel/GPU, so it must come from the host's package manager. This is the same split jellyfin-ffmpeg uses. The `linux_setup2.sh` installer in [agent-install-scripts](https://github.com/ispysoftware/agent-install-scripts) installs `mesa-va-drivers` / `intel-media-va-driver` for bare-metal installs; **Docker images must install it themselves** — see [Docker / VA-API](#docker--va-api) below.

## CI — GitHub Actions

Pushing a version tag builds all six targets in parallel and attaches the archives to a GitHub Release automatically:

```powershell
git tag v8.1
git push origin v8.1
```

You can also trigger a single target manually from **Actions → Build FFmpeg → Run workflow** in the GitHub UI — useful for testing without burning minutes on all six jobs.

See [`.github/workflows/build.yml`](.github/workflows/build.yml) for the full workflow definition.

## Building locally

### Linux / Windows targets (requires Docker)

```powershell
# Default target: prints usage
.\build.ps1

# Specific target
.\build.ps1 -Arch x64
.\build.ps1 -Arch arm64
.\build.ps1 -Arch armhf
.\build.ps1 -Arch win64

# Build all four at once
.\build.ps1 -Arch all

# Override FFmpeg version or output directory
.\build.ps1 -Arch x64 -FfmpegVer 8.1 -OutDir .\dist

# Force a full rebuild (no Docker layer cache)
.\build.ps1 -Arch x64 -NoCache
```

### macOS targets (requires Xcode command-line tools + Homebrew)

Run natively on the target architecture. The script installs any missing build tools via Homebrew and compiles everything from source.

```bash
# arm64 (run on Apple Silicon)
./build_macos.sh

# x86_64 (run on Intel Mac)
./build_macos.sh

# Override FFmpeg version or parallelism
FFMPEG_VER=8.1 ./build_macos.sh
FFMPEG_VER=8.1 JOBS=8 ./build_macos.sh
```

## Outputs

### Linux targets (`armhf`, `arm64`, `x86_64`)

```
out/
  ffmpeg8.1-linux-armhf.tar.xz
  ffmpeg8.1-linux-arm64.tar.xz
  ffmpeg8.1-linux-x86_64.tar.xz
```

The archive contains `/bin/ffmpeg` and `/lib/libav*.so.*`, `/lib/libsw*.so.*`. Extract with:

```bash
sudo tar -xJf ffmpeg8.1-linux-arm64.tar.xz -C /usr/local && sudo ldconfig
```

The arm64 tarball also includes `librockchip_mpp.so` under `lib/`.

### Windows (`win64`)

```
out/
  ffmpeg8.1-windows-x64.7z
```

The archive contains only the 7 DLLs and `ffmpeg.exe` at the root. Drop them into your application directory alongside your `.exe` (or anywhere on `PATH`):

```
avcodec-62.dll  avdevice-62.dll  avfilter-11.dll  avformat-62.dll
avutil-60.dll   swresample-6.dll  swscale-9.dll    ffmpeg.exe
```

FFmpeg.AutoGen discovers them automatically via `PATH` or the application directory.

### macOS (`macos-arm64`, `macos-x86_64`)

```
out/
  ffmpeg8.1-macos-arm64.tar.xz
  ffmpeg8.1-macos-x86_64.tar.xz
```

The archive contains `/bin/ffmpeg` and `/lib/libav*.dylib`, `/lib/libsw*.dylib`. All dylib install names use `@rpath` so they can be loaded from any directory. Extract with:

```bash
sudo tar -xJf ffmpeg8.1-macos-arm64.tar.xz -C /usr/local
```

## Build options

### Linux / Windows (`build.ps1`)

| Parameter    | Default  | Description                                            |
|--------------|----------|--------------------------------------------------------|
| `-Arch`      | *(required)* | `armhf` / `arm64` / `x64` / `win64` / `both` / `all` |
| `-FfmpegVer` | `8.1`    | FFmpeg release tag                                     |
| `-OutDir`    | `.\out`  | Output directory                                       |
| `-NoCache`   | off      | Force full rebuild (no Docker layer cache)             |

All dependency version pins are at the top of `Dockerfile` — bump them there to update a dependency.

### macOS (`build_macos.sh`)

| Variable     | Default  | Description                                            |
|--------------|----------|--------------------------------------------------------|
| `FFMPEG_VER` | `8.1`    | FFmpeg release tag                                     |
| `JOBS`       | CPU count | Parallel make jobs                                    |

All version pins are at the top of `build_macos.sh`.

## Licence note

`--enable-gpl` is required for libx264. The resulting binaries are **GPL 2+**. Do not distribute them in a closed-source product without complying with the GPL (source offer etc.).

## File map

| File                              | Purpose                                                           |
|-----------------------------------|-------------------------------------------------------------------|
| `Dockerfile`                      | Single multi-target Dockerfile. `ARG TARGET` selects the target. |
| `build.ps1`                       | PowerShell orchestrator for Linux/Windows — builds image and extracts archive. |
| `build_macos.sh`                  | Bash script for macOS — builds all deps and FFmpeg from source natively. |
| `.github/workflows/build.yml`     | GitHub Actions workflow — parallel matrix of all 6 targets, triggered by version tags. |
| `out/`                            | Created at build time. Holds output archives. Gitignored.         |

## Wiring into AgentDVR

AgentDVR's `Dependencies.cs` downloader expects archives at:

```
https://files.ispyconnect.com/libs/ffmpeg/<version>/linux-<target>.tar.xz
https://files.ispyconnect.com/libs/ffmpeg/<version>/windows-x64.7z
https://files.ispyconnect.com/libs/ffmpeg/<version>/macos-arm64.tar.xz
https://files.ispyconnect.com/libs/ffmpeg/<version>/macos-x86_64.tar.xz
```

To roll a new version:

1. Push a version tag (e.g. `git tag v8.1 && git push origin v8.1`).
2. Wait for all six CI jobs to complete and attach their archives to the GitHub Release.
3. Upload archives to the CDN under `libs/ffmpeg/<new-version>/`.
4. Bump the version constant in `Dependencies.cs`.

## Troubleshooting

**Linux/Windows build fails in Docker.**  
Run with `-NoCache` to rule out a stale layer. The FFmpeg configure step dumps the last 100 lines of `config.log` on failure — check the Docker build output or the GitHub Actions log for that block.

**`GLIBC_2.X not found` on the target.**  
The `ldd` / `nm` checks in the Dockerfile verify the glibc floor before exporting. If you see this at runtime, the wrong binary was deployed — check the archive matches the target arch.

**Rockchip MPP not found at runtime.**  
The kernel driver (`rkvdec` / `mpp_service`) must be loaded. Check `lsmod | grep rkvdec`. On non-Rockchip hardware this is expected — FFmpeg falls back to software decode.

### Docker / VA-API

**`VAAPI open failed on /dev/dri/renderD128 (ret=-5)` / falls back to software encoding.**  
`ret=-5` is `AVERROR(EIO)` — the device node opened but the VA-API driver failed to initialise. (A permissions problem returns `-13`/`EACCES` instead — that one *is* the `video`/`render` group issue.) `-5` almost always means **the Mesa VA-API driver is missing from the image**. libva is bundled in the tarball; the driver is not. Install it in the container image and map the device:

```dockerfile
# Debian/Ubuntu base
RUN apt-get update && apt-get install -y --no-install-recommends \
      mesa-va-drivers \                 # AMD (radeonsi)
      intel-media-va-driver-non-free \  # Intel Gen8+ (iHD); use i965-va-driver for older
      libva-utils                       # provides vainfo, for debugging
# Alpine: apk add libva-mesa-driver intel-media-driver libva-utils
# Fedora: dnf install mesa-va-drivers intel-media-driver libva-utils
```

```yaml
# docker run / compose — map the GPU in
devices:
  - /dev/dri:/dev/dri
```

Verify inside the running container with `vainfo --display drm --device /dev/dri/renderD128`. If it lists profiles, AgentDVR will pick up hardware encode on next start. If `vainfo` reports no driver despite the package being installed, set `LIBVA_DRIVER_NAME=radeonsi` (AMD) or `iHD` (Intel) as a container env var.

> **NVIDIA is the opposite model** — install nothing in the image. The host driver is injected by the [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit) (`--gpus all`), which bind-mounts `libnvcuvid` / `libnvidia-encode` matching the host driver version. Installing NVIDIA userspace libs in the image breaks this.

**win64 DLLs not found by FFmpeg.AutoGen.**  
Put the DLLs in the same directory as the application `.exe`, or anywhere on `PATH`. FFmpeg.AutoGen calls `LoadLibrary` with bare names (`avcodec-62.dll` etc.) — the Windows loader resolves from the application directory first.

**macOS dylibs not found at runtime.**  
Ensure the `lib/` directory is on the rpath of your application. All dylibs use `@rpath/<name>` install names — add the lib directory with `install_name_tool -add_rpath <path> <your-binary>` or set `DYLD_LIBRARY_PATH` for testing.
