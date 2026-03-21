import Fastify from 'fastify';
import cors from '@fastify/cors';
import staticFiles from '@fastify/static';
import { chromium, Browser, Page } from 'playwright';
import { z } from 'zod';
import { join } from 'path';
import { exec, spawn } from 'child_process';
import { existsSync, mkdirSync, readdirSync, statSync, unlinkSync } from 'fs';
import { downloadHLS } from './hls-downloader';

const PORT = process.env.PORT ? parseInt(process.env.PORT) : 3456;
const HOST = process.env.HOST || '0.0.0.0';
const DOWNLOADS_DIR = join(import.meta.dir, '..', 'downloads');

// Ensure downloads directory exists
if (!existsSync(DOWNLOADS_DIR)) {
  mkdirSync(DOWNLOADS_DIR, { recursive: true });
}

// Track active downloads with persistence
interface DownloadStatus {
  id: string;
  url: string;
  quality: string;
  filename: string;
  status: 'downloading' | 'completed' | 'error' | 'retrying';
  progress: number;
  size: string;
  startTime: number;
  error?: string;
  pid?: number;
  retries?: number;
  retryCount?: number;
}

const activeDownloads = new Map<string, DownloadStatus>();
const downloadProcesses = new Map<number, any>();

// Cleanup old downloads periodically
setInterval(() => {
  const now = Date.now();
  for (const [id, dl] of activeDownloads) {
    // Remove completed/errored downloads older than 1 hour
    if (dl.status !== 'downloading' && now - dl.startTime > 3600000) {
      activeDownloads.delete(id);
    }
  }
}, 60000);

// Schemas
const ExtractRequestSchema = z.object({
  url: z.string().url('Invalid URL'),
  waitTime: z.number().min(1000).max(30000).optional().default(8000)
});

interface VideoSource {
  quality: string;
  resolution: string;
  bandwidth: number;
  url: string;
  type: 'hls' | 'mp4' | 'dash';
}

interface ExtractResult {
  success: boolean;
  videoId: string;
  title?: string;
  duration?: number;
  thumbnail?: string;
  subtitles: { language: string; url: string }[];
  sources: VideoSource[];
  masterUrl?: string;
  error?: string;
}

// JW8 Player config extraction
async function extractJW8Config(page: Page): Promise<any> {
  return await page.evaluate(() => {
    // Try JW8 API
    if (typeof (window as any).jwplayer !== 'undefined') {
      try {
        const player = (window as any).jwplayer();
        return {
          method: 'jwplayer_api',
          playlist: player.getPlaylist(),
          config: player.getConfig ? player.getConfig() : null
        };
      } catch (e) {
        return { method: 'jwplayer_api_error', error: (e as Error).message };
      }
    }
    return { method: 'not_found' };
  });
}

// Parse master.m3u8 to extract qualities
async function parseM3U8(masterUrl: string, referer: string): Promise<VideoSource[]> {
  try {
    const response = await fetch(masterUrl, {
      headers: {
        'Referer': referer,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
      }
    });
    
    if (!response.ok) {
      console.error(`Failed to fetch m3u8: ${response.status}`);
      return [];
    }
    
    const content = await response.text();
    const sources: VideoSource[] = [];
    
    // Parse EXT-X-STREAM-INF lines
    const lines = content.split('\n');
    let currentInfo: any = {};
    
    for (const line of lines) {
      if (line.startsWith('#EXT-X-STREAM-INF:')) {
        // Parse attributes
        const attrs: any = {};
        const attrStr = line.replace('#EXT-X-STREAM-INF:', '');
        const attrMatches = attrStr.matchAll(/([A-Z-]+)=("([^"]*)"|([^,]*))/g);
        
        for (const match of attrMatches) {
          const key = match[1].toLowerCase().replace(/-/g, '_');
          const value = match[3] || match[4];
          attrs[key] = value;
        }
        
        currentInfo = {
          resolution: attrs.resolution || 'unknown',
          bandwidth: parseInt(attrs.bandwidth) || 0,
          codecs: attrs.codecs || '',
          frameRate: attrs.frame_rate || ''
        };
      } else if (line.trim() && !line.startsWith('#') && currentInfo.resolution) {
        // This is the URL line
        const baseUrl = masterUrl.substring(0, masterUrl.lastIndexOf('/'));
        const streamUrl = line.startsWith('http') ? line : `${baseUrl}/${line}`;
        
        // Determine quality label from resolution
        let quality = 'unknown';
        if (currentInfo.resolution.includes('1920x1080')) quality = '1080p';
        else if (currentInfo.resolution.includes('1280x720')) quality = '720p';
        else if (currentInfo.resolution.includes('852x480') || currentInfo.resolution.includes('640x480')) quality = '480p';
        else if (currentInfo.resolution.includes('640x360')) quality = '360p';
        else if (currentInfo.resolution.includes('426x240')) quality = '240p';
        
        sources.push({
          quality,
          resolution: currentInfo.resolution,
          bandwidth: currentInfo.bandwidth,
          url: streamUrl,
          type: 'hls'
        });
        
        currentInfo = {};
      }
    }
    
    return sources;
  } catch (error) {
    console.error('Error parsing m3u8:', error);
    return [];
  }
}

