# JW8 Video Extractor API

API para extraer calidades de videos de sitios con reproductor JW8 Player.

## Instalación

```bash
cd /home/fullmetal/MCP_Customs/jw8-extractor-api
bun install
```

## Uso

### Iniciar servidor

```bash
bun start
# o para desarrollo con hot reload:
bun dev
```

El servidor corre en `http://localhost:3456`

### Interfaz Web

Abre `http://localhost:3456/` en tu navegador para usar la interfaz gráfica.

### API REST

#### Extraer fuentes de video

```bash
POST /api/extract
Content-Type: application/json

{
  "url": "https://callistanise.com/v/715evv6rb8lg",
  "waitTime": 8000
}
```

**Respuesta:**

```json
{
  "success": true,
  "videoId": "715evv6rb8lg",
  "title": "Video Title",
  "duration": "2925.44",
  "thumbnail": "https://...",
  "subtitles": [
    {
      "language": "Español",
      "url": "https://...vtt"
    }
  ],
  "sources": [
    {
      "quality": "480p",
      "resolution": "852x480",
      "bandwidth": 464161,
      "url": "https://...index-f1-v1-a1.m3u8",
      "type": "hls"
    },
    {
      "quality": "720p",
      "resolution": "1280x720",
      "bandwidth": 858202,
      "url": "https://...index-f2-v1-a1.m3u8",
      "type": "hls"
    },
    {
      "quality": "1080p",
      "resolution": "1920x1080",
      "bandwidth": 1697680,
      "url": "https://...index-f3-v1-a1.m3u8",
      "type": "hls"
    }
  ],
  "masterUrl": "https://...master.m3u8"
}
```

#### Health check

```bash
GET /health
```

## Cómo funciona

1. **Playwright** carga la página con el JW8 player
2. Se ejecuta `jwplayer().getPlaylist()` para obtener la configuración
3. Se intercepta el tráfico de red para capturar el `master.m3u8`
4. Se parsea el M3U8 para extraer las calidades disponibles
5. Se devuelve un JSON con todas las opciones

## Descargar videos

Usa FFmpeg con la URL obtenida:

```bash
ffmpeg -i "URL_DEL_M3U8" -c copy video.mp4
```

## Dependencias

- Fastify - Framework web
- Playwright - Navegador headless
- Zod - Validación de schemas
- @fastify/static - Archivos estáticos
- @fastify/cors - CORS
