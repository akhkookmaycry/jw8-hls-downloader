#!/usr/bin/env python3
"""
Diagnostic video extractor with Ghostery adblocker.
Logs everything to help debug why video extraction fails.
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

def debug(msg):
    print(f"[EXTRACT-DEBUG] {msg}", file=sys.stderr)

def get_file_size(url, proxy=None, timeout=8):
    # unchanged, keep original
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

def extract_video_url(page_url, referer=""):
    debug("=== Diagnostic Video Extractor ===")
    debug(f"Page URL: {page_url}")
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    proxy = "socks5://127.0.0.1:1080" if use_warp else None
    debug(f"WARP: {use_warp}")

    if re.search(r'\.(m3u8|mp4|webm|mkv|avi|mov)$', page_url.lower()):
        debug("Direct URL, returning as is")
        return page_url

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
    let finalUrl = '';
    
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
    let response;
    try {{
        response = await page.goto('{escaped_url}', {{ waitUntil: 'networkidle', timeout: 45000 }});
        finalUrl = page.url();
        console.error(`[NODE] Final URL after redirects: ${{finalUrl}}`);
        console.error(`[NODE] Navigation finished, final status: ${{response?.status() || 'unknown'}}`);
    }} catch(e) {{
        console.error(`[NODE] Navigation error: ${{e.message}}`);
        await browser.close();
        process.exit(1);
    }}
    
    const pageTitle = await page.title();
    console.error(`[NODE] Page title: "${{pageTitle}}"`);
    
    // Save HTML for manual inspection
    const html = await page.content();
    fs.writeFileSync('{tmp_html_path}', html);
    console.error(`[NODE] Saved final HTML to {tmp_html_path}`);
    
    // Check for common blockers / messages in the page body
    const bodyText = await page.evaluate(() => document.body?.innerText || '');
    const bodyLower = bodyText.toLowerCase();
    let blockersDetected = [];
    if (bodyLower.includes('age verification') || bodyLower.includes('confirm you are 18') || bodyLower.includes('yes i am')) {{
        blockersDetected.push('AGE_GATE');
        console.error(`[NODE] 🚨 AGE GATE detected in page text.`);
    }}
    if (bodyLower.includes('captcha') || bodyLower.includes('access denied') || bodyLower.includes('unusual traffic')) {{
        blockersDetected.push('CAPTCHA_OR_BLOCK');
        console.error(`[NODE] 🚨 CAPTCHA or ACCESS DENIED detected.`);
    }}
    if (bodyLower.includes('video not found') || bodyLower.includes('404') || bodyLower.includes('removed')) {{
        blockersDetected.push('VIDEO_MISSING');
        console.error(`[NODE] 🚨 'VIDEO NOT FOUND' message detected.`);
    }}
    if (bodyLower.includes('error') || bodyLower.includes('fail')) {{
        console.error(`[NODE] ⚠️ Page contains generic error messages.`);
    }}
    
    // DOM analysis
    const domInfo = await page.evaluate(() => {{
        const hasVideo = !!document.querySelector('video');
        const videoSrc = document.querySelector('video')?.src || null;
        const iframes = document.querySelectorAll('iframe').length;
        const playerScripts = Array.from(document.querySelectorAll('script')).some(s => 
            s.src && (s.src.includes('jwplayer') || s.src.includes('videojs') || s.src.includes('hls.js'))
        );
        return {{ hasVideo, videoSrc, iframes, playerScripts }};
    }});
    console.error(`[NODE] DOM info: video element = ${{domInfo.hasVideo}}, video.src = ${{domInfo.videoSrc || 'none'}}, iframes = ${{domInfo.iframes}}, player script = ${{domInfo.playerScripts}}`);
    
    await page.waitForTimeout(5000);
    
    // After waiting, re-check for any new video src
    const finalVideoSrc = await page.evaluate(() => {{
        const v = document.querySelector('video');
        if (v && v.src && v.src.startsWith('http')) return v.src;
        const sources = document.querySelectorAll('video source');
        for (let s of sources) {{
            if (s.src && s.src.startsWith('http')) return s.src;
        }}
        return null;
    }});
    if (finalVideoSrc) {{
        console.error(`[NODE] Final video src from DOM: ${{finalVideoSrc}}`);
        if (finalVideoSrc.endsWith('.m3u8')) m3u8Urls.add(finalVideoSrc);
        else videoUrls.add(finalVideoSrc);
    }}
    
    await browser.close();
    
    console.error(`[NODE] Summary: M3U8 URLs found = ${{m3u8Urls.size}}, Direct URLs = ${{videoUrls.size}}, Blocked requests = ${{blockedCount}}`);
    console.error(`[NODE] Total network requests: ${{allRequests.length}}`);
    if (allRequests.length > 0) {{
        console.error(`[NODE] Sample of first 10 requests:`);
        allRequests.slice(0, 10).forEach((url, i) => console.error(`  ${{i+1}}. ${{url.substring(0, 120)}}`));
    }}
    if (redirectChain.length > 0) {{
        console.error(`[NODE] Redirect chain:`);
        redirectChain.forEach((r, i) => console.error(`  ${{i+1}}. ${{r.status}} ${{r.from}} -> ${{r.to}}`));
    }}
    if (blockersDetected.length > 0) {{
        console.error(`[NODE] Blockers detected: ${{blockersDetected.join(', ')}}`);
    }}
    
    // Output video URL(s) to stdout
    if (m3u8Urls.size > 0) {{
        let master = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
        console.log(master);
        return;
    }}
    if (videoUrls.size > 0) {{
        // Output all direct URLs (one per line) – the outer script will pick the largest
        for (let url of videoUrls) {{
            console.log(url);
        }}
        return;
    }}
    console.error('[NODE] ❌ No video sources found. See diagnostics above.');
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
            # Print everything – it's diagnostic
            debug(f"Node stderr:\n{result.stderr}")
        if result.returncode != 0:
            debug("Extraction failed – see stderr for details")
            return None
        lines = result.stdout.strip().split('\n')
        if not lines:
            return None
        # If first line is an M3U8
        if lines[0].endswith('.m3u8'):
            best = get_highest_bandwidth_url(lines[0], proxy=proxy)
            return best
        # Multiple direct URLs: pick largest by size
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