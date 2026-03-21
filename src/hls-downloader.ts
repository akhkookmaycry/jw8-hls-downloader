/**
 * Custom HLS downloader for non-standard extensions
 * Downloads segments directly and merges with ffmpeg
 */

import { existsSync, mkdirSync, writeFileSync, readFileSync, readdirSync, statSync, unlinkSync } from 'fs';
import { join } from 'path';
import { execSync, spawn } from 'child_process';

export interface DownloadOptions {
  url: string;
  outputPath: string;
  referer?: string;
  onProgress?: (progress: number, size: string) => void;
  onStatus?: (status: string) => void;
  maxRetries?: number;
}

export interface M3U8Segment {
  url: string;
  duration: number;
  index: number;
}

/**
 * Fetch M3U8 content with headers
 */
async function fetchM3U8(url: string, referer: string): Promise<string> {
  const response = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Referer': referer,
      'Accept': '*/*',
      'Accept-Language': 'en-US,en;q=0.9',
    }
  });
  
  if (!response.ok) {
    throw new Error(`Failed to fetch M3U8: ${response.status} ${response.statusText}`);
  }
  
  return response.text();
}

/**
 * Parse M3U8 content and extract segments
 */
function parseM3U8(content: string, baseUrl: string): M3U8Segment[] {
  const lines = content.split('\n');
  const segments: M3U8Segment[] = [];
  let currentIndex = 0;
  let currentDuration = 10;
  
  for (const line of lines) {
    const trimmed = line.trim();
    
    // Parse duration
    if (trimmed.startsWith('#EXTINF:')) {
      const match = trimmed.match(/#EXTINF:([\d.]+)/);
      if (match) {
        currentDuration = parseFloat(match[1]);
      }
      continue;
    }
    
    // Skip comments and empty lines
    if (trimmed.startsWith('#') || !trimmed) {
      continue;
    }
    
    // This is a segment URL
    let segmentUrl = trimmed;
    if (!segmentUrl.startsWith('http')) {
      // Relative URL - resolve against base
      const base = baseUrl.endsWith('/') ? baseUrl : baseUrl + '/';
      segmentUrl = base + segmentUrl;
    }
    
    segments.push({
      url: segmentUrl,
      duration: currentDuration,
      index: currentIndex++
    });
    
    currentDuration = 10; // Reset to default
  }
  
  return segments;
}

/**
 * Download a single segment with retry
 */
async function downloadSegment(
  url: string, 
  outputPath: string, 
  referer: string,
  retries: number = 3
): Promise<boolean> {
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const response = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Referer': referer,
          'Accept': '*/*',
        },
        signal: AbortSignal.timeout(30000) // 30 second timeout
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const buffer = Buffer.from(await response.arrayBuffer());
      writeFileSync(outputPath, buffer);
      return true;
    } catch (error) {
      console.log(`Segment download attempt ${attempt + 1}/${retries} failed: ${(error as Error).message}`);
      if (attempt < retries - 1) {
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1))); // Exponential backoff
      }
    }
  }
  return false;
}

/**
 * Download HLS stream with custom segment downloader
 */
export async function downloadHLS(options: DownloadOptions): Promise<{ success: boolean; error?: string }> {
  const { url, outputPath, referer = '', onProgress, onStatus, maxRetries = 3 } = options;
  
  // Create temp directory for segments
  const tempDir = outputPath.replace(/\.mp4$/, '_segments');
  if (!existsSync(tempDir)) {
    mkdirSync(tempDir, { recursive: true });
  }
  
  try {
    onStatus?.('Fetching M3U8 playlist...');
    
    // Handle master playlist (find best quality)
    let playlistUrl = url;
    const masterContent = await fetchM3U8(url, referer);
    
    // Check if this is a master playlist (has multiple qualities)
    if (masterContent.includes('#EXT-X-STREAM-INF')) {
      onStatus?.('Parsing master playlist for best quality...');
      const lines = masterContent.split('\n');
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].includes('#EXT-X-STREAM-INF') && i + 1 < lines.length) {
          const nextLine = lines[i + 1].trim();
          if (nextLine && !nextLine.startsWith('#')) {
            // This is the first (usually best) quality
            playlistUrl = nextLine.startsWith('http') ? nextLine : new URL(nextLine, url).href;
            break;
          }
        }
      }
    }
    
    // Fetch the actual segment playlist
    const playlistContent = await fetchM3U8(playlistUrl, referer);
    const segments = parseM3U8(playlistContent, playlistUrl);
    
    if (segments.length === 0) {
      return { success: false, error: 'No segments found in M3U8' };
    }
    
    onStatus?.(`Found ${segments.length} segments. Starting download...`);
    
    // Download segments
    let downloadedSize = 0;
    let failedSegments = 0;
    
    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i];
      const segmentPath = join(tempDir, `segment_${String(i).padStart(6, '0')}.ts`);
      
      const success = await downloadSegment(segment.url, segmentPath, referer, maxRetries);
      
      if (success) {
        const stats = statSync(segmentPath);
        downloadedSize += stats.size;
      } else {
        failedSegments++;
        console.log(`Failed to download segment ${i}`);
      }
      
      // Update progress
      const progress = Math.round(((i + 1) / segments.length) * 100);
      const sizeMB = (downloadedSize / (1024 * 1024)).toFixed(2);
      onProgress?.(progress, `${sizeMB} MB`);
      
      // Small delay to avoid overwhelming the server
      if (i % 10 === 0) {
        await new Promise(r => setTimeout(r, 100));
      }
    }
    
    if (failedSegments > segments.length / 2) {
      return { success: false, error: `Too many failed segments (${failedSegments}/${segments.length})` };
    }
    
    onStatus?.('Merging segments with ffmpeg...');
    
    // Create concat file for ffmpeg
    const fileListPath = join(tempDir, 'filelist.txt');
    const fileContent = segments
      .map((_, i) => `file 'segment_${String(i).padStart(6, '0')}.ts'`)
      .join('\n');
    writeFileSync(fileListPath, fileContent);
    
    // Use ffmpeg to merge segments
    await new Promise<void>((resolve, reject) => {
      const ffmpeg = spawn('ffmpeg', [
        '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', fileListPath,
        '-c', 'copy',
        outputPath
      ]);
      
      ffmpeg.on('close', (code) => {
        if (code === 0) resolve();
        else reject(new Error(`ffmpeg exited with code ${code}`));
      });
      
      ffmpeg.on('error', reject);
    });
    
    // Cleanup temp directory
    const files = readdirSync(tempDir);
    for (const file of files) {
      unlinkSync(join(tempDir, file));
    }
    require('fs').rmdirSync(tempDir);
    
    onStatus?.('Download complete!');
    return { success: true };
    
  } catch (error) {
    return { success: false, error: (error as Error).message };
  }
}
