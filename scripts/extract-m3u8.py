#!/usr/bin/env python3
"""
Ultimate diagnostic video extractor – safe JSON handling for window globals.
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
    debug("=== Ultimate Diagnostic Video Extractor ===")
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

// Safe JSON stringify that handles circular references
function safeStringify(obj, indent = 0) {{
    const seen = new WeakSet();
    return JSON.stringify(obj, (key, value) => {{
        if (typeof value === 'object' && value !== null) {{
            if (seen.has(value)) return '[Circular]';
            seen.add(value);
        }}
        // Truncate long strings
        if (typeof value === 'string' && value.length > 200) return value.substring(0, 200) + '...';
        return value;
    }}, indent);
}}

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
    
    let m3u8Urls = new Set();
    let videoUrls = new Set();
    let adPattern = /(?:^|[/_])(ad|preview|thumb|roomad|affiliates|promo|trailer|sample|demo)(?:$|[/_])/i;
    let allRequests = [];
    
    page.on('response', response => {{
        const url = response.url();
        const status = response.status();
        if (url.includes('.m3u8') || url.includes('.mp4') || url.includes('playlist')) {{
            console.error(`[NODE] Important response: ${{status}} - ${{url.substring(0, 100)}}`);
        }}
    }});
    
    page.on('request', request => {{
        const url = request.url();
        allRequests.push(url);
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] M3U8 request: ${{url}}`);
            m3u8Urls.add(url);
        }}
        if (/\\.(mp4|webm|mkv|ts|m2ts|mts)$/i.test(url)) {{
            console.error(`[NODE] Direct video request: ${{url.substring(0, 100)}}`);
            videoUrls.add(url);
        }}
    }});
    
    console.error(`[NODE] Navigating to {escaped_url}`);
    let response;
    try {{
        response = await page.goto('{escaped_url}', {{ waitUntil: 'networkidle', timeout: 45000 }});
        console.error(`[NODE] Navigation finished, final status: ${{response?.status() || 'unknown'}}`);
        console.error(`[NODE] Final URL: ${{page.url()}}`);
    }} catch(e) {{
        console.error(`[NODE] Navigation error: ${{e.message}}`);
        await browser.close();
        process.exit(1);
    }}
    
    const pageTitle = await page.title();
    console.error(`[NODE] Page title: "${{pageTitle}}"`);
    
    // ---- SAFE DIAGNOSTIC ANALYSIS (errors won't crash extraction) ----
    console.error('[NODE] ===== DETAILED PAGE ANALYSIS START =====');
    let pageAnalysis = null;
    try {{
        pageAnalysis = await page.evaluate(() => {{
            // Helper to safely get a snippet of an object
            function safeGet(obj, maxLen = 200) {{
                try {{
                    let str = JSON.stringify(obj);
                    if (str.length > maxLen) str = str.substring(0, maxLen) + '...';
                    return str;
                }} catch(e) {{
                    return '[Unable to stringify]';
                }}
            }}
            
            const result = {{
                bodyText: (document.body?.innerText || '').substring(0, 500),
                allIframes: [],
                allVideos: [],
                allScripts: [],
                windowGlobals: [],
                metaTags: [],
                locationHref: window.location.href,
                urlChanged: window.location.href !== '{escaped_url}'
            }};
            
            document.querySelectorAll('iframe').forEach((iframe, idx) => {{
                result.allIframes.push({{
                    src: iframe.src || 'NO SRC',
                    id: iframe.id || 'NO ID'
                }});
            }});
            
            document.querySelectorAll('video').forEach((video, idx) => {{
                result.allVideos.push({{
                    src: video.src || 'NO SRC',
                    id: video.id || 'NO ID'
                }});
            }});
            
            document.querySelectorAll('script').forEach((script, idx) => {{
                if (script.src && (script.src.includes('jwplayer') || script.src.includes('videojs'))) {{
                    result.allScripts.push({{ src: script.src }});
                }}
            }});
            
            // Window globals – safe, no circular JSON
            const importantGlobals = ['jwplayer', 'videojs', 'player', 'config', 'VIDEO_URL', 'MASTER_URL'];
            importantGlobals.forEach(global => {{
                if (typeof window[global] !== 'undefined') {{
                    let valueStr = '';
                    try {{
                        if (typeof window[global] === 'object') {{
                            valueStr = Object.keys(window[global]).slice(0, 5).join(', ');
                        }} else {{
                            valueStr = String(window[global]).substring(0, 100);
                        }}
                    }} catch(e) {{
                        valueStr = '[Error reading]';
                    }}
                    result.windowGlobals.push({{
                        name: global,
                        type: typeof window[global],
                        value: valueStr
                    }});
                }}
            }});
            
            document.querySelectorAll('meta').forEach(meta => {{
                if (meta.getAttribute('content')?.includes('http')) {{
                    result.metaTags.push({{
                        name: meta.getAttribute('name') || meta.getAttribute('property'),
                        content: meta.getAttribute('content').substring(0, 100)
                    }});
                }}
            }});
            
            return result;
        }});
    }} catch(e) {{
        console.error(`[NODE] ⚠️ Page analysis failed: ${{e.message}}`);
        pageAnalysis = {{ error: e.message }};
    }}
    
    if (pageAnalysis && !pageAnalysis.error) {{
        console.error(`[NODE] Page location: ${{pageAnalysis.locationHref}}`);
        console.error(`[NODE] URL changed: ${{pageAnalysis.urlChanged}}`);
        console.error(`[NODE] Iframes: ${{pageAnalysis.allIframes.length}}`);
        console.error(`[NODE] Video elements: ${{pageAnalysis.allVideos.length}}`);
        console.error(`[NODE] Window globals found: ${{pageAnalysis.windowGlobals.length}}`);
    }}
    console.error('[NODE] ===== DETAILED PAGE ANALYSIS END =====');
    
    // Wait a bit for any late video loads
    await page.waitForTimeout(5000);
    
    // Extract final video URLs from network requests (most reliable)
    await browser.close();
    
    console.error(`[NODE] Final summary: M3U8=${{m3u8Urls.size}}, Direct=${{videoUrls.size}}`);
    if (m3u8Urls.size > 0) {{
        let master = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
        console.log(master);
        return;
    }}
    
    if (videoUrls.size > 0) {{
        let candidates = Array.from(videoUrls).filter(url => !adPattern.test(url));
        if (candidates.length === 0) {{
            console.error('[NODE] No candidate direct URLs after ad filtering');
            process.exit(1);
        }}
        for (let url of candidates) console.log(url);
        return;
    }}
    
    console.error('[NODE] ❌ No video sources found – check network requests');
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
            debug("Extraction failed – see stderr for details")
            return None
        lines = result.stdout.strip().split('\n')
        if not lines:
            return None
        if lines[0].endswith('.m3u8'):
            best = get_highest_bandwidth_url(lines[0], proxy=proxy)
            return best
        # Check sizes of direct URLs
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