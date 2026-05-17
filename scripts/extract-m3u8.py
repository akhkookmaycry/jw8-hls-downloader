#!/usr/bin/env python3
"""
Diagnostic video extractor with Ghostery adblocker + Eporner API fallback.
"""

import sys
import subprocess
import os
import re
import urllib.request
import urllib.parse
from urllib.request import Request
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
import json
import requests  # <-- add requests for API calls

def debug(msg):
    print(f"[EXTRACT-DEBUG] {msg}", file=sys.stderr)

def get_file_size(url, proxy=None, timeout=8):
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({'socks5': proxy})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        req = Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=timeout) as resp:
            size = resp.headers.get('Content-Length')
            return int(size) if size else 0
    except Exception as e:
        debug(f"HEAD failed for {url[:80]}: {e}")
        return 0

def get_highest_bandwidth_url(master_url, proxy=None):
    # unchanged
    if not master_url.endswith('.m3u8'):
        return master_url
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({'socks5': proxy})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        req = Request(master_url, headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=15) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Failed to fetch master: {e}")
        return master_url

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
                        debug(f"Variant {bw}: {full[:100]}")
                        if bw > best_bw:
                            best_bw = bw
                            best_url = full
    return best_url if best_url else master_url

def extract_eporner_api(page_url, proxy=None):
    """Extract direct MP4 URL from Eporner API with extensive logging."""
    debug("=== Eporner API Extractor ===")
    debug(f"Page URL: {page_url}")
    
    # Extract video ID
    match = re.search(r'video-([A-Za-z0-9]+)', page_url)
    if not match:
        debug("❌ Could not extract video ID from URL")
        return None
    video_id = match.group(1)
    debug(f"Extracted video ID: {video_id}")
    
    # Build API URL
    api_url = f"https://www.eporner.com/api/v2/video/id/{video_id}/"
    debug(f"Calling API: {api_url}")
    
    try:
        # Use optional proxy if WARP is enabled
        proxies = None
        if proxy:
            proxies = {'http': proxy, 'https': proxy}
        response = requests.get(api_url, headers={'User-Agent': 'Mozilla/5.0'}, proxies=proxies, timeout=15)
        debug(f"API response status: {response.status_code}")
        
        if response.status_code != 200:
            debug(f"❌ API returned non-200: {response.status_code}")
            debug(f"Response body: {response.text[:200]}")
            return None
        
        data = response.json()
        debug(f"API response keys: {list(data.keys())}")
        
        if 'video' not in data:
            debug("❌ No 'video' key in API response")
            return None
        
        video_data = data['video']
        sources = video_data.get('sources', [])
        if not sources:
            debug("❌ No sources in API response")
            return None
        
        debug(f"Found {len(sources)} quality levels:")
        # Sort by resolution or size (prefer higher resolution)
        for src in sources:
            width = src.get('width', 0)
            height = src.get('height', 0)
            size_mb = src.get('size', 0) / (1024*1024)
            debug(f"  - {width}x{height} ({size_mb:.1f} MB) -> {src.get('src', '')[:80]}")
        
        # Select the best quality: prefer 1080p, then 720p, then largest size
        best = None
        best_score = -1
        for src in sources:
            width = src.get('width', 0)
            height = src.get('height', 0)
            size = src.get('size', 0)
            # Score: resolution (pixels) + size bonus
            score = width * height + (size / 1000)
            if score > best_score:
                best_score = score
                best = src
        
        if not best:
            debug("❌ No best source selected")
            return None
        
        video_url = best.get('src')
        if not video_url:
            debug("❌ Selected source missing 'src'")
            return None
        
        debug(f"✅ Selected video URL: {video_url}")
        debug(f"Resolution: {best.get('width')}x{best.get('height')}, Size: {best.get('size', 0)/(1024*1024):.1f} MB")
        return video_url
        
    except Exception as e:
        debug(f"❌ Exception during API call: {e}")
        return None

