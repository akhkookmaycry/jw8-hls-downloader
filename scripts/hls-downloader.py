#!/usr/bin/env python3
"""
Universal downloader – HLS (with quality selection) or direct video.
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

def get_opener():
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    if use_warp:
        proxy = 'socks5://127.0.0.1:1080'
        debug(f"Using WARP proxy: {proxy}")
        handler = urllib.request.ProxyHandler({'socks5': proxy})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()

def fetch(url, referer):
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=30) as resp:
        return resp.read().decode('utf-8')

def get_best_media_playlist(master_url, referer):
    content = fetch(master_url, referer)
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
                        full = urllib.parse.urljoin(master_url, variant)
                        debug(f"Variant {bw}: {full}")
                        if bw > best_bw:
                            best_bw = bw
                            best_url = full
    return best_url if best_url else master_url

def download_file(url, output_path, referer):
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
    debug(f"Downloading direct: {url}")
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

def download_segment(url, path, referer, retries=3):
    opener = get_opener()
    for attempt in range(retries):
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=60) as resp:
                data = resp.read()
                with open(path, 'wb') as f:
                    f.write(data)
                return True
        except Exception as e:
            debug(f"Segment download attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return False

def download_hls(master_url, output_name, referer):
    media_url = get_best_media_playlist(master_url, referer)
    debug(f"Media playlist: {media_url}")
    content = fetch(media_url, referer)
    base = media_url.rsplit('/', 1)[0] + '/'
    segs = []
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            segs.append(urllib.parse.urljoin(base, line))
    debug(f"Found {len(segs)} segments")
    seg_dir = Path("segments")
    seg_dir.mkdir(exist_ok=True)
    tasks = [(i, url, seg_dir / f"seg_{i:04d}.ts") for i, url in enumerate(segs)]
    success = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(download_segment, url, path, referer): i for i, url, path in tasks}
        for fut in as_completed(futures):
            if fut.result():
                success += 1
                if success % 50 == 0:
                    debug(f"Segments: {success}/{len(segs)}")
    debug(f"Downloaded {success}/{len(segs)} segments")
    if success < len(segs):
        debug("Not all segments downloaded")
        return False
    # Merge
    filelist = seg_dir / "filelist.txt"
    with open(filelist, 'w') as f:
        for i, _, path in tasks:
            if path.exists():
                f.write(f"file '{path.resolve()}'\n")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out = out_dir / output_name
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(filelist), "-c", "copy", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        debug(f"ffmpeg error: {result.stderr}")
        return False
    # Cleanup
    for _, _, path in tasks:
        path.unlink(missing_ok=True)
    seg_dir.rmdir()
    debug(f"Merged to {out}")
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

    # Detect if HLS
    is_hls = False
    try:
        if url.endswith('.m3u8') or '#EXTM3U' in fetch(url, referer)[:1000]:
            is_hls = True
    except:
        pass

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / output_name

    if is_hls:
        success = download_hls(url, output_name, referer)
    else:
        success = download_file(url, out_path, referer)

    if success and out_path.exists():
        size = out_path.stat().st_size / (1024*1024)
        debug(f"SUCCESS: {out_path} ({size:.2f} MB)")
        sys.exit(0)
    else:
        debug("Download failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
