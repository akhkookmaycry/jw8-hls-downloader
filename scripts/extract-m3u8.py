#!/usr/bin/env python3
"""
Extract M3U8 URL from JW8 player page using Playwright
"""

import sys
import subprocess
import json
import os


def extract_m3u8(page_url, referer=""):
    """Extract M3U8 URL using Playwright"""

    print(f"=== M3U8 Extractor ===", file=sys.stderr)
    print(f"Page: {page_url[:60]}...", file=sys.stderr)

    if ".m3u8" in page_url.lower():
        print("Already an M3U8 URL", file=sys.stderr)
        return page_url

    # Create Node.js script
    node_script = f"""
const {{ chromium }} = require('playwright');

(async () => {{
    const browser = await chromium.launch({{ headless: true }});
    const page = await browser.newPage();
    
    let m3u8Urls = new Set();
    
    // Intercept network requests
    page.on('request', request => {{
        const url = request.url();
        if (url.includes('.m3u8')) {{
            m3u8Urls.add(url);
        }}
    }});
    
    await page.goto('{page_url}', {{ timeout: 30000, waitUntil: 'domcontentloaded' }});
    await page.waitForTimeout(6000);
    
    // Try JW8 player API
    try {{
        const playlist = await page.evaluate(() => {{
            if (typeof jwplayer !== 'undefined') {{
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
            let url = playlist;
            if (!url.startsWith('http')) {{
                // Convert relative to absolute
                const u = new URL('{page_url}');
                url = u.origin + url;
            }}
            m3u8Urls.add(url);
        }}
    }} catch(e) {{}}
    
    await browser.close();
    
    // Find master playlist
    const urls = Array.from(m3u8Urls);
    const master = urls.find(u => u.includes('master')) || urls[0];
    
    if (master) {{
        console.log(master);
    }} else {{
        process.exit(1);
    }}
}})();
"""

    # Write script to MCP directory so playwright can be found
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(script_dir, "extract_m3u8.cjs")

    with open(script_path, "w") as f:
        f.write(node_script)

    # Run with bun or node
    for cmd in ["bun", "node"]:
        try:
            result = subprocess.run(
                [cmd, script_path],
                capture_output=True,
                text=True,
                timeout=45,
                cwd=script_dir,  # Run from MCP directory
            )
            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip()
                print(f"✅ Found: {url[:80]}...", file=sys.stderr)
                return url
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    print("❌ Could not extract M3U8 URL", file=sys.stderr)
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract-m3u8.py <page_url> [referer]")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract-m3u8.py <page_url> [referer]")
        sys.exit(1)

    page_url = sys.argv[1]
    referer = sys.argv[2] if len(sys.argv) > 2 else ""

    result = extract_m3u8(page_url, referer)

    if result:
        # Output for GitHub Actions (stdout only)
        print(f"M3U8_URL={result}")
    else:
        sys.exit(1)