def extract_video_url(page_url, referer=""):
    debug("=== Diagnostic Video Extractor ===")
    debug(f"Page URL: {page_url}")
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    proxy = "socks5://127.0.0.1:1080" if use_warp else None
    debug(f"WARP: {use_warp}")

    # Special handling for eporner.com – use API directly (fast and reliable)
    if 'eporner.com' in page_url.lower():
        debug("Eporner domain detected – using API extractor (skipping Playwright)")
        video_url = extract_eporner_api(page_url, proxy=proxy)
        if video_url:
            debug("Eporner API extraction successful")
            return video_url
        else:
            debug("Eporner API failed – falling back to Playwright (may not work)")
            # Continue to Playwright fallback

    if re.search(r'\.(m3u8|mp4|webm|mkv|avi|mov)$', page_url.lower()):
        debug("Direct URL, returning as is")
        return page_url

    # ========== Playwright-based extractor (original) ==========
    escaped_url = page_url.replace("'", "\\'").replace('"', '\\"')
    proxy_arg = f', "--proxy-server={proxy}"' if proxy else ''
    proxy_log = f', proxy: {proxy}' if proxy else ''

    tmp_html = tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False)
    tmp_html_path = tmp_html.name
    tmp_html.close()

    node_script = f'''
const {{ chromium }} = require('playwright');
const {{ PlaywrightBlocker }} = require('@ghostery/adblocker-playwright');
const fetch = require('cross-fetch');
const fs = require('fs');

(async () => {{
    console.error('[NODE] Launching browser{proxy_log}');
    const browser = await chromium.launch({{
        headless: true,
        args: ['--no-sandbox'{proxy_arg}]
    }});
    const page = await browser.newPage();
    
    await page.setViewportSize({{ width: 1280, height: 720 }});
    await page.setExtraHTTPHeaders({{
        'Accept-Language': 'en-US,en;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }});
    
    console.error('[NODE] Initializing adblocker...');
    const blocker = await PlaywrightBlocker.fromPrebuiltAdsOnly(fetch);
    await blocker.enableBlockingInPage(page);
    
    let blockedCount = 0;
    blocker.on('request-blocked', (request) => {{
        blockedCount++;
        console.error(`[NODE] 🚫 Ad blocked: ${{request.url.substring(0, 100)}}`);
    }});
    
    let m3u8Urls = new Set();
    let videoUrls = new Set();
    let allRequests = [];
    let redirectChain = [];
    
    page.on('response', response => {{
        const url = response.url();
        const status = response.status();
        const headers = response.headers();
        if (status >= 300 && status < 400 && headers.location) {{
            redirectChain.push({{ from: url, to: headers.location, status }});
            console.error(`[NODE] 🔁 Redirect ${{status}}: ${{url}} -> ${{headers.location}}`);
        }}
        if (url === '{escaped_url}') {{
            console.error(`[NODE] Main page HTTP status: ${{status}}`);
        }}
    }});
    
    page.on('request', request => {{
        const url = request.url();
        allRequests.push(url);
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] 📺 M3U8 request: ${{url}}`);
            m3u8Urls.add(url);
        }}
        if (/\\.(mp4|webm|mkv|ts|m2ts|mts)$/i.test(url)) {{
            console.error(`[NODE] 🎬 Direct video request: ${{url.substring(0, 100)}}`);
            videoUrls.add(url);
        }}
    }});
    
    page.on('requestfailed', request => {{
        console.error(`[NODE] ❌ Failed request: ${{request.url().substring(0, 100)}} - ${{request.failure()?.errorText || 'unknown'}}`);
    }});
    
    page.on('pageerror', error => {{
        console.error(`[NODE] ⚠️ Page JS error: ${{error.message}}`);
    }});
    
    console.error(`[NODE] Navigating to {escaped_url}`);
    try {{
        await page.goto('{escaped_url}', {{ waitUntil: 'networkidle', timeout: 45000 }});
        console.error(`[NODE] Final URL: ${{page.url()}}`);
    }} catch(e) {{
        console.error(`[NODE] Navigation error: ${{e.message}}`);
        await browser.close();
        process.exit(1);
    }}
    
    const pageTitle = await page.title();
    console.error(`[NODE] Page title: "${{pageTitle}}"`);
    
    // Save HTML for debugging
    const html = await page.content();
    fs.writeFileSync('{tmp_html_path}', html);
    console.error(`[NODE] Saved HTML to {tmp_html_path}`);
    
    // DOM analysis
    const domInfo = await page.evaluate(() => {{
        const hasVideo = !!document.querySelector('video');
        const videoSrc = document.querySelector('video')?.src || null;
        const iframes = document.querySelectorAll('iframe').length;
        return {{ hasVideo, videoSrc, iframes }};
    }});
    console.error(`[NODE] DOM: video element = ${{domInfo.hasVideo}}, video.src = ${{domInfo.videoSrc || 'none'}}, iframes = ${{domInfo.iframes}}`);
    
    await page.waitForTimeout(5000);
    
    await browser.close();
    
    console.error(`[NODE] Summary: M3U8=${{m3u8Urls.size}}, Direct=${{videoUrls.size}}, Blocked=${{blockedCount}}`);
    console.error(`[NODE] Total requests: ${{allRequests.length}}`);
    if (m3u8Urls.size > 0) {{
        let master = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
        console.log(master);
        return;
    }}
    if (videoUrls.size > 0) {{
        for (let url of videoUrls) console.log(url);
        return;
    }}
    console.error('[NODE] No video sources found');
    process.exit(1);
}})();
'''

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(script_dir, "extract_video.cjs")
    with open(script_path, "w") as f:
        f.write(node_script)

    try:
        result = subprocess.run(
            ["node", script_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=script_dir
        )
        debug(f"Node exit: {result.returncode}")
        if result.stderr:
            debug(f"Node stderr:\n{result.stderr}")
        if result.returncode != 0:
            debug("Playwright extraction failed")
            return None
        lines = result.stdout.strip().split('\n')
        if not lines:
            return None
        if lines[0].endswith('.m3u8'):
            best = get_highest_bandwidth_url(lines[0], proxy=proxy)
            return best
        # Direct URLs
        debug(f"Checking sizes for {len(lines)} direct URLs...")
        sizes = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(get_file_size, url, proxy): url for url in lines}
            for fut in as_completed(futures):
                url = futures[fut]
                sizes[url] = fut.result()
                debug(f"  {url[:80]} -> {sizes[url]} bytes")
        if not sizes:
            return None
        best_url = max(sizes, key=sizes.get)
        best_size = sizes[best_url]
        debug(f"Selected URL (size {best_size} bytes): {best_url}")
        if best_size < 100000:
            debug("WARNING: File smaller than 100KB – likely not the real video")
        return best_url
    except Exception as e:
        debug(f"Exception: {e}")
        return None
    finally:
        try:
            os.remove(script_path)
            os.unlink(tmp_html_path)
        except:
            pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract-m3u8.py <page_url> [referer]", file=sys.stderr)
        sys.exit(1)
    page_url = sys.argv[1]
    referer = sys.argv[2] if len(sys.argv) > 2 else ""
    result = extract_video_url(page_url, referer)
    if result:
        print(f"VIDEO_URL={result}")
        sys.exit(0)
    else:
        sys.exit(1)