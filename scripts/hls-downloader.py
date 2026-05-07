#!/usr/bin/env python3
"""
Universal downloader: HLS streams (with highest quality selection) or regular video files.
Supports optional SOCKS5 proxy (WARP).
"""

import sys
import os
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import time
from pathlib import Path

def debug(msg):
    print(f"[DOWNLOAD-DEBUG] {msg}", file=sys.stderr)

def get_proxy_opener():
    """Return URL opener with SOCKS5 proxy if USE_WARP is set."""
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    if use_warp:
        proxy = 'socks5://127.0.0.1:1080'
        debug(f"Using WARP proxy: {proxy}")
        proxy_handler = urllib.request.ProxyHandler({'socks5': proxy})
        return urllib.request.build_opener(proxy_handler)
    else:
        debug("Using direct connection (no proxy)")
        return urllib.request.build_opener()

def fetch_playlist(url, referer):
    debug(f"Fetching: {url[:100]}")
    opener = get_proxy_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Fetch failed: {e}")
        raise

def get_best_media_playlist(master_url, referer):
    """Parse master playlist, return URL of variant with highest BANDWIDTH."""
    content = fetch_playlist(master_url, referer)
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
                        full_url = urllib.parse.urljoin(master_url, variant)
                        debug(f"Variant {bw} bps: {full_url}")
                        if bw > best_bw:
                            best_bw = bw
                            best_url = full_url
    if best_url:
        debug(f"Selected highest quality: {best_url} (bandwidth {best_bw})")
        return best_url
    debug("No variants found – assuming input is media playlist")
    return master_url

def download_segment(seg_url, out_path, referer, retries=3):
    opener = get_proxy_opener()
    for attempt in range(retries):
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
            req = urllib.request.Request(seg_url, headers=headers)
            with opener.open(req, timeout=60) as resp:
                data = resp.read()
                with open(out_path, 'wb') as f:
                    f.write(data)
                debug(f"Downloaded {out_path.name} ({len(data)} bytes)")
                return True
        except Exception as e:
            debug(f"Attempt {attempt+1} failed for {out_path.name}: {e}")
            time.sleep(2)
    debug(f"Giving up on {out_path.name}")
    return False

def download_direct_file(url, output_path, referer):
    """Download a single file (non-HLS) with resume support."""
    debug(f"Downloading direct file: {url}")
    opener = get_proxy_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
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
            debug(f"Download complete: {output_path} ({downloaded} bytes)")
            return True
    except Exception as e:
        debug(f"Direct download failed: {e}")
        return False

def is_hls_playlist(url, referer):
    """Quick check if URL is an HLS playlist (contains #EXTM3U)."""
    try:
        content = fetch_playlist(url, referer)
        return '#EXTM3U' in content
    except:
        return False

def main():
    if len(sys.argv) < 3:
        print("Usage: hls-downloader.py <url> <output_file> [referer]")
        sys.exit(1)

    url = sys.argv[1]
    output_name = sys.argv[2]
    referer = sys.argv[3] if len(sys.argv) > 3 else ""

    debug(f"=== Universal Downloader ===")
    debug(f"URL: {url}")
    debug(f"Output: {output_name}")
    debug(f"Referer: {referer}")

    # Detect if it's an HLS stream or direct file
    if url.lower().endswith(('.mp4', '.webm', '.mkv', '.avi', '.mov', '.ts')):
        debug("Extension suggests direct video file")
        is_hls = False
    else:
        debug("Checking if URL is an HLS playlist...")
        try:
            is_hls = is_hls_playlist(url, referer)
            debug(f"HLS detected: {is_hls}")
        except:
            debug("Could not determine, assuming direct file")
            is_hls = False

    if not is_hls:
        # Direct file download
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        out_path = output_dir / output_name
        if download_direct_file(url, str(out_path), referer):
            debug(f"SUCCESS: File saved to {out_path}")
            sys.exit(0)
        else:
            debug("ERROR: Direct download failed")
            sys.exit(1)

    # --- HLS download path ---
    debug("Processing as HLS stream")
    media_url = get_best_media_playlist(url, referer)
    debug(f"Media playlist URL: {media_url}")

    media_content = fetch_playlist(media_url, referer)
    base_url = media_url.rsplit('/', 1)[0] + '/'
    seg_urls = []
    for line in media_content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            full = urllib.parse.urljoin(base_url, line)
            seg_urls.append(full)

    debug(f"Found {len(seg_urls)} segments")
    for i, u in enumerate(seg_urls[:5]):
        debug(f"  seg {i:04d}: {u[:80]}")

    seg_dir = Path("segments")
    seg_dir.mkdir(exist_ok=True)
    tasks = []
    for idx, seg_url in enumerate(seg_urls):
        out_path = seg_dir / f"seg_{idx:04d}.ts"
        tasks.append((seg_url, out_path, referer))

    debug(f"Starting parallel download (max 10 workers)...")
    successful = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(download_segment, url, path, referer): path for url, path, _ in tasks}
        for fut in as_completed(futures):
            if fut.result():
                successful += 1
            if successful % 10 == 0:
                debug(f"Progress: {successful}/{len(seg_urls)}")

    debug(f"Downloaded {successful} of {len(seg_urls)} segments")
    if successful < len(seg_urls):
        debug("ERROR: Not all segments downloaded")
        sys.exit(1)

    # Merge with ffmpeg
    filelist = seg_dir / "filelist.txt"
    with open(filelist, 'w') as f:
        for idx in range(len(seg_urls)):
            seg_path = seg_dir / f"seg_{idx:04d}.ts"
            if seg_path.exists():
                f.write(f"file '{seg_path.resolve()}'\n")

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / output_name

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(filelist),
        "-c", "copy",
        str(output_path)
    ]
    debug(f"Running ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        debug(f"ffmpeg error (code {result.returncode}):")
        debug(f"STDERR: {result.stderr}")
        sys.exit(1)

    # Cleanup
    for idx in range(len(seg_urls)):
        seg_path = seg_dir / f"seg_{idx:04d}.ts"
        if seg_path.exists():
            seg_path.unlink()
    seg_dir.rmdir()

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        debug(f"SUCCESS: Output file {output_path} ({size_mb:.2f} MB)")
    else:
        debug("ERROR: Output file not created")
        sys.exit(1)

if __name__ == "__main__":
    main()
