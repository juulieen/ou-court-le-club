import type { APIRoute, GetStaticPaths } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { generateOgImage } from '../../lib/og-image';

export const getStaticPaths: GetStaticPaths = async () => {
  const candidates = [
    path.resolve(process.cwd(), 'src/data/races.json'),
    path.resolve(process.cwd(), '../docs/data/races.json'),
    path.resolve(process.cwd(), '../data/races.json'),
  ];

  let data: { races: any[] } | null = null;
  for (const candidate of candidates) {
    try {
      const raw = fs.readFileSync(candidate, 'utf-8');
      data = JSON.parse(raw);
      break;
    } catch {
      // Try next
    }
  }

  if (!data) {
    throw new Error('Unable to load races.json for OG image generation');
  }

  return (data.races || [])
    .filter((r: any) => r.member_count > 0)
    .map((race: any) => ({
      params: { id: race.id },
      props: { race },
    }));
};

export const GET: APIRoute = async ({ props }) => {
  const race = props.race;
  const png = await generateOgImage(race);

  return new Response(png, {
    headers: {
      'Content-Type': 'image/png',
      'Cache-Control': 'public, max-age=86400',
    },
  });
};
