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

// Cache static map images to avoid re-fetching
const mapCache = new Map<string, string>();

async function fetchStaticMap(lat: number, lng: number, key: string): Promise<string> {
  const cacheKey = `${lat},${lng}`;
  if (mapCache.has(cacheKey)) return mapCache.get(cacheKey)!;

  const style = 'outdoor-v2';
  const url = `https://api.maptiler.com/maps/${style}/static/${lng},${lat},9/400x400@2x.png?key=${key}&attribution=false`;

  try {
    // Try without Referer first (works when key has no domain restriction)
    // If key is domain-restricted, this will 403 — caught gracefully below
    const res = await fetch(url);
    const buf = Buffer.from(await res.arrayBuffer());
    const dataUri = `data:image/png;base64,${buf.toString('base64')}`;
    mapCache.set(cacheKey, dataUri);
    return dataUri;
  } catch (err) {
    console.warn(`Static map fetch failed for ${lat},${lng}:`, err);
    return '';
  }
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
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

function formatNames(names: string[], max = 4): string {
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

  const maptilerKey = import.meta.env.PUBLIC_MAPTILER_KEY || process.env.PUBLIC_MAPTILER_KEY || '';

  const dateFormatted = formatDate(race.date);
  const namesStr = formatNames(race.first_names || []);
  const typeLabel = getTypeLabel(race.race_type);
  const distLabel = race.distances?.length
    ? race.distances.map(d => `${d}km`).join(' / ')
    : '';

  // Fetch static map if coordinates available
  let mapDataUri = '';
  if (race.lat && race.lng && maptilerKey) {
    mapDataUri = await fetchStaticMap(race.lat, race.lng, maptilerKey);
  }

  // Build the markup
  const markup = {
    type: 'div',
    props: {
      style: {
        width: '1200px',
        height: '630px',
        display: 'flex',
        flexDirection: 'column',
        background: 'linear-gradient(135deg, #ffffff 0%, #f8f7f6 100%)',
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
              height: '6px',
              background: 'linear-gradient(90deg, #F57C20, #6B2D5B)',
            },
          },
        },
        // Main content: left text + right map
        {
          type: 'div',
          props: {
            style: {
              display: 'flex',
              flex: 1,
              padding: '48px 0 40px 56px',
            },
            children: [
              // Left column: text content
              {
                type: 'div',
                props: {
                  style: {
                    display: 'flex',
                    flexDirection: 'column',
                    flex: 1,
                    paddingRight: '32px',
                  },
                  children: [
                    // Header: logo + club name
                    {
                      type: 'div',
                      props: {
                        style: {
                          display: 'flex',
                          alignItems: 'center',
                          gap: '14px',
                          marginBottom: '28px',
                        },
                        children: [
                          {
                            type: 'img',
                            props: {
                              src: logoDataUri,
                              width: 44,
                              height: 44,
                              style: {
                                borderRadius: '10px',
                                objectFit: 'contain',
                              },
                            },
                          },
                          {
                            type: 'div',
                            props: {
                              style: { display: 'flex', flexDirection: 'column' },
                              children: [
                                {
                                  type: 'div',
                                  props: {
                                    style: {
                                      fontFamily: 'Bebas Neue',
                                      fontSize: '20px',
                                      color: '#1a1a1a',
                                      lineHeight: 1,
                                    },
                                    children: 'Ou court le club ?',
                                  },
                                },
                                {
                                  type: 'div',
                                  props: {
                                    style: {
                                      fontSize: '11px',
                                      fontWeight: 600,
                                      color: '#F57C20',
                                      textTransform: 'uppercase' as const,
                                      letterSpacing: '0.1em',
                                    },
                                    children: 'Run Event 86',
                                  },
                                },
                              ],
                            },
                          },
                        ],
                      },
                    },
                    // Race name
                    {
                      type: 'div',
                      props: {
                        style: {
                          fontSize: race.name.length > 40 ? '34px' : '42px',
                          fontWeight: 700,
                          color: '#1a1a1a',
                          lineHeight: 1.15,
                          marginBottom: '14px',
                          display: 'flex',
                          flexWrap: 'wrap' as const,
                          gap: '10px',
                          alignItems: 'center',
                        },
                        children: [
                          race.name,
                          ...(typeLabel ? [{
                            type: 'div',
                            props: {
                              style: {
                                fontSize: '15px',
                                fontWeight: 700,
                                padding: '3px 10px',
                                borderRadius: '20px',
                                background: getTypeBg(race.race_type),
                                color: getTypeColor(race.race_type),
                                textTransform: 'uppercase' as const,
                              },
                              children: typeLabel,
                            },
                          }] : []),
                        ],
                      },
                    },
                    // Date + location
                    {
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '20px',
                          color: '#777',
                          marginBottom: '6px',
                        },
                        children: [dateFormatted, race.location].filter(Boolean).join(' — '),
                      },
                    },
                    // Distances
                    ...(distLabel ? [{
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '17px',
                          color: '#aaa',
                          marginBottom: '6px',
                        },
                        children: distLabel,
                      },
                    }] : []),
                    // Spacer
                    { type: 'div', props: { style: { flex: 1 } } },
                    // Bottom: members
                    {
                      type: 'div',
                      props: {
                        style: {
                          display: 'flex',
                          alignItems: 'center',
                          gap: '14px',
                          flexWrap: 'wrap' as const,
                        },
                        children: [
                          {
                            type: 'div',
                            props: {
                              style: {
                                display: 'flex',
                                alignItems: 'center',
                                gap: '6px',
                                background: 'rgba(245, 124, 32, 0.1)',
                                color: '#F57C20',
                                padding: '7px 16px',
                                borderRadius: '24px',
                                fontSize: '20px',
                                fontWeight: 700,
                              },
                              children: `${race.member_count} membre${race.member_count > 1 ? 's' : ''} inscrit${race.member_count > 1 ? 's' : ''}`,
                            },
                          },
                          ...(namesStr ? [{
                            type: 'div',
                            props: {
                              style: {
                                fontSize: '18px',
                                color: '#999',
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
              // Right column: static map or location placeholder
              {
                type: 'div',
                props: {
                  style: {
                    width: '380px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    position: 'relative' as const,
                  },
                  children: mapDataUri ? [
                    {
                      type: 'img',
                      props: {
                        src: mapDataUri,
                        width: 380,
                        height: 580,
                        style: {
                          borderRadius: '16px 0 0 16px',
                          objectFit: 'cover',
                          opacity: 0.85,
                        },
                      },
                    },
                    // Orange pin dot overlay (center of map)
                    {
                      type: 'div',
                      props: {
                        style: {
                          position: 'absolute',
                          top: '50%',
                          left: '50%',
                          transform: 'translate(-50%, -50%)',
                          width: '22px',
                          height: '22px',
                          borderRadius: '50%',
                          background: '#F57C20',
                          border: '3px solid #fff',
                          boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
                        },
                      },
                    },
                  ] : [
                    // Placeholder: gradient background with location pin
                    {
                      type: 'div',
                      props: {
                        style: {
                          width: '380px',
                          height: '580px',
                          borderRadius: '16px 0 0 16px',
                          background: 'linear-gradient(160deg, #f0ebe6 0%, #e8e0d8 100%)',
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: '12px',
                        },
                        children: [
                          // Pin icon circle
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
                                    boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
                                  },
                                },
                              },
                            },
                          },
                          // Location text
                          ...(race.location ? [{
                            type: 'div',
                            props: {
                              style: {
                                fontSize: '16px',
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
                  ],
                },
              },
            ],
          },
        },
        // Bottom decorative bar
        {
          type: 'div',
          props: {
            style: {
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: '4px',
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
