# FFmpeg source patches

Applied to the extracted FFmpeg source with `patch -p1`, in filename order, by
both the Dockerfile (Linux/Windows targets) and `build_macos.sh`. Keep the two
apply steps in sync. Files must stay LF (`.gitattributes` pins `*.patch`), and
both apply steps strip CRLF defensively anyway.

## 0001-rtsp-accept-unlabeled-tcp-interleaved-reply.patch

Some cheap OEM camera firmwares (sricam clones and similar) reply to a TCP
SETUP request with a Transport header that omits the explicit lower-transport
token but still carries the interleaved channel parameter:

    Transport: RTP/AVP;unicast;interleaved=0-1

Per RFC 2326 the lower transport defaults to UDP, so stock ffmpeg parses this
as a UDP reply and fails the handshake with "Nonmatching transport in server
reply", even though the reply is functionally a valid TCP-interleaved answer
(VLC and other RTSP clients accept it). The patch makes `rtsp_parse_transport`
treat an RTP reply with an `interleaved=` parameter and no explicit lower
transport as TCP — `interleaved` is only meaningful on TCP, so this cannot
misclassify a genuine UDP reply.

Verify after each FFmpeg version bump that the hunks still apply. Both apply
steps use `-N` (never reverse-apply a patch that looks already applied - macOS's
`patch` otherwise answers its own "Assume -R? [y]" prompt and removes the change)
and fail the build unless the patch applies cleanly and a reverse dry run shows
it fully in place.

## 0003-d3d12va-share-command-queues.patch

NVIDIA's Windows driver (seen on 32.0.16.2124, RTX 5080) caps the D3D12 video
command queues one process may create: about 50 `VIDEO_DECODE` queues, about 64
decode + `VIDEO_PROCESS` together, with `COPY` queues taking a little of the same
budget. Creating one more doesn't fail the call - it removes the device
(`DXGI_ERROR_DEVICE_REMOVED`, reason `DXGI_ERROR_DRIVER_INTERNAL_ERROR`), so
every D3D12 decoder and filter in the process dies at once. Reproduced with
plain D3D12 calls, no FFmpeg. AMD showed no limit up to 200 of each.

Stock FFmpeg creates a queue per decoder (`VIDEO_DECODE`), per frames context
(`COPY`, for downloads) and per `scale_d3d12` instance (`VIDEO_PROCESS`), so a
process hit the cap at ~47 decoders, or ~31 with GPU scaling.

The patch adds `avpriv_d3d12va_get_command_queue()` to libavutil: decode,
process and copy queues come from a round-robin pool of 8 per type, shared by
every device context on the same `ID3D12Device` (D3D12CreateDevice returns one
device per adapter per process, so separately created device contexts share
too). Queues are free-threaded; each user keeps its own command lists,
allocators and fences. A pool is released with the last device context using
its device, so a removed device isn't kept alive. The three call sites
(`d3d12va_decode.c`, `hwcontext_d3d12va.c` helper objects, `vf_scale_d3d12.c`)
take their queue from it. Not upstream (yet).

## Dropped: 0002-rtsp-limit-the-number-of-redirects.patch

Was a backport of FFmpeg master `f5d4fd186c` ("avformat/rtsp: limit the number
of redirects", 8 redirects then `AVERROR(EIO)`) for 9.0.2. FFmpeg 9.1 ships the
same change, so the file was removed when the build moved to 9.1 (applying it
again made `patch` ignore both hunks and fail the build).
