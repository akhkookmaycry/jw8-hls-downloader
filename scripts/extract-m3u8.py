#!/usr/bin/env python3
"""
Universal video extractor – works for HLS, direct video, ads, any player.
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
    if not master_url.endswith('.m3u8'):
        return master_url
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
    return best_url if best_url else master_url

def extract_video_url(page_url, referer=""):
    debug("=== Universal Video Extractor ===")
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
    let videoUrlCount = new Map();
    let adPattern = /(?:^|[/_])(ad|preview|thumb|roomad|affiliates|promo|trailer)(?:$|[/_])/i;
    
    // Intercept network
    page.on('request', request => {{
        const url = request.url();
        if (url.includes('.m3u8')) {{
            console.error(`[NODE] M3U8: ${{url}}`);
            m3u8Urls.add(url);
        }}
        if (/\\.(mp4|webm|mkv|ts)$/i.test(url)) {{
            console.error(`[NODE] Video request: ${{url}}`);
            let count = videoUrlCount.get(url) || 0;
            videoUrlCount.set(url, count + 1);
        }}
    }});
    page.on('response', response => {{
        if (response.status() >= 400)
            console.error(`[NODE] HTTP ${{response.status()}} for ${{response.url()}}`);
    }});
    page.on('requestfailed', req => {{
        console.error(`[NODE] Failed request: ${{req.url()}} - ${{req.failure()?.errorText || ''}}`);
    }});
    
    // Navigate
    console.error(`[NODE] Navigating to {escaped_url}`);
    await page.goto('{escaped_url}', {{ waitUntil: 'domcontentloaded', timeout: 30000 }});
    console.error(`[NODE] Page title: "${{await page.title()}}"`);
    
    // Detect captcha/block
    const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
    if (/captcha|access denied|blocked|unusual traffic/i.test(bodyText))
        console.error(`[NODE] WARNING: Possible CAPTCHA/block detected`);
    
    // Click to start video (handles overlays)
    console.error('[NODE] Attempting to click on video player...');
    const clickSelectors = [
        'video', '.jwplayer', '.video-js', '.player', '[id*=player]', 
        '[class*=video]', '[class*=player]', '#main-video', '.html5-video-container'
    ];
    let clicked = false;
    for (const sel of clickSelectors) {{
        if (await page.$(sel)) {{
            await page.click(sel, {{ timeout: 2000 }}).catch(() => {{}});
            console.error(`[NODE] Clicked selector: ${{sel}}`);
            clicked = true;
            break;
        }}
    }}
    if (!clicked) {{
        console.error('[NODE] No clickable player found – clicking center of page');
        await page.mouse.click(await page.evaluate(() => window.innerWidth/2), 
                               await page.evaluate(() => window.innerHeight/2));
    }}
    
    // Wait for video to start loading
    console.error('[NODE] Waiting 8 seconds for video initialization...');
    await page.waitForTimeout(8000);
    
    // Additional clicks for stubborn players
    for (let i = 0; i < 2; i++) {{
        await page.click('video', {{ timeout: 1000 }}).catch(() => {{}});
        await page.waitForTimeout(2000);
    }}
    
    // Try to get video src from DOM
    const videoSrc = await page.evaluate(() => {{
        const v = document.querySelector('video');
        if (v && v.src && v.src.length > 0) return v.src;
        const sources = Array.from(document.querySelectorAll('video source'));
        if (sources.length) return sources[0].src;
        return null;
    }});
    if (videoSrc && !adPattern.test(videoSrc)) {{
        console.error(`[NODE] Video src from DOM: ${{videoSrc}}`);
        if (videoSrc.includes('.m3u8')) m3u8Urls.add(videoSrc);
        else {{
            let count = videoUrlCount.get(videoSrc) || 0;
            videoUrlCount.set(videoSrc, count + 1);
        }}
    }}
    
    // JWPlayer API
    try {{
        const jw = await page.evaluate(() => {{
            if (typeof jwplayer !== 'undefined') {{
                const pl = jwplayer().getPlaylist();
                if (pl && pl[0] && pl[0].file) return pl[0].file;
            }}
            return null;
        }});
        if (jw) {{
            console.error(`[NODE] JWPlayer API: ${{jw}}`);
            if (jw.includes('.m3u8')) m3u8Urls.add(jw);
            else {{
                let c = videoUrlCount.get(jw) || 0;
                videoUrlCount.set(jw, c + 1);
            }}
        }}
    }} catch(e) {{ console.error(`[NODE] JW error: ${{e.message}}`); }}
    
    // Video.js player
    try {{
        const vjs = await page.evaluate(() => {{
            if (window.videojs && window.videojs.players && Object.keys(window.videojs.players).length) {{
                const player = Object.values(window.videojs.players)[0];
                return player.currentSource ? player.currentSource().src : null;
            }}
            return null;
        }});
        if (vjs) {{
            console.error(`[NODE] Video.js src: ${{vjs}}`);
            if (vjs.includes('.m3u8')) m3u8Urls.add(vjs);
            else {{
                let c = videoUrlCount.get(vjs) || 0;
                videoUrlCount.set(vjs, c + 1);
            }}
        }}
    }} catch(e) {{}}
    
    await page.waitForTimeout(2000);
    await browser.close();
    
    // Choose best video URL
    console.error(`[NODE] M3U8 URLs: ${{m3u8Urls.size}}, distinct direct: ${{videoUrlCount.size}}`);
    let bestDirect = null;
    let bestCount = 0;
    for (let [url, count] of videoUrlCount.entries()) {{
        if (adPattern.test(url)) {{
            console.error(`[NODE] Skipping ad: ${{url.substring(0, 80)}}`);
            continue;
        }}
        console.error(`[NODE] Candidate (count=${{count}}): ${{url.substring(0, 80)}}`);
        if (count > bestCount) {{
            bestCount = count;
            bestDirect = url;
        }}
    }}
    
    let finalUrl = null;
    if (m3u8Urls.size) {{
        finalUrl = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
        console.error(`[NODE] Using M3U8: ${{finalUrl}}`);
    }} else if (bestDirect) {{
        finalUrl = bestDirect;
        console.error(`[NODE] Using direct video (frequency ${{bestCount}}): ${{finalUrl}}`);
    }} else {{
        console.error('[NODE] No video URL found');
        process.exit(1);
    }}
    console.log(finalUrl);
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
            timeout=60,
            cwd=script_dir
        )
        debug(f"Node exit: {result.returncode}")
        if result.stderr:
            debug(f"Node stderr:\n{result.stderr}")
        if result.returncode != 0 or not result.stdout.strip():
            return None
        url = result.stdout.strip()
        # Upgrade master playlist to highest quality if HLS
        if url.endswith('.m3u8'):
            url = get_highest_bandwidth_url(url, proxy=proxy)
        return url
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
