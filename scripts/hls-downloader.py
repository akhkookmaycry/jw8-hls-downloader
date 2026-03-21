#!/usr/bin/env python3
"""
HLS Segment Downloader for GitHub Actions
Downloads HLS streams with non-standard extensions (like .image from TikTok CDN)
"""

import sys
import os
import subprocess
import urllib.request
import urllib.error
import ssl
import time
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Disable SSL verification for some CDNs
ssl._create_default_https_context = ssl._create_unverified_context


def fetch_m3u8(url, referer=""):
    """Fetch M3U8 playlist content"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer or "https://callistanise.com",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"Error fetching M3U8: {e}")
        return None


def parse_m3u8(content, base_url, referer=""):
    """Parse M3U8 content and extract segment URLs"""
    if not content:
        return []

    lines = content.strip().split("\n")
    segments = []
    base_path = base_url.rsplit("/", 1)[0] + "/"

    # Check if it's a master playlist
    if "#EXT-X-STREAM-INF" in content:
        print("Master playlist detected, finding best quality...")
        for i, line in enumerate(lines):
            if "#EXT-X-STREAM-INF" in line and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("#"):
                    # Use first quality (usually best)
                    if not next_line.startswith("http"):
                        next_line = base_path + next_line
                    print(f"Selected quality URL: {next_line[:80]}...")
                    # Fetch the actual segment playlist
                    sub_content = fetch_m3u8(next_line, referer)
                    if sub_content:
                        return parse_m3u8(sub_content, next_line, referer)
                    else:
                        print("Error fetching sub-playlist")
                        return []

    # Parse segments
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            if line.startswith("http"):
                segments.append(line)
            else:
                segments.append(base_path + line)

    return segments


def download_segment(args):
    """Download a single segment"""
    index, url, output_path, headers, max_retries = args

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                with open(output_path, "wb") as f:
                    f.write(data)
                return (index, True, len(data))
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1 * (attempt + 1))  # Exponential backoff
            else:
                print(f"  Failed segment {index} after {max_retries} attempts: {e}")
                return (index, False, 0)

    return (index, False, 0)


def download_hls(m3u8_url, output_name, referer="", max_workers=10):
    """Main download function"""

    # Setup headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = (
            referer.rstrip("/v/").rsplit("/", 1)[0] if "/v/" in referer else ""
        )

    print(f"=== HLS Downloader ===")
    print(f"URL: {m3u8_url[:80]}...")
    print(f"Output: {output_name}")
    if referer:
        print(f"Referer: {referer}")

    # Fetch and parse M3U8
    print("\n[1/4] Fetching M3U8 playlist...")
    content = fetch_m3u8(m3u8_url, referer)
    if not content:
        print("ERROR: Failed to fetch M3U8")
        sys.exit(1)

    print("[2/4] Parsing segments...")
    segments = parse_m3u8(content, m3u8_url, referer)

    if not segments:
        print("ERROR: No segments found")
        sys.exit(1)

    print(f"Found {len(segments)} segments")

    # Create directories
    segments_dir = Path("segments")
    segments_dir.mkdir(exist_ok=True)
    Path("output").mkdir(exist_ok=True)

    # Prepare download tasks
    print(f"\n[3/4] Downloading {len(segments)} segments (parallel: {max_workers})...")
    tasks = []
    for i, url in enumerate(segments):
        seg_path = segments_dir / f"seg_{i:06d}.ts"
        tasks.append((i, url, str(seg_path), headers, 3))

    # Download segments in parallel
    downloaded = 0
    failed = 0
    total_bytes = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(download_segment, task): task[0] for task in tasks}

        for future in as_completed(futures):
            index, success, size = future.result()
            if success:
                downloaded += 1
                total_bytes += size
                if (index + 1) % 50 == 0 or index == len(segments) - 1:
                    print(
                        f"  Progress: {index + 1}/{len(segments)} ({(index + 1) * 100 // len(segments)}%)"
                    )
            else:
                failed += 1

    print(
        f"\nDownloaded: {downloaded}/{len(segments)} segments ({total_bytes / (1024 * 1024):.2f} MB)"
    )

    if failed > len(segments) / 2:
        print(f"ERROR: Too many failed segments ({failed})")
        sys.exit(1)

    # Create file list for ffmpeg
    print("\n[4/4] Merging with ffmpeg...")
    filelist_path = segments_dir / "filelist.txt"
    with open(filelist_path, "w") as f:
        for i in range(len(segments)):
            seg_path = segments_dir / f"seg_{i:06d}.ts"
            if seg_path.exists():
                f.write(f"file '{seg_path.resolve()}'\n")

    # Merge with ffmpeg
    output_path = Path("output") / output_name
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(filelist_path),
        "-c",
        "copy",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        sys.exit(1)

    # Verify output
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"\n=== SUCCESS ===")
        print(f"Output: {output_path}")
        print(f"Size: {size_mb:.2f} MB")
    else:
        print("ERROR: Output file not created")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python hls-downloader.py <m3u8_url> <output_name> [referer]")
        sys.exit(1)

    m3u8_url = sys.argv[1]
    output_name = sys.argv[2]
    referer = sys.argv[3] if len(sys.argv) > 3 else ""

    download_hls(m3u8_url, output_name, referer)
