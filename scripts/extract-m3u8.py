#!/usr/bin/env python3
"""
Extract M3U8 URL from JW8 player – fast network capture + highest quality upgrade.
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

def get_highest_bandwidth_url(master_url):
    """Fetch master playlist and return best variant URL."""
    debug(f"Fetching master playlist for quality upgrade: {master_url}")
    try:
        req = urllib.request.Request(master_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        debug(f"Failed to fetch master: {e}")
        return master_url  # fallback to original

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
    debug("=== M3U8 Extractor Started (Fast Capture) ===")
    debug(f"Page URL: {page_url}")
    debug(f"Referer: {referer if referer else '(none)'}")

    # If it's already an M3U8, return as is
    if ".m3u8" in page_url.lower():
        debug("Already an M3U8 URL")
        return page_url

    # Create Node.js script (same as before, but with forced timeout)
    escaped_url = page_url.replace("'", "\\'")
    node_script = f"""
const {{ chromium }} = require('playwright');

(async () => {{
    console.error('[NODE] Launching browser...');
    const browser = await chromium.launch({{ headless: true, args: ['--no-sandbox'] }});
    const page = await browser.newPage();
    let m3u8Urls = new Set();
    
    page.on('request', request => {{
        const url = request.url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] Captured: ${{url}}`);
            m3u8Urls.add(url);
        }}
    }});
    
    // Set a timeout to force close after 20 seconds
    const timeout = setTimeout(async () => {{
        console.error('[NODE] Timeout reached, closing browser');
        await browser.close();
        process.exit(1);
    }}, 20000);
    
    await page.goto('{escaped_url}', {{ timeout: 15000, waitUntil: 'domcontentloaded' }});
    await page.waitForTimeout(6000);
    
    // Try JW8 API (optional)
    try {{
        const playlist = await page.evaluate(() => {{
            if (typeof jwplayer !== 'undefined') {{
                const pl = jwplayer().getPlaylist();
                if (pl && pl[0] && pl[0].file) return pl[0].file;
            }}
            return null;
        }});
        if (playlist && playlist.includes('.m3u8')) {{
            console.error(`[NODE] JW API returned: ${{playlist}}`);
            m3u8Urls.add(playlist);
        }}
    }} catch(e) {{}}
    
    clearTimeout(timeout);
    await browser.close();
    
    const urls = Array.from(m3u8Urls);
    console.error(`[NODE] Total M3U8 URLs found: ${{urls.length}}`);
    // Prefer a master playlist
    let master = urls.find(u => u.includes('master') || u.includes('playlist.m3u8'));
    if (!master && urls.length) master = urls[0];
    if (master) {{
        console.log(master);
    }} else {{
        process.exit(1);
    }}
}})();
"""

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(script_dir, "extract_m3u8.cjs")
    debug(f"Writing Node script to {script_path}")
    with open(script_path, "w") as f:
        f.write(node_script)

    # Try to run with node only (bun is not installed)
    cmd = "node"
    debug(f"Running {cmd} {script_path}")
    try:
        result = subprocess.run(
            [cmd, script_path],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=script_dir
        )
        debug(f"Subprocess exit code: {result.returncode}")
        if result.stderr:
            debug(f"Node STDERR (first 500 chars): {result.stderr[:500]}")
        if result.returncode != 0 or not result.stdout.strip():
            debug("Node script failed or produced no output")
            return None
        master_url = result.stdout.strip()
        debug(f"Raw extracted master URL: {master_url}")
        # Upgrade to highest quality
        best_url = get_highest_bandwidth_url(master_url)
        return best_url
    except subprocess.TimeoutExpired:
        debug("Node script timed out after 45 seconds")
        return None
    except Exception as e:
        debug(f"Exception running subprocess: {e}")
        return None
    finally:
        # Clean up temp script
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
    debug(f"Script called with page_url='{page_url}', referer='{referer}'")
    result = extract_m3u8(page_url, referer)
    if result:
        print(f"M3U8_URL={result}")
        sys.exit(0)
    else:
        sys.exit(1)
