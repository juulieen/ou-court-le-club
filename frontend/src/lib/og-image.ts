import satori from 'satori';
import { Resvg } from '@resvg/resvg-js';

// Fetch font data once at module level
let dmSansRegular: ArrayBuffer | null = null;
let dmSansBold: ArrayBuffer | null = null;
let bebasNeue: ArrayBuffer | null = null;

async function loadFonts() {
  if (dmSansRegular) return;

  const [regular, bold, bebas] = await Promise.all([
    fetch('https://fonts.gstatic.com/s/dmsans/v17/rP2tp2ywxg089UriI5-g4vlH9VoD8CmcqZG40F9JadbnoEwAopxhTg.ttf').then(r => r.arrayBuffer()),
    fetch('https://fonts.gstatic.com/s/dmsans/v17/rP2tp2ywxg089UriI5-g4vlH9VoD8CmcqZG40F9JadbnoEwARZthTg.ttf').then(r => r.arrayBuffer()),
    fetch('https://fonts.gstatic.com/s/bebasneue/v16/JTUSjIg69CK48gW7PXooxW4.ttf').then(r => r.arrayBuffer()),
  ]);

  dmSansRegular = regular;
  dmSansBold = bold;
  bebasNeue = bebas;
}

// Load club logo as base64
import fs from 'node:fs';
import path from 'node:path';

let logoDataUri = '';
function loadLogo() {
  if (logoDataUri) return;
  const logoPath = path.resolve(process.cwd(), 'public/img/logo.jpg');
  const buf = fs.readFileSync(logoPath);
  logoDataUri = `data:image/jpeg;base64,${buf.toString('base64')}`;
}

// --- Static map from OSM tiles (free, no API key) ---
import sharp from 'sharp';

const mapCache = new Map<string, string>();

