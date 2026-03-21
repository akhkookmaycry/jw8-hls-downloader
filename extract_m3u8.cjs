
const { chromium } = require('playwright');

(async () => {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    
    let m3u8Urls = new Set();
    
    // Intercept network requests
    page.on('request', request => {
        const url = request.url();
        if (url.includes('.m3u8')) {
            m3u8Urls.add(url);
        }
    });
    
    await page.goto('https://callistanise.com/v/715evv6rb8lg', { timeout: 30000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(6000);
    
    // Try JW8 player API
    try {
        const playlist = await page.evaluate(() => {
            if (typeof jwplayer !== 'undefined') {
                const pl = jwplayer().getPlaylist();
                if (pl && pl[0]) {
                    const sources = pl[0].sources || pl[0].allSources || [];
                    for (const s of sources) {
                        if (s.file) return s.file;
                    }
                }
            }
            return null;
        });
        
        if (playlist) {
            let url = playlist;
            if (!url.startsWith('http')) {
                // Convert relative to absolute
                const u = new URL('https://callistanise.com/v/715evv6rb8lg');
                url = u.origin + url;
            }
            m3u8Urls.add(url);
        }
    } catch(e) {}
    
    await browser.close();
    
    // Find master playlist
    const urls = Array.from(m3u8Urls);
    const master = urls.find(u => u.includes('master')) || urls[0];
    
    if (master) {
        console.log(master);
    } else {
        process.exit(1);
    }
})();
