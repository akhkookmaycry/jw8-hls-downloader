#!/usr/bin/env python3
"""
Fast HLS downloader – parallel segment downloading with ffmpeg remux.
"""

import sys
import os
import re
import urllib.request
import urllib.parse
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time

def debug(msg):
    print(f"[FAST-DL] {msg}", file=sys.stderr)

def get_opener():
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    if use_warp:
        proxy = 'socks5://127.0.0.1:1080'
        debug(f"Using WARP proxy: {proxy}")
        handler = urllib.request.ProxyHandler({'socks5': proxy})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()

def download_segment(url, output_path, referer, timeout=30):
    """Download a single segment, return bytes or None on failure."""
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = resp.read()
            with open(output_path, 'wb') as f:
                f.write(data)
            return len(data)
    except Exception as e:
        debug(f"Failed to download {url}: {e}")
        return None

def parse_m3u8_playlist(playlist_url, referer):
    """Fetch and parse an m3u8 playlist, returning list of segment URLs."""
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(playlist_url, headers=headers)
    try:
        with opener.open(req, timeout=20) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Failed to fetch playlist {playlist_url}: {e}")
        return None

    # If it's a master playlist, pick the highest bandwidth variant
    if '#EXT-X-STREAM-INF' in content:
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
                            full = urllib.parse.urljoin(playlist_url, variant)
                            if bw > best_bw:
                                best_bw = bw
                                best_url = full
        if best_url:
            debug(f"Selected best variant: {best_url} (bandwidth {best_bw})")
            return parse_m3u8_playlist(best_url, referer)
        else:
            debug("No variant found in master playlist, using original")
            return None

    # Media playlist – extract segment URLs
    segment_urls = []
    base_url = playlist_url.rsplit('/', 1)[0] + '/'
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            # Resolve relative URLs
            if line.startswith('http'):
                seg_url = line
            else:
                seg_url = urllib.parse.urljoin(base_url, line)
            segment_urls.append(seg_url)
    debug(f"Found {len(segment_urls)} segments")
    return segment_urls

def download_hls_parallel(m3u8_url, output_path, referer, max_workers=10):
    """Download all segments in parallel, then remux with ffmpeg."""
    debug("Parsing HLS playlist...")
    segments = parse_m3u8_playlist(m3u8_url, referer)
    if not segments:
        debug("Failed to parse playlist or no segments found")
        return False

    # Create temp directory for segments
    with tempfile.TemporaryDirectory() as tmpdir:
        seg_dir = Path(tmpdir)
        seg_files = []
        # Prepare download tasks: (url, output_path)
        tasks = []
        for i, seg_url in enumerate(segments):
            seg_file = seg_dir / f"seg_{i:05d}.ts"
            seg_files.append(seg_file)
            tasks.append((seg_url, seg_file))

        debug(f"Downloading {len(tasks)} segments with {max_workers} parallel connections...")
        downloaded = 0
        failed = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(download_segment, url, seg_file, referer): seg_file
                for url, seg_file in tasks
            }
            for future in as_completed(futures):
                seg_file = futures[future]
                try:
                    size = future.result()
                    if size:
                        downloaded += 1
                    else:
                        failed += 1
                    # Progress report
                    percent = (downloaded + failed) / len(tasks) * 100
                    elapsed = time.time() - start_time
                    speed = (downloaded * 1024 * 1024) / elapsed if elapsed > 0 else 0  # rough MB/s
                    print(f"\r  Progress: {downloaded}/{len(tasks)} segments downloaded, {failed} failed, {percent:.1f}% | {speed:.1f} MB/s", end="", flush=True)
                except Exception as e:
                    debug(f"\nSegment {seg_file} raised exception: {e}")
                    failed += 1

        print()  # newline after progress
        if downloaded == 0:
            debug("No segments downloaded successfully")
            return False

        # Check if all segments exist
        missing = [f for f in seg_files if not f.exists() or f.stat().st_size == 0]
        if missing:
            debug(f"Warning: {len(missing)} segments missing or empty, may cause broken video")

        # Create a file list for ffmpeg concat
        concat_list = seg_dir / "concat.txt"
        with open(concat_list, 'w') as f:
            for seg_file in sorted(seg_files):
                if seg_file.exists() and seg_file.stat().st_size > 0:
                    # ffmpeg concat needs escaped paths
                    f.write(f"file '{seg_file.as_posix()}'\n")

        # Remux with ffmpeg
        debug("Remuxing segments to MP4...")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            debug(f"ffmpeg concat failed: {result.stderr[:500]}")
            return False

        if output_path.exists() and output_path.stat().st_size > 0:
            debug(f"Successfully created {output_path}")
            return True
        else:
            debug("Output file missing or empty after remux")
            return False

def main():
    if len(sys.argv) < 3:
        print("Usage: hls-downloader.py <url> <output_name> [referer]")
        sys.exit(1)
    url = sys.argv[1]
    output_name = sys.argv[2]
    referer = sys.argv[3] if len(sys.argv) > 3 else ""

    debug(f"=== Fast HLS Downloader ===")
    debug(f"URL: {url}")
    debug(f"Output: {output_name}")
    debug(f"Referer: {referer}")

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / output_name

    # Check if it's HLS
    is_hls = url.endswith('.m3u8')
    if not is_hls:
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
        print("Downloading HLS stream with parallel segment fetcher...")
        success = download_hls_parallel(url, out_path, referer, max_workers=15)
        if not success:
            debug("Parallel download failed, falling back to ffmpeg direct")
            # Fallback to original ffmpeg method (optional)
            from original_hls_downloader import download_direct_ffmpeg  # or inline
            # For simplicity, we'll just exit here; you can copy the ffmpeg fallback from your script
            sys.exit(1)
    else:
        print("Downloading direct file... (no parallel optimization)")
        # Use your existing direct download function
        success = download_file_direct(url, out_path, referer)  # you need to define this or import
        if not success:
            sys.exit(1)

    if out_path.exists() and out_path.stat().st_size > 0:
        size = out_path.stat().st_size / (1024*1024)
        print(f"\n✅ SUCCESS: {out_path} ({size:.2f} MB)")
        sys.exit(0)
    else:
        debug("Output file missing or empty")
        sys.exit(1)

def download_file_direct(url, output_path, referer):
    """Simple direct download with progress (copy from your existing function)."""
    # Placeholder – use your existing download_file function
    # For completeness, here’s a minimal version:
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=60) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            last_percent = 0
            with open(output_path, 'wb') as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = (downloaded / total) * 100
                        if int(percent) > last_percent:
                            last_percent = int(percent)
                            if last_percent % 10 == 0 or last_percent == 100:
                                print(f"  Progress: {percent:.1f}% ({downloaded//1024} KB / {total//1024} KB)", flush=True)
        return True
    except Exception as e:
        debug(f"Direct download failed: {e}")
        return False

if __name__ == "__main__":
    main()