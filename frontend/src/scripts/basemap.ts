// Fond de carte : style « juulieen » et fichiers PMTiles servis par
// map.juulieen.fr (dépôt public juulieen/map, fichiers sur l'ASUS) : le fond
// Protomaps (france.pmtiles) et le relief Mapterhorn (relief.pmtiles). Il
// remplace MapTiler depuis novembre 2026 : pas de clé, pas de quota.
//
// Les tuiles sont lues par plages d'octets dans ces fichiers, que le cache
// HTTP du navigateur ne ressert pas de façon fiable. Le préchargement de la
// vue d'arrivée (mapfx.prefetchLanding) garde donc ses octets en mémoire et
// les rend à la carte quand elle demande la même plage du même fichier.
import { FetchSource, PMTiles, Protocol, type RangeResponse } from "pmtiles";

declare const maplibregl: any;

export const BASEMAP_STYLE = "https://map.juulieen.fr/styles/juulieen.json";
const RELIEF = "https://map.juulieen.fr/relief.pmtiles";
const ARCHIVES = ["https://map.juulieen.fr/france.pmtiles", RELIEF];
// Les polices de map.juulieen.fr sont Noto Sans Regular / Medium / Italic.
export const LABEL_FONT = "Noto Sans Medium";

const KEEP_MS = 120_000; // octets préchargés jamais redemandés : oubliés

const kept = new Map<string, Promise<RangeResponse>>();
const rangeKey = (archive: string, offset: number, length: number) => `${archive}#${offset}:${length}`;

// Source lue par la carte : ce qui a été préchargé d'abord, le réseau sinon.
function mapSource(archive: string) {
  const network = new FetchSource(archive);
  return {
    getKey: () => archive,
    getBytes(offset: number, length: number, signal?: AbortSignal, etag?: string) {
      const key = rangeKey(archive, offset, length);
      const hit = kept.get(key);
      if (!hit) return network.getBytes(offset, length, signal, etag);
      kept.delete(key);
      // Lecture préchargée en échec (coupure, réponse autre que 206) : la carte
      // refait sa propre requête, sinon la tuile resterait vide à l'arrivée.
      return hit.catch(() => network.getBytes(offset, length, signal, etag));
    },
  };
}

export function setupBasemap() {
  const protocol = new Protocol();
  for (const archive of ARCHIVES) protocol.add(new PMTiles(mapSource(archive)));
  const relief = protocol.get(RELIEF)!;
  maplibregl.addProtocol("pmtiles", async (params: { url: string }, abort: AbortController) => {
    const res = await protocol.tilev4(params, abort);
    const tile = res.data === null && TILE_URL.exec(params.url);
    if (!tile || tile[1] !== RELIEF) return res;
    return { data: await overzoomRelief(relief, +tile[2], +tile[3], +tile[4], abort.signal) };
  });
}

// Relief absent : relief.pmtiles va jusqu'au zoom 15 en France seulement
// (zoom 12 ailleurs en Europe de l'Ouest, 6 sur le reste de la planète).
// Pour une tuile raster absente, pmtiles répond `data: null` et MapLibre la
// laisse vide pour de bon, sans se rabattre sur la tuile parente. On agrandit
// donc la plus proche tuile ancêtre présente, en interpolant les altitudes
// (interpoler les couleurs Terrarium fausserait les sauts de canal vert).
// Même repli dans la page d'exploration de juulieen/map (site/overzoom.js).
const TILE_URL = /^pmtiles:\/\/(.+)\/(\d+)\/(\d+)\/(\d+)$/;

async function overzoomRelief(archive: PMTiles, z: number, x: number, y: number, signal: AbortSignal) {
  for (let dz = 1; dz <= z; dz++) {
    const parent = await archive.getZxy(z - dz, x >> dz, y >> dz, signal);
    if (parent) return upscaleTerrarium(parent.data, dz, x & ((1 << dz) - 1), y & ((1 << dz) - 1));
  }
  return null;
}

