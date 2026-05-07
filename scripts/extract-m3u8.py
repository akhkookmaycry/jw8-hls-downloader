#!/usr/bin/env python3
"""
Universal video extractor – picks largest direct video if no HLS.
"""

import sys
import subprocess
import os
import re
import urllib.request
import urllib.parse
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

def debug(msg):
    print(f"[EXTRACT-DEBUG] {msg}", file=sys.stderr)

def get_file_size(url, proxy=None, timeout=5):
    """Return size in bytes via HEAD request."""
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({'socks5': proxy})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        req = Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=timeout) as resp:
            size = resp.headers.get('Content-Length')
            if size:
                return int(size)
    except Exception as e:
        debug(f"HEAD request failed for {url[:80]}: {e}")
    return 0

def get_highest_bandwidth_url(master_url, proxy=None):
    if not master_url.endswith('.m3u8'):
        return master_url
    debug(f"Fetching master playlist: {master_url}")
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

def extract_video_url(page_url, referer=""):
    debug("=== Universal Video Extractor (size‑aware) ===")
    debug(f"Page URL: {page_url}")
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    proxy = "socks5://127.0.0.1:1080" if use_warp else None
    debug(f"WARP: {use_warp}")

    if re.search(r'\.(m3u8|mp4|webm|mkv|avi|mov)$', page_url.lower()):
        debug("Direct URL – returning as is")
        return page_url

    escaped_url = page_url.replace("'", "\\'").replace('"', '\\"')
    proxy_arg = f', "--proxy-server={proxy}"' if proxy else ''
    proxy_log = f', proxy: {proxy}' if proxy else ''

    node_script = f'''
const {{ chromium }} = require('playwright');

(async () => {{
    console.error('[NODE] Launching browser{proxy_log}');
    const browser = await chromium.launch({{
        headless: true,
        args: ['--no-sandbox'{proxy_arg}]
    }});
    const page = await browser.newPage();
    let m3u8Urls = new Set();
    let videoUrls = new Set();
    let adPattern = /(?:^|[/_])(ad|preview|thumb|roomad|affiliates|promo|trailer)(?:$|[/_])/i;
    
    page.on('request', request => {{
        const url = request.url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] M3U8: ${{url}}`);
            m3u8Urls.add(url);
        }}
        if (/\\.(mp4|webm|mkv|ts)$/i.test(url)) {{
            console.error(`[NODE] Video: ${{url}}`);
            videoUrls.add(url);
        }}
    }});
    
    console.error(`[NODE] Navigating to {escaped_url}`);
    await page.goto('{escaped_url}', {{ waitUntil: 'networkidle', timeout: 30000 }});
    console.error(`[NODE] Page title: "${{await page.title()}}"`);
    
    // Click on video player
    console.error('[NODE] Trying to click on video player...');
    const selectors = ['video', '.jwplayer', '.video-js', '.player', '[id*=player]', '[class*=video]'];
    for (const sel of selectors) {{
        if (await page.$(sel)) {{
            await page.click(sel, {{ timeout: 2000 }}).catch(() => {{}});
            console.error(`[NODE] Clicked ${{sel}}`);
            break;
        }}
    }}
    
    console.error('[NODE] Waiting 10 seconds for video to load...');
    await page.waitForTimeout(10000);
    
    // Also try to get video src directly
    const videoSrc = await page.evaluate(() => {{
        const v = document.querySelector('video');
        if (v && v.src && v.src.length > 0 && !v.src.startsWith('blob:')) return v.src;
        return null;
    }});
    if (videoSrc && !adPattern.test(videoSrc)) {{
        console.error(`[NODE] Direct video src: ${{videoSrc}}`);
        videoUrls.add(videoSrc);
    }}
    
    await browser.close();
    console.error(`[NODE] Found ${{m3u8Urls.size}} M3U8, ${{videoUrls.size}} direct URLs`);
    
    if (m3u8Urls.size > 0) {{
        let master = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
        console.log(master);
        return;
    }}
    
    // No M3U8 – filter and pick largest direct video
    let candidates = [];
    for (let url of videoUrls) {{
        if (adPattern.test(url)) {{
            console.error(`[NODE] Skipping ad URL: ${{url.substring(0, 80)}}`);
            continue;
        }}
        candidates.push(url);
        console.error(`[NODE] Candidate: ${{url.substring(0, 80)}}`);
    }}
    
    if (candidates.length === 0) {{
        console.error('[NODE] No suitable video URL found');
        process.exit(1);
    }}
    
    // Output URLs to stdout for size checking in Python (we'll just output the first,
    // but the Python script will query sizes. To avoid multiple passes,
    // we output all candidates line by line, and Python picks the largest.
    for (let url of candidates) {{
        console.log(url);
    }}
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
            timeout=90,
            cwd=script_dir
        )
        debug(f"Node exit: {result.returncode}")
        if result.stderr:
            debug(f"Node stderr:\n{result.stderr}")
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().split('\n')
        if not lines:
            return None
        # If first line looks like an M3U8, return upgraded version
        if lines[0].endswith('.m3u8'):
            best = get_highest_bandwidth_url(lines[0], proxy=proxy)
            return best
        # Otherwise treat as list of direct URLs – pick largest by size
        debug(f"Checking sizes of {len(lines)} candidate direct URLs...")
        sizes = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(get_file_size, url, proxy): url for url in lines}
            for fut in as_completed(futures):
                url = futures[fut]
                size = fut.result()
                sizes[url] = size
                debug(f"Size for {url[:80]}: {size} bytes")
        # Choose largest
        best_url = max(sizes, key=sizes.get, default=None)
        if best_url and sizes[best_url] > 100000:  # >100KB (real video)
            debug(f"Selected largest video: {best_url} (size {sizes[best_url]} bytes)")
            return best_url
        elif best_url:
            debug(f"Selected URL but size is tiny ({sizes[best_url]}), may be ad")
            return best_url
        return None
    except Exception as e:
        debug(f"Exception: {e}")
        return None
    finally:
        try:
            os.remove(script_path)
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
