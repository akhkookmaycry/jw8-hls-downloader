#!/usr/bin/env python3
"""
Extract M3U8 URL from JW8 player page using Playwright
Enhanced with comprehensive debug logging.
"""

import sys
import subprocess
import json
import os
import traceback

def debug(msg):
    """Print debug message to stderr with [DEBUG] prefix"""
    print(f"[DEBUG] {msg}", file=sys.stderr)

def extract_m3u8(page_url, referer=""):
    """Extract M3U8 URL using Playwright"""

    debug("=== M3U8 Extractor Started ===")
    debug(f"Page URL: {page_url}")
    debug(f"Referer: {referer if referer else '(not provided)'}")

    # Quick check: if page_url already contains .m3u8, treat as direct URL
    if ".m3u8" in page_url.lower():
        debug("Page URL already contains '.m3u8' -> returning as is")
        return page_url

    # Prepare Node.js script content
    debug("Creating Node.js script for Playwright...")
    
    # Escape single quotes in page_url for embedding in JavaScript string
    escaped_url = page_url.replace("'", "\\'")
    
    node_script = f"""
const {{ chromium }} = require('playwright');

(async () => {{
    console.error('[NODE-DEBUG] Launching headless Chromium...');
    const browser = await chromium.launch({{ headless: true, args: ['--no-sandbox'] }});
    const page = await browser.newPage();
    
    let m3u8Urls = new Set();
    let requestCount = 0;
    
    // Intercept network requests
    page.on('request', request => {{
        requestCount++;
        const url = request.url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE-DEBUG] Captured .m3u8 request: ${{url}}`);
            m3u8Urls.add(url);
        }}
    }});
    
    console.error(`[NODE-DEBUG] Navigating to: {escaped_url}`);
    await page.goto('{escaped_url}', {{ timeout: 30000, waitUntil: 'domcontentloaded' }});
    console.error('[NODE-DEBUG] Page loaded (domcontentloaded)');
    
    console.error('[NODE-DEBUG] Waiting 6 seconds for player initialization...');
    await page.waitForTimeout(6000);
    console.error(`[NODE-DEBUG] Total requests seen: ${{requestCount}}`);
    console.error(`[NODE-DEBUG] M3U8 URLs captured so far: ${{Array.from(m3u8Urls).length}}`);
    
    // Try JW8 player API
    console.error('[NODE-DEBUG] Attempting to access jwplayer() API...');
    try {{
        const playlistInfo = await page.evaluate(() => {{
            if (typeof jwplayer !== 'undefined') {{
                console.log('jwplayer found');
                const pl = jwplayer().getPlaylist();
                if (pl && pl[0]) {{
                    const sources = pl[0].sources || pl[0].allSources || [];
                    for (const s of sources) {{
                        if (s.file) return {{ found: true, url: s.file }};
                    }}
                }}
                return {{ found: false, reason: 'no playlist or sources' }};
            }} else {{
                return {{ found: false, reason: 'jwplayer undefined' }};
            }}
        }});
        
        if (playlistInfo.found && playlistInfo.url) {{
            let url = playlistInfo.url;
            console.error(`[NODE-DEBUG] JW8 API returned: ${{url}}`);
            if (!url.startsWith('http')) {{
                // Convert relative to absolute
                const u = new URL('{escaped_url}');
                url = u.origin + url;
                console.error(`[NODE-DEBUG] Converted relative to absolute: ${{url}}`);
            }}
            m3u8Urls.add(url);
        }} else {{
            console.error(`[NODE-DEBUG] JW8 API gave no URL: ${{playlistInfo.reason || 'unknown'}}`);
        }}
    }} catch(e) {{
        console.error(`[NODE-DEBUG] Exception in JW8 evaluation: ${{e.message}}`);
    }}
    
    await browser.close();
    
    // Find master playlist (prefer 'master' in name, else first)
    const urls = Array.from(m3u8Urls);
    console.error(`[NODE-DEBUG] Final M3U8 URLs found: ${{urls.length}}`);
    for (let i = 0; i < urls.length; i++) {{
        console.error(`[NODE-DEBUG]   ${{i+1}}. ${{urls[i]}}`);
    }}
    
    const master = urls.find(u => u.includes('master')) || urls[0];
    
    if (master) {{
        console.log(master);
        console.error(`[NODE-DEBUG] Selected M3U8: ${{master}}`);
    }} else {{
        console.error('[NODE-DEBUG] No M3U8 URLs found');
        process.exit(1);
    }}
}})();
"""

    # Determine MCP directory (where this script's parent is one level above scripts/)
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(script_dir, "extract_m3u8.cjs")
    debug(f"Will write Node script to: {script_path}")
    debug(f"Working directory for subprocess: {script_dir}")

    # Write the Node.js script
    try:
        with open(script_path, "w") as f:
            f.write(node_script)
        debug("Node script written successfully")
    except Exception as e:
        debug(f"Failed to write Node script: {str(e)}")
        debug(traceback.format_exc())
        return None

    # Try running with bun, then node
    for cmd in ["bun", "node"]:
        debug(f"Trying to run with: {cmd}")
        try:
            # Check if command exists
            which_result = subprocess.run(
                ["which", cmd], capture_output=True, text=True
            )
            if which_result.returncode != 0:
                debug(f"Command '{cmd}' not found in PATH, skipping")
                continue
            debug(f"Found {cmd} at: {which_result.stdout.strip()}")

            # Run the Node script
            debug(f"Executing: {cmd} {script_path}")
            result = subprocess.run(
                [cmd, script_path],
                capture_output=True,
                text=True,
                timeout=45,
                cwd=script_dir,
            )

            debug(f"Subprocess exit code: {result.returncode}")
            if result.stdout:
                debug(f"STDOUT (first 200 chars): {result.stdout[:200]}")
            if result.stderr:
                debug(f"STDERR (first 500 chars): {result.stderr[:500]}")
                # Full stderr for debugging (but avoid flooding if huge)
                if len(result.stderr) > 500:
                    debug(f"Full STDERR length: {len(result.stderr)} bytes (truncated above)")

            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip()
                debug(f"Successfully extracted M3U8 URL: {url[:100]}...")
                # Clean up the temporary script
                try:
                    os.remove(script_path)
                    debug(f"Removed temporary script: {script_path}")
                except:
                    pass
                return url
            else:
                debug(f"Command {cmd} returned non-zero or empty stdout")
                if result.stderr:
                    debug(f"Last lines of stderr from {cmd}:")
                    for line in result.stderr.strip().split('\n')[-10:]:
                        debug(f"  {line}")

        except subprocess.TimeoutExpired:
            debug(f"Command {cmd} timed out after 45 seconds")
        except FileNotFoundError:
            debug(f"Command {cmd} not found (FileNotFoundError)")
        except Exception as e:
            debug(f"Unexpected error running {cmd}: {str(e)}")
            debug(traceback.format_exc())

    debug("All attempts failed to extract M3U8 URL")
    # Clean up leftover script if any
    try:
        if os.path.exists(script_path):
            os.remove(script_path)
            debug(f"Removed leftover script: {script_path}")
    except:
        pass
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract-m3u8.py <page_url> [referer]", file=sys.stderr)
        sys.exit(1)

    page_url = sys.argv[1]
    referer = sys.argv[2] if len(sys.argv) > 2 else ""

    debug(f"Script called with: page_url='{page_url}', referer='{referer}'")
    result = extract_m3u8(page_url, referer)

    if result:
        # Output for GitHub Actions (stdout only)
        print(f"M3U8_URL={result}")
        debug("Extraction complete, exiting with 0")
        sys.exit(0)
    else:
        debug("Extraction failed, exiting with 1")
        sys.exit(1)
