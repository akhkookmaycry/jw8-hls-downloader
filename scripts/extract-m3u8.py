#!/usr/bin/env python3
"""
Robust HLS downloader with debug logs and highest quality selection.
"""

import sys
import os
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import time

def debug(msg):
    print(f"[DOWNLOADER-DEBUG] {msg}", file=sys.stderr)

def fetch_playlist(url, referer):
    """Fetch a playlist (master or media) with referer header."""
    debug(f"Fetching playlist: {url}")
    req = urllib.request.Request(url, headers={'Referer': referer, 'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode('utf-8')
            debug(f"Playlist fetched, size {len(content)} bytes")
            return content
    except Exception as e:
        debug(f"Failed to fetch playlist: {e}")
        raise

def get_highest_quality_media_playlist(master_url, referer):
    """Parse master playlist and return URL of the variant with highest BANDWIDTH."""
    content = fetch_playlist(master_url, referer)
    best_bw = -1
    best_url = None
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('#EXT-X-STREAM-INF'):
            bw_match = re.search(r'BANDWIDTH=(\d+)', line)
            if bw_match:
                bw = int(bw_match.group(1))
                # Next line is the media playlist URL
                if i+1 < len(lines):
                    variant = lines[i+1].strip()
                    if variant and not variant.startswith('#'):
                        full_url = urllib.parse.urljoin(master_url, variant)
                        debug(f"Variant bandwidth {bw}: {full_url}")
                        if bw > best_bw:
                            best_bw = bw
                            best_url = full_url
    if best_url:
        debug(f"Selected highest bandwidth: {best_url} (bw={best_bw})")
        return best_url
    else:
        debug("No variants found, assuming input URL is a media playlist")
        return master_url

def download_segment(segment_url, output_path, referer, retries=3):
    """Download a single segment with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(segment_url, headers={'Referer': referer, 'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                with open(output_path, 'wb') as f:
                    f.write(data)
                debug(f"Downloaded {output_path} ({len(data)} bytes)")
                return True
        except Exception as e:
            debug(f"Attempt {attempt+1} failed for {segment_url}: {e}")
            time.sleep(2)
    debug(f"Giving up on {segment_url}")
    return False

def main():
    if len(sys.argv) < 3:
        print("Usage: hls-downloader.py <m3u8_url> <output_file> [referer]")
        sys.exit(1)

    m3u8_url = sys.argv[1]
    output_file = sys.argv[2]
    referer = sys.argv[3] if len(sys.argv) > 3 else ""

    debug(f"=== HLS Downloader Started ===")
    debug(f"Input URL: {m3u8_url}")
    debug(f"Output file: {output_file}")
    debug(f"Referer: {referer}")

    # 1. Fetch and parse the master (if any) to get best quality media playlist
    debug("Checking if input is a master playlist...")
    media_url = get_highest_quality_media_playlist(m3u8_url, referer)
    debug(f"Media playlist URL: {media_url}")

    # 2. Fetch media playlist to get segment list
    media_playlist = fetch_playlist(media_url, referer)
    segment_urls = []
    base_url = media_url.rsplit('/', 1)[0] + '/'
    for line in media_playlist.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            # Resolve relative URLs
            seg_url = urllib.parse.urljoin(base_url, line)
            segment_urls.append(seg_url)

    debug(f"Found {len(segment_urls)} segments")
    for i, url in enumerate(segment_urls[:5]):  # log first 5
        debug(f"  Segment {i:04d}: {url}")

    # 3. Create segments directory
    seg_dir = "segments"
    os.makedirs(seg_dir, exist_ok=True)
    debug(f"Segments will be saved in {os.path.abspath(seg_dir)}")

    # 4. Download segments in parallel
    downloaded = 0
    total = len(segment_urls)
    debug(f"Starting parallel download (max workers=10)")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for idx, seg_url in enumerate(segment_urls):
            seg_path = os.path.join(seg_dir, f"seg_{idx:04d}.ts")
            futures[executor.submit(download_segment, seg_url, seg_path, referer)] = idx

        for future in as_completed(futures):
            if future.result():
                downloaded += 1
                if downloaded % 10 == 0:
                    debug(f"Progress: {downloaded}/{total}")
            else:
                debug(f"Failed segment {futures[future]}, aborting")
                sys.exit(1)

    debug(f"All {downloaded} segments downloaded successfully")

    # 5. Create ffmpeg concat file list (using absolute paths to avoid "file not found")
    filelist_path = os.path.join(seg_dir, "filelist.txt")
    with open(filelist_path, "w") as f:
        for idx in range(total):
            abs_path = os.path.abspath(os.path.join(seg_dir, f"seg_{idx:04d}.ts"))
            f.write(f"file '{abs_path}'\n")
    debug(f"Filelist created: {filelist_path}")

    # 6. Run ffmpeg to merge segments
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", filelist_path,
        "-c", "copy",
        output_file
    ]
    debug(f"Running ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        debug(f"ffmpeg error (code {result.returncode}):")
        debug(f"STDERR: {result.stderr}")
        sys.exit(1)
    else:
        debug(f"ffmpeg succeeded, output file: {output_file}")

    # 7. Cleanup segments (optional)
    debug("Cleaning up segment files...")
    for idx in range(total):
        try:
            os.remove(os.path.join(seg_dir, f"seg_{idx:04d}.ts"))
        except:
            pass
    try:
        os.rmdir(seg_dir)
    except:
        pass
    debug("Download complete!")

if __name__ == "__main__":
    main()