function latLngToTile(lat: number, lng: number, zoom: number) {
  const n = Math.pow(2, zoom);
  const x = Math.floor((lng + 180) / 360 * n);
  const latRad = lat * Math.PI / 180;
  const y = Math.floor((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n);
  return { x, y };
}

async function fetchTile(z: number, x: number, y: number): Promise<Buffer | null> {
  try {
    const res = await fetch(`https://tile.openstreetmap.org/${z}/${x}/${y}.png`, {
      headers: { 'User-Agent': 'RunEvent86-OG/1.0 (https://github.com/juulieen/ou-court-le-club)' },
    });
    if (!res.ok) return null;
    return Buffer.from(await res.arrayBuffer());
  } catch { return null; }
}

async function fetchStaticMap(lat: number, lng: number): Promise<string> {
  const cacheKey = `${lat.toFixed(2)},${lng.toFixed(2)}`;
  if (mapCache.has(cacheKey)) return mapCache.get(cacheKey)!;

  const zoom = 9;
  const center = latLngToTile(lat, lng, zoom);

  // Fetch 3x3 grid of tiles
  const grid: { dx: number; dy: number; buf: Buffer }[] = [];
  const offsets = [-1, 0, 1];
  await Promise.all(
    offsets.flatMap(dy => offsets.map(async dx => {
      const buf = await fetchTile(zoom, center.x + dx, center.y + dy);
      if (buf) grid.push({ dx: dx + 1, dy: dy + 1, buf });
    }))
  );

  if (grid.length < 4) return '';

  // Compose 3x3 tiles into 768x768 image, then crop to 340x630
  const composite = await sharp({
    create: { width: 768, height: 768, channels: 4, background: { r: 240, g: 235, b: 230, alpha: 1 } },
  })
    .composite(
      grid.map(t => ({
        input: t.buf,
        left: t.dx * 256,
        top: t.dy * 256,
      }))
    )
    .extract({ left: 214, top: 69, width: 340, height: 630 })
    .png()
    .toBuffer();

  const dataUri = `data:image/png;base64,${composite.toString('base64')}`;
  mapCache.set(cacheKey, dataUri);
  return dataUri;
}

interface RaceData {
  name: string;
  date: string;
  location: string;
  member_count: number;
  first_names?: string[];
  race_type?: string;
  distances?: number[];
  lat?: number;
  lng?: number;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

function formatNames(names: string[], max = 3): string {
  if (!names || !names.length) return '';
  if (names.length <= max) return names.join(', ');
  return names.slice(0, max).join(', ') + ` +${names.length - max}`;
}

function getTypeLabel(type?: string): string {
  if (!type || type === 'autre') return '';
  return type.charAt(0).toUpperCase() + type.slice(1);
}

function getTypeColor(type?: string): string {
  switch (type) {
    case 'trail': return '#2e7d32';
    case 'route': return '#1565c0';
    default: return '#777';
  }
}

function getTypeBg(type?: string): string {
  switch (type) {
    case 'trail': return '#e8f5e9';
    case 'route': return '#e3f2fd';
    default: return '#f5f5f5';
  }
}

export async function generateOgImage(race: RaceData): Promise<Buffer> {
  await loadFonts();
  loadLogo();

  const dateFormatted = formatDate(race.date);
  const namesStr = formatNames(race.first_names || []);
  const typeLabel = getTypeLabel(race.race_type);
  const distLabel = race.distances?.length
    ? race.distances.map(d => `${d}km`).join(' / ')
    : '';
  const metaLine = [dateFormatted, race.location].filter(Boolean).join(' — ');

  // Fetch static map
  let mapDataUri = '';
  if (race.lat && race.lng) {
    mapDataUri = await fetchStaticMap(race.lat, race.lng);
  }

  // Compute font size based on name length — must be readable at WhatsApp preview size (~300px wide)
  const nameFontSize = race.name.length > 50 ? 42 : race.name.length > 35 ? 48 : 56;

  const markup = {
    type: 'div',
    props: {
      style: {
        width: '1200px',
        height: '630px',
        display: 'flex',
        background: '#ffffff',
        fontFamily: 'DM Sans',
        position: 'relative',
        overflow: 'hidden',
      },
      children: [
        // Top accent bar
        {
          type: 'div',
          props: {
            style: {
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: '8px',
              background: 'linear-gradient(90deg, #F57C20, #6B2D5B)',
            },
          },
        },
        // Left: text content
        {
          type: 'div',
          props: {
            style: {
              display: 'flex',
              flexDirection: 'column',
              flex: 1,
              padding: '40px 40px 36px 48px',
            },
            children: [
              // Club header — compact
              {
                type: 'div',
                props: {
                  style: {
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    marginBottom: '24px',
                  },
                  children: [
                    {
                      type: 'img',
                      props: {
                        src: logoDataUri,
                        width: 38,
                        height: 38,
                        style: { borderRadius: '8px', objectFit: 'contain' },
                      },
                    },
                    {
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '15px',
                          fontWeight: 600,
                          color: '#F57C20',
                          textTransform: 'uppercase' as const,
                          letterSpacing: '0.08em',
                        },
                        children: 'Run Event 86',
                      },
                    },
                  ],
                },
              },
              // Race name — BIG
              {
                type: 'div',
                props: {
                  style: {
                    fontSize: `${nameFontSize}px`,
                    fontWeight: 700,
                    color: '#1a1a1a',
                    lineHeight: 1.1,
                    marginBottom: '20px',
                  },
                  children: race.name,
                },
              },
              // Type + distance badges row
              ...(typeLabel || distLabel ? [{
                type: 'div',
                props: {
                  style: {
                    display: 'flex',
                    gap: '10px',
                    alignItems: 'center',
                    marginBottom: '12px',
                    flexWrap: 'wrap' as const,
                  },
                  children: [
                    ...(typeLabel ? [{
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '18px',
                          fontWeight: 700,
                          padding: '4px 14px',
                          borderRadius: '20px',
                          background: getTypeBg(race.race_type),
                          color: getTypeColor(race.race_type),
                          textTransform: 'uppercase' as const,
                        },
                        children: typeLabel,
                      },
                    }] : []),
                    ...(distLabel ? [{
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '20px',
                          color: '#999',
                          fontWeight: 500,
                        },
                        children: distLabel,
                      },
                    }] : []),
                  ],
                },
              }] : []),
              // Date + location
              {
                type: 'div',
                props: {
                  style: {
                    fontSize: '24px',
                    color: '#555',
                    marginBottom: '8px',
                  },
                  children: metaLine,
                },
              },
              // Spacer
              { type: 'div', props: { style: { flex: 1 } } },
              // Members — BIG badge
              {
                type: 'div',
                props: {
                  style: {
                    display: 'flex',
                    alignItems: 'center',
                    gap: '16px',
                    flexWrap: 'wrap' as const,
                  },
                  children: [
                    {
                      type: 'div',
                      props: {
                        style: {
                          display: 'flex',
                          alignItems: 'center',
                          background: 'rgba(245, 124, 32, 0.12)',
                          color: '#F57C20',
                          padding: '10px 22px',
                          borderRadius: '28px',
                          fontSize: '26px',
                          fontWeight: 700,
                        },
                        children: `${race.member_count} membre${race.member_count > 1 ? 's' : ''}`,
                      },
                    },
                    ...(namesStr ? [{
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '22px',
                          color: '#888',
                          fontStyle: 'italic',
                        },
                        children: namesStr,
                      },
                    }] : []),
                  ],
                },
              },
            ],
          },
        },
        // Right: map
        ...(mapDataUri ? [{
          type: 'div',
          props: {
            style: {
              width: '340px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              position: 'relative' as const,
            },
            children: [
              {
                type: 'img',
                props: {
                  src: mapDataUri,
                  width: 340,
                  height: 630,
                  style: { objectFit: 'cover', opacity: 0.9 },
                },
              },
              // Gradient overlay to blend with left
              {
                type: 'div',
                props: {
                  style: {
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    bottom: 0,
                    width: '60px',
                    background: 'linear-gradient(90deg, #ffffff, rgba(255,255,255,0))',
                  },
                },
              },
              // Pin
              {
                type: 'div',
                props: {
                  style: {
                    position: 'absolute',
                    top: '50%',
                    left: '50%',
                    transform: 'translate(-50%, -50%)',
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: '#F57C20',
                    border: '4px solid #fff',
                    boxShadow: '0 2px 10px rgba(0,0,0,0.3)',
                  },
                },
              },
            ],
          },
        }] : [
          // Placeholder when no map
          {
            type: 'div',
            props: {
              style: {
                width: '340px',
                background: 'linear-gradient(160deg, #f0ebe6 0%, #e8e0d8 100%)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
              },
              children: [
                {
                  type: 'div',
                  props: {
                    style: {
                      width: '64px',
                      height: '64px',
                      borderRadius: '50%',
                      background: 'rgba(245, 124, 32, 0.15)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    },
                    children: {
                      type: 'div',
                      props: {
                        style: {
                          width: '24px',
                          height: '24px',
                          borderRadius: '50%',
                          background: '#F57C20',
                          border: '3px solid #fff',
                        },
                      },
                    },
                  },
                },
                ...(race.location ? [{
                  type: 'div',
                  props: {
                    style: {
                      fontSize: '18px',
                      color: '#999',
                      textAlign: 'center' as const,
                      padding: '0 24px',
                    },
                    children: race.location,
                  },
                }] : []),
              ],
            },
          },
        ]),
        // Bottom accent bar
        {
          type: 'div',
          props: {
            style: {
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: '6px',
              background: 'linear-gradient(90deg, #6B2D5B, #F57C20)',
            },
          },
        },
      ],
    },
  };

  const svg = await satori(markup as any, {
    width: 1200,
    height: 630,
    fonts: [
      { name: 'DM Sans', data: dmSansRegular!, weight: 400, style: 'normal' },
      { name: 'DM Sans', data: dmSansBold!, weight: 700, style: 'normal' },
      { name: 'Bebas Neue', data: bebasNeue!, weight: 400, style: 'normal' },
    ],
  });

  const resvg = new Resvg(svg, {
    fitTo: { mode: 'width', value: 1200 },
  });

  return Buffer.from(resvg.render().asPng());
}