// Main extraction function
async function extractVideoSources(targetUrl: string, waitTime: number): Promise<ExtractResult> {
  let browser: Browser | null = null;
  
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    });
    const page = await context.newPage();
    
    // Track M3U8 URLs
    const m3u8Urls = new Set<string>();
    let masterM3U8 = '';
    
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('.m3u8')) {
        m3u8Urls.add(url);
        if (url.includes('master')) {
          masterM3U8 = url;
        }
      }
    });
    
    // Navigate to page
    await page.goto(targetUrl, { timeout: 30000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(waitTime);
    
    // Extract JW8 config
    const jw8Config = await extractJW8Config(page);
    
    // Get title
    let title = '';
    try {
      title = await page.title();
    } catch (e) {}
    
    // Process JW8 config
    let masterUrl = masterM3U8;
    let duration = 0;
    let thumbnail = '';
    let subtitles: { language: string; url: string }[] = [];
    
    if (jw8Config.method === 'jwplayer_api' && jw8Config.playlist) {
      const playlist = jw8Config.playlist[0];
      if (playlist) {
        // Get master URL from playlist if not found via network
        if (!masterUrl && playlist.file) {
          masterUrl = playlist.file.startsWith('http') 
            ? playlist.file 
            : `https://${new URL(targetUrl).host}${playlist.file}`;
        }
        
        duration = playlist.duration || 0;
        thumbnail = playlist.image || '';
        
        // Extract subtitles
        if (playlist.tracks) {
          subtitles = playlist.tracks
            .filter((t: any) => t.kind === 'captions')
            .map((t: any) => ({
              language: t.label || t.srclang || 'unknown',
              url: t.file
            }));
        }
      }
    }
    
    // If we still don't have master URL, look in captured URLs
    if (!masterUrl && m3u8Urls.size > 0) {
      masterUrl = Array.from(m3u8Urls).find(u => u.includes('master')) || Array.from(m3u8Urls)[0];
    }
    
    // Parse master.m3u8 for qualities
    let sources: VideoSource[] = [];
    if (masterUrl) {
      sources = await parseM3U8(masterUrl, targetUrl);
    }
    
    // If no sources from master, create entries from found URLs
    if (sources.length === 0 && m3u8Urls.size > 0) {
      for (const url of m3u8Urls) {
        sources.push({
          quality: 'auto',
          resolution: 'auto',
          bandwidth: 0,
          url,
          type: 'hls'
        });
      }
    }
    
    // Extract video ID from URL
    const videoIdMatch = targetUrl.match(/\/v\/([a-zA-Z0-9]+)/);
    const videoId = videoIdMatch ? videoIdMatch[1] : 'unknown';
    
    await browser.close();
    
    return {
      success: true,
      videoId,
      title,
      duration,
      thumbnail,
      subtitles,
      sources,
      masterUrl
    };
    
  } catch (error) {
    if (browser) await browser.close();
    return {
      success: false,
      videoId: '',
      subtitles: [],
      sources: [],
      error: (error as Error).message
    };
  }
}

