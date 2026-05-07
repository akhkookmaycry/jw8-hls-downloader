#!/usr/bin/env python3
"""
Universal HLS downloader – uses ffmpeg directly for reliability,
or falls back to manual segment download with correct file types.
"""

import sys
import os
import re
import urllib.request
import subprocess
import time
from pathlib import Path

def debug(msg):
    print(f"[DOWNLOAD-DEBUG] {msg}", file=sys.stderr)

def get_opener():
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    if use_warp:
        proxy = 'socks5://127.0.0.1:1080'
        debug(f"Using WARP proxy: {proxy}")
        handler = urllib.request.ProxyHandler({'socks5': proxy})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()

def download_direct_ffmpeg(m3u8_url, output_path, referer):
    """Use ffmpeg to download the stream directly (handles fMP4, segments, etc.)"""
    cmd = [
        "ffmpeg", "-y",
        "-headers", f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0\r\n",
        "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        str(output_path)
    ]
    debug(f"Running ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return True
    debug(f"ffmpeg direct failed (code {result.returncode}): {result.stderr[:500]}")
    return False

def download_file(url, output_path, referer):
    """Direct download for non-HLS files"""
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
    debug(f"Downloading direct: {url}")
    try:
        with opener.open(req, timeout=60) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            with open(output_path, 'wb') as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = (downloaded / total) * 100
                        if int(percent) % 10 == 0:
                            debug(f"Progress: {percent:.1f}%")
        debug(f"Downloaded {downloaded} bytes to {output_path}")
        return True
    except Exception as e:
        debug(f"Direct download failed: {e}")
        return False

def download_hls_manual(m3u8_url, output_path, referer):
    """Fallback: manual segment download (supports both .ts and .mp4 fragments)"""
    # First get the media playlist (follow variant if master)
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(m3u8_url, headers=headers)
    try:
        with opener.open(req, timeout=30) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Failed to fetch M3U8: {e}")
        return False

    # Check if it's a master playlist
    media_url = m3u8_url
    if '#EXT-X-STREAM-INF' in content:
        debug("Master playlist detected, finding best quality...")
        best_bw = -1
        best_url = None
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith('#EXT-X-STREAM-INF'):
                bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                if bw_match:
                    bw = int(bw_match.group(1))
                    if i+1 < len(lines):
                        variant = lines[i+1].strip()
                        if variant and not variant.startswith('#'):
                            full = urllib.parse.urljoin(m3u8_url, variant)
                            debug(f"Variant {bw}: {full}")
                            if bw > best_bw:
                                best_bw = bw
                                best_url = full
        if best_url:
            media_url = best_url
            debug(f"Selected quality (bandwidth {best_bw}): {media_url}")
        else:
            debug("No variant found, using original URL")

    # Fetch media playlist
    try:
        req = urllib.request.Request(media_url, headers=headers)
        with opener.open(req, timeout=30) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Failed to fetch media playlist: {e}")
        return False

    # Parse segment URLs and determine extension
    base_url = media_url.rsplit('/', 1)[0] + '/'
    seg_urls = []
    seg_ext = None
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            full = urllib.parse.urljoin(base_url, line)
            seg_urls.append(full)
            if not seg_ext:
                # Guess extension from URL
                if '.ts' in full:
                    seg_ext = '.ts'
                elif '.mp4' in full:
                    seg_ext = '.mp4'
                elif '.m4s' in full:
                    seg_ext = '.m4s'
    if not seg_ext:
        seg_ext = '.ts'  # default
    debug(f"Found {len(seg_urls)} segments, extension: {seg_ext}")

    # Download segments
    seg_dir = Path("segments")
    seg_dir.mkdir(exist_ok=True)
    success = 0
    for i, url in enumerate(seg_urls):
        out_path = seg_dir / f"seg_{i:04d}{seg_ext}"
        debug(f"Downloading segment {i}: {url}")
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=60) as resp:
                data = resp.read()
                if len(data) < 1000:
                    debug(f"Segment {i} too small ({len(data)} bytes), retrying...")
                    time.sleep(1)
                    # Retry once
                    with opener.open(req, timeout=60) as resp2:
                        data = resp2.read()
                with open(out_path, 'wb') as f:
                    f.write(data)
                debug(f"Segment {i} saved ({len(data)} bytes)")
                success += 1
        except Exception as e:
            debug(f"Failed segment {i}: {e}")

    if success < len(seg_urls):
        debug(f"Only {success}/{len(seg_urls)} segments downloaded")
        return False

    # Create filelist for ffmpeg
    filelist = seg_dir / "filelist.txt"
    with open(filelist, 'w') as f:
        for i in range(len(seg_urls)):
            seg_path = seg_dir / f"seg_{i:04d}{seg_ext}"
            if seg_ext == '.mp4':
                # For MP4 fragments, we may need to use concat demuxer with -c copy
                f.write(f"file '{seg_path.resolve()}'\n")
            else:
                f.write(f"file '{seg_path.resolve()}'\n")

    # Merge with ffmpeg
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(filelist),
        "-c", "copy",
        str(output_path)
    ]
    debug(f"Merging: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        debug(f"ffmpeg merge error: {result.stderr}")
        return False

    # Cleanup
    for i in range(len(seg_urls)):
        (seg_dir / f"seg_{i:04d}{seg_ext}").unlink(missing_ok=True)
    seg_dir.rmdir()
    return True

def main():
    if len(sys.argv) < 3:
        print("Usage: hls-downloader.py <url> <output_name> [referer]")
        sys.exit(1)
    url = sys.argv[1]
    output_name = sys.argv[2]
    referer = sys.argv[3] if len(sys.argv) > 3 else ""

    debug(f"=== Universal Downloader ===")
    debug(f"URL: {url}")
    debug(f"Output: {output_name}")
    debug(f"Referer: {referer}")

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / output_name

    # Determine if it's HLS (m3u8) or direct file
    is_hls = url.endswith('.m3u8')
    if not is_hls:
        # Try to peek at content
        try:
            opener = get_opener()
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': referer})
            with opener.open(req, timeout=10) as resp:
                first_kb = resp.read(1024).decode('utf-8', errors='ignore')
                if '#EXTM3U' in first_kb:
                    is_hls = True
        except:
            pass

    if is_hls:
        # First try ffmpeg direct (most reliable)
        debug("Attempting ffmpeg direct download...")
        if download_direct_ffmpeg(url, out_path, referer):
            debug("ffmpeg direct succeeded")
        else:
            debug("ffmpeg direct failed, falling back to manual segment download")
            if not download_hls_manual(url, out_path, referer):
                debug("Manual HLS download failed")
                sys.exit(1)
    else:
        if not download_file(url, out_path, referer):
            debug("Direct download failed")
            sys.exit(1)

    if out_path.exists() and out_path.stat().st_size > 0:
        size = out_path.stat().st_size / (1024*1024)
        debug(f"SUCCESS: {out_path} ({size:.2f} MB)")
        sys.exit(0)
    else:
        debug("Output file missing or empty")
        sys.exit(1)

if __name__ == "__main__":
    main()
