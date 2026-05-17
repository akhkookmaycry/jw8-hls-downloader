#!/usr/bin/env python3
"""
Fast HLS downloader – uses yt-dlp (parallel) or custom parallel fMP4 downloader.
No slow ffmpeg sequential fallback.
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
import shutil

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
    opener = get_opener()
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    req = urllib.request.Request(playlist_url, headers=headers)
    try:
        with opener.open(req, timeout=20) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Failed to fetch playlist {playlist_url}: {e}")
        return None, None

    # Master playlist → select highest bandwidth variant
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
            debug(f"Selected variant: {best_url} (bandwidth {best_bw})")
            return parse_m3u8_playlist(best_url, referer)
        else:
            debug("No variant found, trying original")
            return None, None

    # Media playlist – extract init segment (EXT-X-MAP) and media segments
    init_seg = None
    init_byte_range = None
    segment_urls = []
    base_url = playlist_url.rsplit('/', 1)[0] + '/'
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('#EXT-X-MAP'):
            # Example: #EXT-X-MAP:URI="init.mp4",BYTERANGE="0-1234"
            uri_match = re.search(r'URI="([^"]+)"', line)
            if uri_match:
                uri = uri_match.group(1)
                if not uri.startswith('http'):
                    uri = urllib.parse.urljoin(base_url, uri)
                init_seg = uri
            br_match = re.search(r'BYTERANGE="([0-9]+)(?:@([0-9]+))?"', line)
            if br_match:
                length = int(br_match.group(1))
                offset = int(br_match.group(2)) if br_match.group(2) else 0
                init_byte_range = (offset, length)
        elif line and not line.startswith('#'):
            # segment URI
            if line.startswith('http'):
                seg_url = line
            else:
                seg_url = urllib.parse.urljoin(base_url, line)
            segment_urls.append(seg_url)

    debug(f"Init segment: {init_seg if init_seg else 'none'}")
    debug(f"Found {len(segment_urls)} media segments")
    return init_seg, segment_urls

def download_fmp4_parallel(m3u8_url, output_path, referer, max_workers=15):
    """
    Custom parallel downloader for fMP4 HLS (with EXT-X-MAP).
    Downloads init segment and all media fragments, then uses ffmpeg to combine them.
    """
    init_seg, segments = parse_m3u8_playlist(m3u8_url, referer)
    if not segments:
        debug("No segments found")
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Download init segment
        init_file = None
        if init_seg:
            init_file = tmp / "init.mp4"
            debug(f"Downloading init segment: {init_seg}")
            size = download_segment(init_seg, init_file, referer)
            if not size:
                debug("Failed to download init segment")
                return False

        # Download media fragments in parallel
        seg_files = []
        tasks = []
        for i, seg_url in enumerate(segments):
            seg_file = tmp / f"frag_{i:05d}.m4s"
            seg_files.append(seg_file)
            tasks.append((seg_url, seg_file))

        debug(f"Downloading {len(tasks)} fragments with {max_workers} connections...")
        downloaded = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(download_segment, url, seg_file, referer): seg_file for url, seg_file in tasks}
            for future in as_completed(futures):
                seg_file = futures[future]
                try:
                    size = future.result()
                    if size:
                        downloaded += 1
                    percent = (downloaded / len(tasks)) * 100
                    print(f"\r  Progress: {downloaded}/{len(tasks)} fragments ({percent:.1f}%)", end="", flush=True)
                except Exception as e:
                    debug(f"\nFragment {seg_file} error: {e}")
        print()

        if downloaded != len(tasks):
            debug("Some fragments failed, aborting")
            return False

        # Combine using ffmpeg: init + fragments in order
        # Create a concat file list (the init file followed by all fragments)
        concat_list = tmp / "concat.txt"
        with open(concat_list, 'w') as f:
            if init_file:
                f.write(f"file '{init_file.absolute().as_posix()}'\n")
            for seg_file in sorted(seg_files):
                f.write(f"file '{seg_file.absolute().as_posix()}'\n")

        # Use ffmpeg concat demuxer (works for fMP4 fragments when init is first)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(output_path)
        ]
        debug(f"Running ffmpeg concat: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            debug("FFmpeg concat succeeded")
            return True
        else:
            debug(f"FFmpeg concat failed: {result.stderr[-300:]}")
            return False

def download_with_ytdlp(url, output_path, referer):
    """Use yt-dlp for fast, parallel HLS downloading."""
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        debug("yt-dlp not found")
        return False

    # Set referer via --add-header
    cmd = [
        ytdlp,
        "--add-header", f"Referer:{referer}",
        "--add-header", "User-Agent:Mozilla/5.0",
        "--no-playlist",
        "--output", str(output_path),
        "--quiet",
        "--no-warnings",
        url
    ]
    debug(f"Running yt-dlp: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        debug("yt-dlp succeeded")
        return True
    else:
        debug(f"yt-dlp failed (code {result.returncode}): {result.stderr[:200]}")
        return False

def download_file_direct(url, output_path, referer):
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

    # Check if HLS
    is_hls = url.endswith('.m3u8')
    if not is_hls:
        try:
            opener = get_opener()
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': referer})
            with opener.open(req, timeout=10) as resp:
                if '#EXTM3U' in resp.read(1024).decode('utf-8', errors='ignore'):
                    is_hls = True
        except:
            pass

    if is_hls:
        print("Downloading HLS stream (parallel methods only)...")
        # Try yt-dlp first (fastest, most reliable)
        if download_with_ytdlp(url, out_path, referer):
            pass
        else:
            print("yt-dlp unavailable or failed, using custom parallel fMP4 downloader...")
            if not download_fmp4_parallel(url, out_path, referer, max_workers=15):
                print("ERROR: Both fast methods failed. No slow fallback available.")
                sys.exit(1)
    else:
        print("Downloading direct file...")
        if not download_file_direct(url, out_path, referer):
            sys.exit(1)

    if out_path.exists() and out_path.stat().st_size > 0:
        size = out_path.stat().st_size / (1024*1024)
        print(f"\n✅ SUCCESS: {out_path} ({size:.2f} MB)")
        sys.exit(0)
    else:
        debug("Output file missing or empty")
        sys.exit(1)

if __name__ == "__main__":
    main()