// Create Fastify server
async function main() {
  const fastify = Fastify({ logger: true });
  
  await fastify.register(cors, {
    origin: true,
    methods: ['GET', 'POST', 'OPTIONS']
  });
  
  // Static files
  await fastify.register(staticFiles, {
    root: join(import.meta.dir, '..', 'public'),
    prefix: '/'
  });
  
  // Health check
  fastify.get('/health', async () => {
    return { status: 'ok', service: 'jw8-extractor-api' };
  });
  
  // Extract video sources
  fastify.post('/api/extract', async (request, reply) => {
    try {
      const body = ExtractRequestSchema.parse(request.body);
      
      fastify.log.info(`Extracting from: ${body.url}`);
      
      const result = await extractVideoSources(body.url, body.waitTime);
      
      return reply.send(result);
    } catch (error) {
      if (error instanceof z.ZodError) {
        return reply.status(400).send({
          success: false,
          error: 'Validation error',
          details: error.errors
        });
      }
      
      return reply.status(500).send({
        success: false,
        error: (error as Error).message
      });
    }
  });
  
  // Quick extract - just get master.m3u8 URL
  fastify.get('/api/quick/:videoId', async (request, reply) => {
    const { videoId } = request.params as { videoId: string };
    
    return reply.send({
      message: 'Use POST /api/extract with full URL',
      example: {
        url: `https://callistanise.com/v/${videoId}`
      }
    });
  });

  // Download video with retry/resume support
  const DownloadRequestSchema = z.object({
    url: z.string().url(),
    quality: z.string().optional().default('auto'),
    filename: z.string().optional(),
    referer: z.string().optional(),
    resume: z.boolean().optional().default(true),
    retries: z.number().min(0).max(10).optional().default(5)
  });

  // Function to start a download with retry support
  const startDownload = (downloadStatus: DownloadStatus, retryCount: number = 0) => {
    const { url, quality, filename, referer } = downloadStatus;
    const outputPath = join(DOWNLOADS_DIR, filename);
    
    // Update status
    downloadStatus.status = 'downloading';
    downloadStatus.startTime = Date.now();
    
    const isHls = url.includes('.m3u8') || url.includes('.m3u');
    
    if (isHls) {
      // Use custom HLS downloader for non-standard extensions (like .image from TikTok CDN)
      downloadStatus.status = 'downloading';
      
      downloadHLS({
        url,
        outputPath,
        referer: referer || 'https://callistanise.com',
        maxRetries: downloadStatus.retries || 5,
        onProgress: (progress, size) => {
          downloadStatus.progress = progress;
          downloadStatus.size = size;
        },
        onStatus: (status) => {
          downloadStatus.size = status;
        }
      }).then(async (result) => {
        if (result.success) {
          downloadStatus.status = 'completed';
          downloadStatus.progress = 100;
          
          // Get final file size
          try {
            if (existsSync(outputPath)) {
              const stats = statSync(outputPath);
              const mb = stats.size / (1024 * 1024);
              downloadStatus.size = mb >= 1 ? `${mb.toFixed(2)} MB` : `${(stats.size / 1024).toFixed(0)} KB`;
            }
          } catch (e) {}
        } else {
          downloadStatus.status = 'error';
          downloadStatus.error = result.error;
        }
      }).catch((err) => {
        downloadStatus.status = 'error';
        downloadStatus.error = err.message;
      });
      
      // Return early for HLS - it runs async
      return downloadStatus;
      
    } else {
      // For non-HLS, use yt-dlp directly with retry
      const command = 'yt-dlp';
      const args = [
        '-o', outputPath,
        '--no-check-certificates',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '--referer', referer || '',
        '--retries', '10',
        '--file-access-retries', '5',
        '--resume',
        url
      ];

      const proc = spawn(command, args);
      downloadStatus.pid = proc.pid;
      downloadProcesses.set(proc.pid!, proc);
      
      let restartTimeout: NodeJS.Timeout | null = null;
      
      const parseOutput = (output: string) => {
        const ytDlpMatch = output.match(/\[download\]\s+(\d+\.?\d*)%.*?of\s+([\d.]+\w+)/);
        if (ytDlpMatch) {
          downloadStatus.progress = Math.min(99, parseFloat(ytDlpMatch[1]));
          downloadStatus.size = ytDlpMatch[2];
        }
        if (output.includes('retry') || output.includes('Retry')) {
          console.log(`[Retry] ${downloadStatus.id}: ${output.trim()}`);
        }
      };
      
      proc.stdout.on('data', (data: Buffer) => parseOutput(data.toString()));
      proc.stderr.on('data', (data: Buffer) => parseOutput(data.toString()));
      
      const sizeInterval = setInterval(() => {
        if (existsSync(outputPath)) {
          try {
            const stats = statSync(outputPath);
            const mb = stats.size / (1024 * 1024);
            downloadStatus.size = mb >= 1 ? `${mb.toFixed(2)} MB` : `${(stats.size / 1024).toFixed(0)} KB`;
          } catch (e) {}
        }
      }, 3000);
      
      proc.on('close', (code: number | null) => {
        clearInterval(sizeInterval);
        if (restartTimeout) clearTimeout(restartTimeout);
        downloadProcesses.delete(proc.pid!);
        
        if (code === 0 || code === null) {
          downloadStatus.status = 'completed';
          downloadStatus.progress = 100;
        } else {
          const maxRetries = downloadStatus.retries || 5;
          if (retryCount < maxRetries) {
            downloadStatus.status = 'retrying';
            downloadStatus.error = `Retrying (${retryCount + 1}/${maxRetries})...`;
            const delay = Math.min(30000, 5000 * Math.pow(2, retryCount));
            restartTimeout = setTimeout(() => startDownload(downloadStatus, retryCount + 1), delay);
          } else {
            downloadStatus.status = 'error';
            downloadStatus.error = `Failed after ${maxRetries} retries`;
          }
        }
      });
      
      proc.on('error', (err: Error) => {
        clearInterval(sizeInterval);
        downloadProcesses.delete(proc.pid!);
        downloadStatus.status = 'error';
        downloadStatus.error = err.message;
      });
    }
    
    return downloadStatus;
  };

  fastify.post('/api/download', async (request, reply) => {
    try {
      const body = DownloadRequestSchema.parse(request.body);
      const id = `dl_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      const filename = body.filename || `${id}_${body.quality}.mp4`;
      const outputPath = join(DOWNLOADS_DIR, filename);
      
      // Check if partial download exists (resume)
      if (body.resume && existsSync(`${outputPath}.part`)) {
        console.log(`[Resume] Found partial download for ${filename}`);
      }
      
      // Initialize download status
      const downloadStatus: DownloadStatus = {
        id,
        url: body.url,
        quality: body.quality,
        filename,
        status: 'downloading',
        progress: 0,
        size: '0 MB',
        startTime: Date.now(),
        retries: body.retries
      };
      activeDownloads.set(id, downloadStatus);

      // Start the download with retry support
      startDownload(downloadStatus, 0);

      return reply.send({
        success: true,
        id,
        message: 'Download started with auto-retry',
        checkProgress: `/api/download/${id}/status`,
        downloadFile: `/api/download/${id}/file`,
        retries: body.retries,
        resume: body.resume
      });

    } catch (error) {
      if (error instanceof z.ZodError) {
        return reply.status(400).send({
          success: false,
          error: 'Validation error',
          details: error.errors
        });
      }
      return reply.status(500).send({
        success: false,
        error: (error as Error).message
      });
    }
  });

  // Check download status
  fastify.get('/api/download/:id/status', async (request, reply) => {
    const { id } = request.params as { id: string };
    const status = activeDownloads.get(id);
    
    if (!status) {
      return reply.status(404).send({ error: 'Download not found' });
    }

    return reply.send({
      ...status,
      elapsed: Math.round((Date.now() - status.startTime) / 1000),
      fileUrl: status.status === 'completed' ? `/api/download/${id}/file` : null
    });
  });

  // Download completed file
  fastify.get('/api/download/:id/file', async (request, reply) => {
    const { id } = request.params as { id: string };
    const status = activeDownloads.get(id);
    
    if (!status) {
      return reply.status(404).send({ error: 'Download not found' });
    }
    
    if (status.status !== 'completed') {
      return reply.status(400).send({ error: 'Download not completed', status: status.status });
    }

    const filePath = join(DOWNLOADS_DIR, status.filename);
    if (!existsSync(filePath)) {
      return reply.status(404).send({ error: 'File not found' });
    }

    return reply
      .header('Content-Type', 'video/mp4')
      .header('Content-Disposition', `attachment; filename="${status.filename}"`)
      .send(require('fs').createReadStream(filePath));
  });

  // List all downloads
  fastify.get('/api/downloads', async () => {
    const downloads = Array.from(activeDownloads.values()).map(d => ({
      id: d.id,
      filename: d.filename,
      quality: d.quality,
      status: d.status,
      size: d.size,
      elapsed: Math.round((Date.now() - d.startTime) / 1000)
    }));
    
    // Also list files in downloads directory
    const files = existsSync(DOWNLOADS_DIR) 
      ? readdirSync(DOWNLOADS_DIR).filter(f => f.endsWith('.mp4'))
      : [];

    return { downloads, files };
  });

  // Cancel download
  fastify.delete('/api/download/:id', async (request, reply) => {
    const { id } = request.params as { id: string };
    const status = activeDownloads.get(id);
    
    if (!status) {
      return reply.status(404).send({ error: 'Download not found' });
    }

    // Kill process if still running
    if (status.pid && downloadProcesses.has(status.pid)) {
      const proc = downloadProcesses.get(status.pid);
      proc?.kill('SIGTERM');
      downloadProcesses.delete(status.pid);
    }
    
    status.status = 'error';
    status.error = 'Cancelled by user';
    activeDownloads.delete(id);
    
    return reply.send({ success: true, message: 'Download cancelled' });
  });

  // Manual resume/retry endpoint
  fastify.post('/api/download/:id/retry', async (request, reply) => {
    const { id } = request.params as { id: string };
    const status = activeDownloads.get(id);
    
    if (!status) {
      return reply.status(404).send({ error: 'Download not found' });
    }
    
    if (status.status === 'downloading') {
      return reply.status(400).send({ error: 'Download already in progress' });
    }
    
    // Reset status and restart
    status.status = 'downloading';
    status.error = undefined;
    status.retryCount = 0;
    
    // Restart download (will resume from partial file)
    startDownload(status, 0);
    
    return reply.send({ 
      success: true, 
      message: 'Download resumed',
      id: status.id
    });
  });
  
  // Start server
  try {
    const address = await fastify.listen({ port: PORT, host: HOST });
    fastify.log.info(`JW8 Extractor API running at ${address}`);
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
}

main();
