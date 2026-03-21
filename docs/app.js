// JW8 HLS Downloader - Frontend App
// GitHub Pages + GitHub Actions + Cloudflare R2

const GITHUB_REPO = 'MoriNo23/jw8-hls-downloader';
const R2_PUBLIC_DOMAIN = 'https://pub-b882a320af94657775eed8c548f99845.r2.dev';

// Elements
const startBtn = document.getElementById('startBtn');
const statusBox = document.getElementById('statusBox');
const statusText = document.getElementById('statusText');
const progressFill = document.getElementById('progressFill');
const runInfo = document.getElementById('runInfo');
const downloadBtn = document.getElementById('downloadBtn');

// State
let pollInterval = null;

function showStatus(message, progress = 0) {
  statusBox.classList.add('show');
  statusText.innerHTML = `<span class="spinner"></span> ${message}`;
  progressFill.style.width = `${progress}%`;
}

function showSuccess(message, downloadUrl) {
  statusText.innerHTML = `✅ ${message}`;
  progressFill.style.width = '100%';
  
  downloadBtn.href = downloadUrl;
  downloadBtn.classList.add('show');
}

function showError(message) {
  statusText.innerHTML = `❌ ${message}`;
  statusText.style.color = '#ef4444';
  progressFill.style.width = '0%';
  startBtn.disabled = false;
}

function generateRunId() {
  return `hls_${Date.now()}`;
}

async function startDownload() {
  const m3u8Url = document.getElementById('m3u8_url').value.trim();
  const outputName = document.getElementById('output_name').value.trim() || 'video.mp4';
  const referer = document.getElementById('referer').value.trim();
  
  if (!m3u8Url) {
    alert('Por favor ingresa una URL M3U8');
    return;
  }
  
  // Validate M3U8 URL
  if (!m3u8Url.includes('.m3u8') && !m3u8Url.includes('.m3u')) {
    if (!confirm('La URL no parece ser un archivo M3U8. ¿Continuar de todas formas?')) {
      return;
    }
  }
  
  startBtn.disabled = true;
  downloadBtn.classList.remove('show');
  statusText.style.color = '';
  
  showStatus('Iniciando workflow de GitHub Actions...', 5);
  runInfo.textContent = `Archivo: ${outputName}`;
  
  const runId = generateRunId();
  
  // Note: This requires GitHub token with workflow trigger permissions
  // For public repos without token, users should use the Actions page directly
  
  // Show instructions for manual trigger
  showManualTriggerInstructions(m3u8Url, outputName, referer);
}

function showManualTriggerInstructions(m3u8Url, outputName, referer) {
  const actionsUrl = `https://github.com/${GITHUB_REPO}/actions/workflows/download-hls.yml`;
  
  statusBox.innerHTML = `
    <div style="color: #00d4ff; margin-bottom: 16px;">
      <strong>📋 Instrucciones para descargar:</strong>
    </div>
    <ol style="margin-left: 20px; line-height: 1.8;">
      <li>Ve a: <a href="${actionsUrl}" target="_blank" style="color: #00d4ff;">GitHub Actions</a></li>
      <li>Click en <strong>"Run workflow"</strong></li>
      <li>Pega la URL M3U8</li>
      <li>Ejecuta y espera (~1-5 min)</li>
    </ol>
    
    <div style="margin-top: 16px; padding: 12px; background: rgba(255,255,255,0.05); border-radius: 8px;">
      <div style="color: rgba(255,255,255,0.6); font-size: 0.85rem; margin-bottom: 8px;">URL M3U8:</div>
      <code style="word-break: break-all; font-size: 0.8rem; color: #00d4ff;">${m3u8Url}</code>
    </div>
    
    <div style="margin-top: 16px;">
      <a href="${actionsUrl}?workflow=Download+HLS+Video" target="_blank" 
         style="display: block; text-align: center; padding: 14px; 
                background: linear-gradient(90deg, #00d4ff, #7c3aed); 
                border-radius: 10px; color: #fff; text-decoration: none; font-weight: 600;">
        🚀 Ir a GitHub Actions
      </a>
    </div>
    
    <div style="margin-top: 16px; font-size: 0.85rem; color: rgba(255,255,255,0.6);">
      <strong>💡 Tip:</strong> También puedes descargar directamente usando el 
      <a href="https://github.com/${GITHUB_REPO}#readme" target="_blank" style="color: #00d4ff;">README</a>
    </div>
  `;
  
  startBtn.disabled = false;
}

// Check if video exists in R2
async function checkR2File(filename) {
  try {
    const response = await fetch(`${R2_PUBLIC_DOMAIN}/${filename}`, { method: 'HEAD' });
    if (response.ok) {
      return true;
    }
  } catch (e) {}
  return false;
}

// Add event listener for Enter key
document.getElementById('m3u8_url').addEventListener('keypress', (e) => {
  if (e.key === 'Enter') startDownload();
});
