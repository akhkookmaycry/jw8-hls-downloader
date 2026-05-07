#!/usr/bin/env python3
"""
Extract M3U8 URL from JW8 player – supports optional SOCKS5 proxy (WARP)
and falls back to direct connection. Enhanced debug logging.
"""

import sys
import subprocess
import os
import re
import urllib.request
import urllib.error
from urllib.parse import urljoin

def debug(msg):
    print(f"[EXTRACT-DEBUG] {msg}", file=sys.stderr)

def get_highest_bandwidth_url(master_url, proxy=None):
    """Fetch master playlist and return best variant URL."""
    debug(f"Fetching master playlist for quality upgrade: {master_url}")
    try:
        if proxy:
            proxy_handler = urllib.request.ProxyHandler({'socks5': proxy})
            opener = urllib.request.build_opener(proxy_handler)
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
                    variant_url = lines[i+1].strip()
                    if variant_url and not variant_url.startswith('#'):
                        full_url = urljoin(master_url, variant_url)
                        debug(f"Variant bandwidth {bw}: {full_url[:100]}")
                        if bw > best_bw:
                            best_bw = bw
                            best_url = full_url
    if best_url:
        debug(f"Selected highest quality: {best_url} (bandwidth {best_bw})")
        return best_url
    debug("No variants found, using original master URL")
    return master_url

def extract_m3u8(page_url, referer=""):
    debug("=== M3U8 Extractor Started ===")
    debug(f"Page URL: {page_url}")
    debug(f"Referer: {referer if referer else '(none)'}")
    use_warp = os.environ.get('USE_WARP', '').lower() == 'true'
    proxy = "socks5://127.0.0.1:1080" if use_warp else None
    debug(f"WARP enabled: {use_warp} (proxy: {proxy})")

    # Direct M3U8?
    if ".m3u8" in page_url.lower():
        debug("Already an M3U8 URL")
        return page_url

    # Build Node.js script with extensive debug output
    escaped_url = page_url.replace("'", "\\'")
    proxy_config = f", proxy: {{ server: '{proxy}' }}" if proxy else ""
    node_script = f"""
const {{ chromium }} = require('playwright');

(async () => {{
    console.error('[NODE] Launching browser with args: --no-sandbox{proxy_config}');
    const browser = await chromium.launch({{
        headless: true,
        args: ['--no-sandbox']{proxy_config}
    }});
    console.error('[NODE] Browser launched');
    const page = await browser.newPage();
    console.error('[NODE] New page created');
    
    let m3u8Urls = new Set();
    
    page.on('request', request => {{
        const url = request.url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] Captured M3U8 request: ${{url}}`);
            m3u8Urls.add(url);
        }}
    }});
    
    console.error(`[NODE] Navigating to: {escaped_url}`);
    try {{
        await page.goto('{escaped_url}', {{ timeout: 30000, waitUntil: 'domcontentloaded' }});
        console.error('[NODE] Page loaded (domcontentloaded)');
    }} catch(e) {{
        console.error(`[NODE] Navigation error: ${{e.message}}`);
        await browser.close();
        process.exit(1);
    }}
    
    console.error('[NODE] Waiting 6 seconds for player initialization...');
    await page.waitForTimeout(6000);
    
    // Try JW8 API
    try {{
        const playlist = await page.evaluate(() => {{
            if (typeof jwplayer !== 'undefined') {{
                console.log('jwplayer found');
                const pl = jwplayer().getPlaylist();
                if (pl && pl[0]) {{
                    const sources = pl[0].sources || pl[0].allSources || [];
                    for (const s of sources) {{
                        if (s.file) return s.file;
                    }}
                }}
            }}
            return null;
        }});
        if (playlist) {{
            console.error(`[NODE] JW API returned: ${{playlist}}`);
            let finalUrl = playlist;
            if (!finalUrl.startsWith('http')) {{
                const u = new URL('{escaped_url}');
                finalUrl = u.origin + finalUrl;
                console.error(`[NODE] Converted relative to absolute: ${{finalUrl}}`);
            }}
            m3u8Urls.add(finalUrl);
        }} else {{
            console.error('[NODE] JW API did not return a playlist URL');
        }}
    }} catch(e) {{
        console.error(`[NODE] JW API error: ${{e.message}}`);
    }}
    
    await browser.close();
    console.error(`[NODE] Total M3U8 URLs found: ${{m3u8Urls.size}}`);
    const urls = Array.from(m3u8Urls);
    urls.forEach((u, i) => console.error(`[NODE] URL ${{i+1}}: ${{u}}`));
    
    const master = urls.find(u => u.includes('master')) || urls[0];
    if (master) {{
        console.log(master);
        console.error(`[NODE] Returning: ${{master}}`);
    }} else {{
        console.error('[NODE] No M3U8 URL found');
        process.exit(1);
    }}
}})();
"""

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(script_dir, "extract_m3u8.cjs")
    debug(f"Writing Node script to {script_path}")
    with open(script_path, "w") as f:
        f.write(node_script)

    try:
        # Run node
        cmd = ["node", script_path]
        debug(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
            cwd=script_dir
        )
        debug(f"Node exit code: {result.returncode}")
        if result.stdout:
            debug(f"Node STDOUT: {result.stdout.strip()}")
        if result.stderr:
            debug(f"Node STDERR (full):\n{result.stderr}")
        else:
            debug("Node STDERR: (empty)")
        
        if result.returncode != 0:
            debug("Node script failed (non-zero exit code)")
            return None
        if not result.stdout.strip():
            debug("Node script produced no output on stdout")
            return None

        master_url = result.stdout.strip()
        debug(f"Extracted master URL: {master_url[:100]}")
        best_url = get_highest_bandwidth_url(master_url, proxy=proxy)
        return best_url

    except subprocess.TimeoutExpired:
        debug("Node script timed out after 45 seconds")
        return None
    except Exception as e:
        debug(f"Exception running subprocess: {e}")
        import traceback
        debug(traceback.format_exc())
        return None
    finally:
        try:
            os.remove(script_path)
            debug(f"Removed temporary script: {script_path}")
        except Exception as e:
            debug(f"Failed to remove script: {e}")

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
