#!/usr/bin/env python3
"""
Ultimate video extractor – captures HLS and direct video using multiple strategies.
"""

import sys
import subprocess
import os
import re
import urllib.request
import urllib.parse
from urllib.request import Request
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

def debug(msg):
    print(f"[EXTRACT-DEBUG] {msg}", file=sys.stderr)

def get_file_size(url, proxy=None, timeout=5):
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
        debug(f"HEAD failed for {url[:80]}: {e}")
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
    debug("=== Ultimate Video Extractor ===")
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
        args: ['--no-sandbox', '--disable-web-security', '--disable-features=IsolateOrigins,site-per-process'{proxy_arg}]
    }});
    const page = await browser.newPage();
    let m3u8Urls = new Set();
    let videoUrls = new Set();
    let adPattern = /(?:^|[/_])(ad|preview|thumb|roomad|affiliates|promo|trailer|sample)(?:$|[/_])/i;
    
    // Enable request interception from start
    await page.route('**/*', route => {{
        const url = route.request().url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] Intercepted M3U8: ${{url}}`);
            m3u8Urls.add(url);
        }}
        if (/\\.(mp4|webm|mkv|ts)$/i.test(url)) {{
            console.error(`[NODE] Intercepted video: ${{url}}`);
            videoUrls.add(url);
        }}
        route.continue();
    }});
    
    console.error(`[NODE] Navigating to {escaped_url}`);
    await page.goto('{escaped_url}', {{ waitUntil: 'networkidle', timeout: 45000 }});
    console.error(`[NODE] Page title: "${{await page.title()}}"`);
    
    // Try to click any common play button
    const playSelectors = [
        'button[aria-label*="play"]', 'button[aria-label*="Play"]',
        '.play-button', '.play-btn', '.vjs-big-play-button',
        'video', '.jwplayer', '.player'
    ];
    for (const sel of playSelectors) {{
        const btn = await page.$(sel);
        if (btn) {{
            await btn.click().catch(() => {{}});
            console.error(`[NODE] Clicked ${{sel}}`);
            break;
        }}
    }}
    
    // Also click center of page
    await page.mouse.click(await page.evaluate(() => window.innerWidth/2), 
                           await page.evaluate(() => window.innerHeight/2));
    
    console.error('[NODE] Waiting 15 seconds for video to load...');
    await page.waitForTimeout(15000);
    
    // Extract video URLs from page's JavaScript variables
    const jsUrls = await page.evaluate(() => {{
        const results = [];
        // Common global video variables
        const vars = ['playerConfig', 'videoSources', 'hlsUrl', 'source', 'src', 'file', 'playlist'];
        for (let v of vars) {{
            if (window[v]) {{
                try {{
                    let val = window[v];
                    if (typeof val === 'object') val = JSON.stringify(val);
                    if (typeof val === 'string' && (val.includes('.m3u8') || val.includes('.mp4')))
                        results.push(val);
                }} catch(e) {{}}
            }}
        }}
        // Search in script tags
        document.querySelectorAll('script').forEach(script => {{
            const text = script.textContent;
            if (text) {{
                const matches = text.match(/(https?:\\/\\/[^\\s"']+\\.(?:m3u8|mp4|webm|mkv))/gi);
                if (matches) results.push(...matches);
            }}
        }});
        return results;
    }});
    for (let url of jsUrls) {{
        if (url.includes('.m3u8')) m3u8Urls.add(url);
        else if (url.includes('.mp4') && !adPattern.test(url)) videoUrls.add(url);
    }}
    
    // Also get video element src
    const videoSrc = await page.evaluate(() => {{
        const v = document.querySelector('video');
        if (v && v.src && v.src.length > 0 && !v.src.startsWith('blob:')) return v.src;
        return null;
    }});
    if (videoSrc && !adPattern.test(videoSrc)) {{
        if (videoSrc.includes('.m3u8')) m3u8Urls.add(videoSrc);
        else videoUrls.add(videoSrc);
    }}
    
    await browser.close();
    console.error(`[NODE] Final: M3U8=${{m3u8Urls.size}}, Direct=${{videoUrls.size}}`);
    
    // Output M3U8 first if any
    if (m3u8Urls.size > 0) {{
        let master = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
        console.log(master);
        return;
    }}
    
    // Filter direct URLs
    let candidates = [];
    for (let url of videoUrls) {{
        if (adPattern.test(url)) {{
            console.error(`[NODE] Skipping ad: ${{url.substring(0, 80)}}`);
            continue;
        }}
        candidates.push(url);
        console.error(`[NODE] Candidate: ${{url.substring(0, 80)}}`);
    }}
    if (candidates.length === 0) {{
        console.error('[NODE] No video found');
        process.exit(1);
    }}
    // Output all candidates for size comparison
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
            timeout=120,
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
        if lines[0].endswith('.m3u8'):
            best = get_highest_bandwidth_url(lines[0], proxy=proxy)
            return best
        # Direct candidates: pick largest by file size
        debug(f"Checking sizes of {len(lines)} candidate direct URLs...")
        sizes = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(get_file_size, url, proxy): url for url in lines}
            for fut in as_completed(futures):
                url = futures[fut]
                size = fut.result()
                sizes[url] = size
                debug(f"Size for {url[:80]}: {size} bytes")
        best_url = max(sizes, key=sizes.get, default=None)
        if best_url:
            debug(f"Selected largest: {best_url} ({sizes[best_url]} bytes)")
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
