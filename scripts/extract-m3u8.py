#!/usr/bin/env python3
"""
Extract M3U8 URL (or direct video) from JW8 player – with full diagnostic logging.
"""

import sys
import subprocess
import os
import re
import urllib.request
from urllib.parse import urljoin

def debug(msg):
    print(f"[EXTRACT-DEBUG] {msg}", file=sys.stderr)

def get_highest_bandwidth_url(master_url, proxy=None):
    debug(f"Fetching master playlist: {master_url}")
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({'socks5': proxy})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        req = urllib.request.Request(master_url, headers={'User-Agent': 'Mozilla/5.0'})
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
                        full = urljoin(master_url, variant)
                        debug(f"Variant {bw}: {full[:100]}")
                        if bw > best_bw:
                            best_bw = bw
                            best_url = full
    if best_url:
        debug(f"Selected best quality (bandwidth {best_bw})")
        return best_url
    return master_url

def extract_m3u8(page_url, referer=""):
    debug("=== M3U8 Extractor Started ===")
    debug(f"Page URL: {page_url}")
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    proxy = "socks5://127.0.0.1:1080" if use_warp else None
    debug(f"WARP enabled: {use_warp}")

    if ".m3u8" in page_url.lower():
        debug("Already an M3U8 URL")
        return page_url

    escaped_url = page_url.replace("'", "\\'").replace('"', '\\"')
    
    # Build proxy argument for Chromium
    if proxy:
        proxy_arg = f', "--proxy-server={proxy}"'
        proxy_log = f', proxy: {proxy}'
    else:
        proxy_arg = ''
        proxy_log = ''

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
    let videoUrls = new Set();  // direct .mp4, .webm, etc.
    let httpStatuses = new Map();
    
    // Capture response status codes
    page.on('response', response => {{
        const url = response.url();
        const status = response.status();
        httpStatuses.set(url, status);
        if (status >= 400) {{
            console.error(`[NODE] HTTP ${{status}} for ${{url}}`);
        }}
    }});
    
    // Capture errors
    page.on('pageerror', error => {{
        console.error(`[NODE] Page error: ${{error.message}}`);
    }});
    page.on('requestfailed', request => {{
        console.error(`[NODE] Request failed: ${{request.url()}} - ${{request.failure()?.errorText || 'unknown'}}`);
    }});
    
    // Intercept requests for HLS and direct video
    page.on('request', request => {{
        const url = request.url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] Captured M3U8: ${{url}}`);
            m3u8Urls.add(url);
        }}
        if (/\\.(mp4|webm|mkv|avi|mov|ts)$/i.test(url)) {{
            console.error(`[NODE] Captured direct video: ${{url}}`);
            videoUrls.add(url);
        }}
    }});
    
    console.error(`[NODE] Navigating to {escaped_url}`);
    let response;
    try {{
        response = await page.goto('{escaped_url}', {{ timeout: 30000, waitUntil: 'domcontentloaded' }});
        console.error(`[NODE] Main page status: ${{response?.status() || 'unknown'}}`);
    }} catch(e) {{
        console.error(`[NODE] Navigation error: ${{e.message}}`);
        await browser.close();
        process.exit(1);
    }}
    
    // Check for CAPTCHA in page title or body
    const pageTitle = await page.title();
    console.error(`[NODE] Page title: "${{pageTitle}}"`);
    const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
    if (/captcha|access denied|blocked|unusual traffic|verify you are human/i.test(bodyText)) {{
        console.error(`[NODE] POSSIBLE CAPTCHA/BLOCKING detected in page content!`);
        console.error(`[NODE] Body snippet: ${{bodyText.substring(0, 200)}}`);
    }}
    
    await page.waitForTimeout(6000);
    
    // JW8 API
    try {{
        const pl = await page.evaluate(() => {{
            if (typeof jwplayer !== 'undefined') {{
                const p = jwplayer().getPlaylist();
                if (p && p[0] && p[0].file) return p[0].file;
            }}
            return null;
        }});
        if (pl) {{
            console.error(`[NODE] JW API returned: ${{pl}}`);
            let final = pl;
            if (!final.startsWith('http')) {{
                const u = new URL('{escaped_url}');
                final = u.origin + final;
            }}
            if (final.includes('.m3u8')) m3u8Urls.add(final);
            else if (/\\.(mp4|webm|mkv)$/i.test(final)) videoUrls.add(final);
        }}
    }} catch(e) {{ console.error(`[NODE] JW error: ${{e.message}}`); }}
    
    await browser.close();
    
    console.error(`[NODE] Found ${{m3u8Urls.size}} M3U8 URLs, ${{videoUrls.size}} direct video URLs`);
    if (m3u8Urls.size === 0 && videoUrls.size === 0) {{
        console.error(`[NODE] No video URLs found. HTTP status summary (non-200):`);
        for (let [url, status] of httpStatuses.entries()) {{
            if (status !== 200) console.error(`[NODE]   ${{url}} -> ${{status}}`);
        }}
        process.exit(1);
    }}
    
    // Prefer M3U8 master, otherwise first video URL
    let master = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
    if (!master && videoUrls.size > 0) {{
        master = Array.from(videoUrls)[0];
        console.error(`[NODE] Using direct video URL as fallback: ${{master}}`);
    }}
    if (master) {{
        console.log(master);
    }} else {{
        process.exit(1);
    }}
}})();
'''

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(script_dir, "extract_m3u8.cjs")
    with open(script_path, "w") as f:
        f.write(node_script)

    try:
        result = subprocess.run(
            ["node", script_path],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=script_dir
        )
        debug(f"Node exit code: {result.returncode}")
        if result.stderr:
            debug(f"Node STDERR:\n{result.stderr}")
        if result.returncode != 0 or not result.stdout.strip():
            debug("Extraction failed – see stderr for details (captcha, missing elements, etc.)")
            return None
        master_url = result.stdout.strip()
        # If it's a direct video URL (not M3U8), return as is
        if not master_url.endswith('.m3u8'):
            debug(f"Direct video URL found (not HLS): {master_url}")
            return master_url
        # Otherwise upgrade quality from master playlist
        best = get_highest_bandwidth_url(master_url, proxy=proxy)
        return best
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
    result = extract_m3u8(page_url, referer)
    if result:
        print(f"M3U8_URL={result}")
        sys.exit(0)
    else:
        sys.exit(1)
