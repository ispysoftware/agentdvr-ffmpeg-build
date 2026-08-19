#!/usr/bin/env python3
"""
publish_cdn.py -- Publish the ffmpeg archives from a GitHub release to the CDN.

Downloads the six platform archives from the v<version> release of
ispysoftware/agentdvr-ffmpeg-build and uploads them to Cloudflare R2 under
libs/ -- the exact keys Agent DVR's FindFFmpeg downloader requests first
(files.ispyconnect.com/libs/..., GitHub release is its fallback mirror).

Zero-dependency: stdlib-only SigV4 PUT with UNSIGNED-PAYLOAD, same approach as
iSpyConnect/sources-cleanup/scripts/r2_distribute.py and GCodeUploader/S3Uploader.cs.

Config: cdn_config.json next to this script (gitignored -- holds R2 secrets):
  { "r2": { "service_url": "https://<account>.r2.cloudflarestorage.com",
            "bucket": "agentfiles",
            "access_key_id": "...", "secret_access_key": "..." } }
Or point --config at sources-cleanup/scripts/sources_daily.config.json, which
has the same shape.

Usage:
  python publish_cdn.py                 # version from ffmpeg_version.txt, prompts before upload
  python publish_cdn.py --version 9.0.1
  python publish_cdn.py --yes           # no prompt (scripted use)
  python publish_cdn.py --config path\to\config.json

Note: files.ispyconnect.com sits behind Cloudflare. If edge caching is enabled
for /libs/, replaced files may serve stale until the cache is purged -- the
post-upload verification HEADs the public URL and warns on a size mismatch.
"""
import argparse, datetime, hashlib, hmac, json, os, ssl, sys, urllib.error, urllib.request
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
RELEASE_URL = "https://github.com/ispysoftware/agentdvr-ffmpeg-build/releases/download"
PUBLIC_URL = "https://files.ispyconnect.com/libs"


def assets(ver):
    """The archive names FindFFmpeg requests (flat under libs/), with content types.

    Two variants per platform: "" = gpl (historical names, with libx264/libx265)
    and "-lgpl" (no GPL components; the app's default from the LGPL split on).
    """
    out = []
    for vtag in ("", "-lgpl"):
        out += [
            (f"ffmpeg{ver}{vtag}-linux-armhf.tar.xz", "application/x-xz"),
            (f"ffmpeg{ver}{vtag}-linux-arm64.tar.xz", "application/x-xz"),
            (f"ffmpeg{ver}{vtag}-linux-x86_64.tar.xz", "application/x-xz"),
            (f"ffmpeg{ver}{vtag}-windows-x64.zip", "application/zip"),
            (f"ffmpeg{ver}{vtag}-macos-arm64-notarized.zip", "application/zip"),
            (f"ffmpeg{ver}{vtag}-macos-x86_64-notarized.zip", "application/zip"),
        ]
    return out


