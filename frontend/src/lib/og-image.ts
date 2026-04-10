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

  const dateFormatted = formatDate(race.date);
  const namesStr = formatNames(race.first_names || []);
  const typeLabel = getTypeLabel(race.race_type);
  const distLabel = race.distances?.length
    ? race.distances.map(d => `${d}km`).join(' / ')
    : '';

  // Build the JSX-like structure for satori
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
        // Content area
        {
          type: 'div',
          props: {
            style: {
              display: 'flex',
              flexDirection: 'column',
              padding: '48px 56px 40px',
              flex: 1,
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
                    marginBottom: '32px',
                  },
                  children: [
                    {
                      type: 'div',
                      props: {
                        style: {
                          width: '44px',
                          height: '44px',
                          borderRadius: '10px',
                          background: '#F57C20',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          color: '#fff',
                          fontSize: '22px',
                          fontFamily: 'Bebas Neue',
                        },
                        children: 'RE',
                      },
                    },
                    {
                      type: 'div',
                      props: {
                        style: {
                          display: 'flex',
                          flexDirection: 'column',
                        },
                        children: [
                          {
                            type: 'div',
                            props: {
                              style: {
                                fontFamily: 'Bebas Neue',
                                fontSize: '22px',
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
                    fontSize: race.name.length > 40 ? '38px' : '46px',
                    fontWeight: 700,
                    color: '#1a1a1a',
                    lineHeight: 1.15,
                    marginBottom: '16px',
                    display: 'flex',
                    flexWrap: 'wrap' as const,
                    gap: '12px',
                    alignItems: 'center',
                  },
                  children: [
                    race.name,
                    ...(typeLabel ? [{
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '16px',
                          fontWeight: 700,
                          padding: '4px 12px',
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
                    fontSize: '22px',
                    color: '#777',
                    marginBottom: '8px',
                    display: 'flex',
                    gap: '8px',
                  },
                  children: [dateFormatted, race.location].filter(Boolean).join(' — '),
                },
              },
              // Distances
              ...(distLabel ? [{
                type: 'div',
                props: {
                  style: {
                    fontSize: '18px',
                    color: '#aaa',
                    marginBottom: '8px',
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
                    gap: '16px',
                  },
                  children: [
                    // Member count badge
                    {
                      type: 'div',
                      props: {
                        style: {
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          background: 'rgba(245, 124, 32, 0.1)',
                          color: '#F57C20',
                          padding: '8px 18px',
                          borderRadius: '24px',
                          fontSize: '22px',
                          fontWeight: 700,
                        },
                        children: `${race.member_count} membre${race.member_count > 1 ? 's' : ''} inscrit${race.member_count > 1 ? 's' : ''}`,
                      },
                    },
                    // First names
                    ...(namesStr ? [{
                      type: 'div',
                      props: {
                        style: {
                          fontSize: '20px',
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
