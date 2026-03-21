#!/usr/bin/env python3
"""
Extract M3U8 URL from JW8 player page using Playwright
"""

import sys
import json
import re
import urllib.request
import ssl

# Ignore SSL for some sites
ssl._create_default_https_context = ssl._create_unverified_context


def fetch_page_content(url, referer=""):
    """Fetch page HTML content"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if referer:
        headers["Referer"] = referer

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"Error fetching page: {e}")
        return None


def extract_m3u8_from_html(html, base_url):
    """Extract M3U8 URLs from HTML content"""
    m3u8_urls = []

    # Pattern 1: Direct m3u8 URLs in quotes
    pattern1 = r'["\']([^"\']*?\.m3u8[^"\']*?)["\']'
    matches = re.findall(pattern1, html, re.IGNORECASE)
    for match in matches:
        url = match
        if not url.startswith("http"):
            url = base_url.rstrip("/") + "/" + url.lstrip("/")
        m3u8_urls.append(url)

    # Pattern 2: JW8 player setup configuration
    # Look for jwplayer().setup({...file: "url"...})
    jw8_patterns = [
        r'jwplayer\s*\(\s*["\']?\w*["\']?\s*\)\s*\.setup\s*\(\s*\{[^}]*?["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8)["\']',
        r'"file"\s*:\s*"([^"]+\.m3u8)"',
        r"'file'\s*:\s*'([^']+\.m3u8)'",
    ]
    for pattern in jw8_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        for match in matches:
            url = match
            if not url.startswith("http"):
                url = base_url.rstrip("/") + "/" + url.lstrip("/")
            if url not in m3u8_urls:
                m3u8_urls.append(url)

    # Pattern 3: JSON playlist/source configuration
    json_patterns = [
        r'"sources"\s*:\s*\[\s*\{[^}]*?"file"\s*:\s*"([^"]+)".*?"type"\s*:\s*"hls"',
        r'"file"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
    ]
    for pattern in json_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        for match in matches:
            url = match
            if not url.startswith("http"):
                url = base_url.rstrip("/") + "/" + url.lstrip("/")
            if url not in m3u8_urls:
                m3u8_urls.append(url)

    return m3u8_urls


def fetch_stream_url(page_url, referer=""):
    """Fetch the page and extract stream URL from JW8 player config"""
    html = fetch_page_content(page_url, referer)
    if not html:
        return None

    # Extract base URL
    if "://" in page_url:
        parts = page_url.split("/")
        base_url = "/".join(parts[:3])
    else:
        base_url = page_url.rsplit("/", 1)[0]

    # Look for stream URLs in JW8 player configuration
    # Pattern: /stream/.../master.m3u8
    stream_pattern = r'["\'](/stream/[^"\']+\.m3u8)["\']'
    matches = re.findall(stream_pattern, html)

    for match in matches:
        url = base_url + match
        print(f"Found stream URL: {url}")
        return url

    # Try generic m3u8 extraction
    m3u8_urls = extract_m3u8_from_html(html, base_url)

    # Filter for master playlists (usually contains 'master' or is the first one)
    for url in m3u8_urls:
        if "master" in url.lower():
            return url

    # Return first URL if no master found
    if m3u8_urls:
        return m3u8_urls[0]

    return None


def extract_m3u8(page_url, referer=""):
    """Main extraction function"""
    print(f"=== M3U8 Extractor ===")
    print(f"Page URL: {page_url}")

    # If input is already an M3U8 URL, return it directly
    if ".m3u8" in page_url.lower():
        print("Input is already an M3U8 URL")
        return page_url

    # Otherwise, fetch the page and extract M3U8
    m3u8_url = fetch_stream_url(page_url, referer)

    if m3u8_url:
        print(f"\n✅ Found M3U8 URL:")
        print(m3u8_url)
        return m3u8_url
    else:
        print("\n❌ Could not find M3U8 URL in page")
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract-m3u8.py <page_url> [referer]")
        sys.exit(1)

    page_url = sys.argv[1]
    referer = sys.argv[2] if len(sys.argv) > 2 else ""

    result = extract_m3u8(page_url, referer)

    if result:
        # Output for GitHub Actions
        print(f"\nM3U8_URL={result}")
    else:
        sys.exit(1)