def _sign(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
def _sigkey(secret, ds, region, svc):
    return _sign(_sign(_sign(_sign(("AWS4" + secret).encode(), ds), region), svc), "aws4_request")


def upload(r2, file_path, key, content_type, timeout=300):
    service_url = r2["service_url"].rstrip("/")
    bucket = r2["bucket"]
    ak = r2["access_key_id"]; sk = r2["secret_access_key"]; region = r2.get("region", "auto")
    host = service_url.split("://", 1)[1]
    cu = "/" + quote(bucket) + "/" + "/".join(quote(s) for s in key.split("/"))
    now = datetime.datetime.utcnow(); amz = now.strftime("%Y%m%dT%H%M%SZ"); ds = now.strftime("%Y%m%d")
    ph = "UNSIGNED-PAYLOAD"
    ch = f"content-type:{content_type}\nhost:{host}\nx-amz-content-sha256:{ph}\nx-amz-date:{amz}\n"
    sh = "content-type;host;x-amz-content-sha256;x-amz-date"
    creq = f"PUT\n{cu}\n\n{ch}\n{sh}\n{ph}"
    scope = f"{ds}/{region}/s3/aws4_request"
    sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n" + hashlib.sha256(creq.encode()).hexdigest()
    sig = hmac.new(_sigkey(sk, ds, region, "s3"), sts.encode(), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={sh}, Signature={sig}"
    with open(file_path, "rb") as f: data = f.read()
    req = urllib.request.Request(service_url + cu, data=data, method="PUT", headers={
        "Host": host, "Content-Type": content_type, "x-amz-date": amz,
        "x-amz-content-sha256": ph, "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            return resp.status, resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"R2 upload failed {e.code}: {e.read()[:300].decode(errors='replace')}")


def download(url, dest, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "publish_cdn"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk: break
            f.write(chunk)
    return os.path.getsize(dest)


def purge_cloudflare(zone_id, api_token, urls, timeout=60):
    """Purge specific URLs from the Cloudflare edge cache (works on all plans)."""
    body = json.dumps({"files": urls}).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {api_token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare purge failed: {result.get('errors')}")


def probe_public(url, timeout=60):
    """Ranged GET through the public URL — same cache path real downloaders hit.
    (HEAD is useless here: Cloudflare can serve HEAD from origin while GET
    serves stale cached bytes, which made verification pass on a stale edge.)
    Returns (total_size, first_bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": "publish_cdn",
                                               "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            crange = resp.headers.get("Content-Range", "")  # "bytes 0-1023/18656649"
            total = int(crange.rsplit("/", 1)[1]) if "/" in crange else -1
            return total, data
    except Exception:
        return -1, b""


def main():
    ap = argparse.ArgumentParser(description="Publish ffmpeg release archives to the CDN (R2 libs/)")
    ap.add_argument("--version", default=None, help="ffmpeg version (default: ffmpeg_version.txt)")
    ap.add_argument("--config", default=os.path.join(HERE, "cdn_config.json"),
                    help="JSON config with r2 credentials (default: cdn_config.json next to this script)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    ver = args.version
    if not ver:
        with open(os.path.join(HERE, "ffmpeg_version.txt"), encoding="utf-8") as f:
            ver = f.read().strip()

    # Credentials: env vars (CI secrets) take precedence over the config file.
    if os.environ.get("R2_ACCESS_KEY_ID"):
        r2 = {
            "service_url": os.environ.get("R2_SERVICE_URL", ""),
            "bucket": os.environ.get("R2_BUCKET", "agentfiles"),
            "access_key_id": os.environ["R2_ACCESS_KEY_ID"],
            "secret_access_key": os.environ.get("R2_SECRET_ACCESS_KEY", ""),
        }
        if not r2["service_url"] or not r2["secret_access_key"]:
            sys.exit("ERROR: R2_SERVICE_URL and R2_SECRET_ACCESS_KEY must be set "
                     "alongside R2_ACCESS_KEY_ID.")
    elif not os.path.isfile(args.config):
        sys.exit(f"ERROR: config not found: {args.config}\n"
                 "Create cdn_config.json (see module docstring), pass --config, "
                 "or set R2_* environment variables.")
    else:
        with open(args.config, encoding="utf-8") as f:
            r2 = json.load(f)["r2"]

    work = os.path.join(HERE, "out", "cdn")
    os.makedirs(work, exist_ok=True)

    # Download everything first -- fail before touching the CDN if any asset is missing.
    files = []
    print(f"==> Fetching v{ver} release assets")
    for name, ctype in assets(ver):
        dest = os.path.join(work, name)
        try:
            size = download(f"{RELEASE_URL}/v{ver}/{name}", dest)
        except urllib.error.HTTPError as e:
            sys.exit(f"ERROR: {name}: HTTP {e.code} -- is it on the release? "
                     "(macOS -notarized zips are produced by notarize_macos.sh, not CI)")
        print(f"    {name}  {size / (1 << 20):.1f} MB")
        files.append((dest, name, ctype, size))

    if not args.yes:
        reply = input(f"Upload {len(files)} files to {r2['bucket']}/libs/ (replaces existing)? [Y/n]: ")
        if reply.strip().lower() == "n":
            sys.exit("aborted")

    print("==> Uploading to R2")
    for dest, name, ctype, size in files:
        status, etag = upload(r2, dest, f"libs/{name}", ctype)
        print(f"    libs/{name}  status={status} etag={etag}")

    # Purge the replaced URLs from the Cloudflare edge cache so the verification
    # below (and every downloader) sees the new files immediately. Optional:
    # skipped when CF_ZONE_ID / CF_API_TOKEN (or config keys) are absent.
    cf_zone = os.environ.get("CF_ZONE_ID") or r2.get("cf_zone_id", "")
    cf_token = os.environ.get("CF_API_TOKEN") or r2.get("cf_api_token", "")
    if cf_zone and cf_token:
        print("==> Purging Cloudflare edge cache")
        purge_cloudflare(cf_zone, cf_token, [f"{PUBLIC_URL}/{name}" for _, name, _, _ in files])
        print("    purged", len(files), "URLs")
    else:
        print("==> Skipping edge cache purge (CF_ZONE_ID / CF_API_TOKEN not set)")

    print("==> Verifying via public URL (ranged GET through the edge cache)")
    stale = False
    for dest, name, _, size in files:
        public, first = probe_public(f"{PUBLIC_URL}/{name}")
        with open(dest, "rb") as f:
            local_first = f.read(len(first) or 1024)
        ok = public == size and first == local_first[:len(first)] and len(first) > 0
        stale |= not ok
        print(f"    {name}  cdn={public} local={size}  {'OK' if ok else 'STALE/MISMATCH'}")
    if stale:
        sys.exit("ERROR: public URL serving stale or wrong bytes -- purge the "
                 "Cloudflare cache for files.ispyconnect.com/libs/* and re-run. "
                 "(Set CF_ZONE_ID / CF_API_TOKEN to purge automatically.)")
    print("Done.")


if __name__ == "__main__":
    main()