// Agrandit 2^dz fois le carré (ox, oy) d'une tuile Terrarium, en PNG.
// Interpolation bicubique (Catmull-Rom) : en bilinéaire, les pentes changent
// d'un coup à chaque pixel source et l'ombrage fait des facettes carrées.
async function upscaleTerrarium(data: ArrayBuffer, dz: number, ox: number, oy: number) {
  const image = await createImageBitmap(new Blob([data]), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
  const size = image.width;
  const canvas = new OffscreenCanvas(size, size);
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(image, 0, 0);
  image.close();
  const src = ctx.getImageData(0, 0, size, size).data;
  const height = new Float32Array(size * size); // altitude + 32768
  for (let p = 0; p < height.length; p++) height[p] = src[p * 4] * 256 + src[p * 4 + 1] + src[p * 4 + 2] / 256;

  // Pour chaque ligne / colonne de sortie : 4 indices source et leurs poids.
  const span = size / (1 << dz); // côté du carré source, en pixels
  const taps = (offset: number) => {
    const index = new Int32Array(size * 4);
    const weight = new Float32Array(size * 4);
    for (let o = 0; o < size; o++) {
      const f = offset * span + ((o + 0.5) * span) / size - 0.5;
      const i = Math.floor(f);
      const t = f - i;
      const w = [(-t * t * t + 2 * t * t - t) / 2, (3 * t * t * t - 5 * t * t + 2) / 2, (-3 * t * t * t + 4 * t * t + t) / 2, (t * t * t - t * t) / 2];
      for (let n = 0; n < 4; n++) {
        index[o * 4 + n] = Math.min(size - 1, Math.max(0, i - 1 + n));
        weight[o * 4 + n] = w[n];
      }
    }
    return { index, weight };
  };
  const cols = taps(ox);
  const rows = taps(oy);

  const out = ctx.createImageData(size, size);
  for (let v = 0; v < size; v++) {
    for (let u = 0; u < size; u++) {
      let h = 0;
      for (let m = 0; m < 4; m++) {
        const row = rows.index[v * 4 + m] * size;
        let line = 0;
        for (let n = 0; n < 4; n++) line += cols.weight[u * 4 + n] * height[row + cols.index[u * 4 + n]];
        h += rows.weight[v * 4 + m] * line;
      }
      h = Math.max(0, h);
      const k = (v * size + u) * 4;
      out.data[k] = h >> 8;
      out.data[k + 1] = h & 255;
      out.data[k + 2] = (h % 1) * 256;
      out.data[k + 3] = 255;
    }
  }
  ctx.putImageData(out, 0, 0);
  return (await canvas.convertToBlob({ type: "image/png" })).arrayBuffer();
}

// Suivi des octets d'un téléchargement : appelé avec la taille attendue, puis
// à chaque morceau reçu, puis à la fin.
export interface BytesTracker {
  part(expected: number | null): { read(n: number): void; done(): void };
}

// Lecteur de plages pour le préchargement : chaque lecture (en-tête,
// répertoires, tuile) est gardée pour la source de la carte.
function prefetchSource(archive: string, tracker: BytesTracker) {
  return {
    getKey: () => archive,
    getBytes(offset: number, length: number): Promise<RangeResponse> {
      const key = rangeKey(archive, offset, length);
      const pending = kept.get(key);
      if (pending) return pending;
      const promise = readRange(archive, offset, length, tracker);
      kept.set(key, promise);
      promise.catch(() => kept.delete(key));
      setTimeout(() => kept.delete(key), KEEP_MS);
      return promise;
    },
  };
}

async function readRange(archive: string, offset: number, length: number, tracker: BytesTracker): Promise<RangeResponse> {
  const part = tracker.part(length);
  try {
    // no-store : les octets sont gardés en mémoire ; et Chromium sous Windows
    // ressert mal les plages en cache (FetchSource de pmtiles l'évite aussi).
    const res = await fetch(archive, {
      headers: { Range: `bytes=${offset}-${offset + length - 1}` },
      cache: "no-store",
    });
    // 200 = serveur qui ignore la plage : on ne télécharge pas tout le fichier.
    if (res.status !== 206 || !res.body) throw new Error(`plage refusée (${res.status})`);
    const data = new Uint8Array(length);
    const reader = res.body.getReader();
    let at = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (at + value.byteLength > length) throw new Error("plage plus longue que demandée");
      data.set(value, at);
      at += value.byteLength;
      part.read(value.byteLength);
    }
    return { data: data.buffer, etag: res.headers.get("etag") || undefined };
  } finally {
    part.done();
  }
}

// Précharge la tuile z/x/y du fichier `archive` (URL https, sans pmtiles://) :
// instance PMTiles à part dont les octets restent disponibles pour la carte.
// Une instance par tuile, pour que `tracker` ne compte que ses octets ; les
// plages communes (en-tête, répertoires) ne partent qu'une fois, les suivantes
// attendent la première.
export async function prefetchTile(archive: string, z: number, x: number, y: number, tracker: BytesTracker) {
  await new PMTiles(prefetchSource(archive, tracker)).getZxy(z, x, y);
}
