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

Verify after each FFmpeg version bump that the hunks still apply (the build
fails loudly if they don't).

## 0002-rtsp-limit-the-number-of-redirects.patch

Backport of FFmpeg master `f5d4fd186c` ("avformat/rtsp: limit the number of
redirects"). `ff_rtsp_connect` follows every 3xx reply with `goto redirect`
and no counter, so a rogue or misconfigured RTSP server that keeps
redirecting (to itself, or between two URLs) holds the connect call in an
endless loop and the camera source never starts or fails. The patch gives up
with `AVERROR(EIO)` after 8 redirects. It is not on `release/9.0` as of
n9.0.2; drop this file once the pinned FFmpeg release contains it.

The hunk offsets assume 0001 has been applied first.
