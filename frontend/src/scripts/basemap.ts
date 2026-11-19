// Fond de carte : style « juulieen » et tuiles Protomaps servis par
// map.juulieen.fr (dépôt public juulieen/map, fichier sur l'ASUS). Il remplace
// MapTiler depuis novembre 2026 : pas de clé, pas de quota.
//
// Les tuiles sont lues par plages d'octets dans un seul fichier PMTiles, que
// le cache HTTP du navigateur ne ressert pas de façon fiable. Le préchargement
// de la vue d'arrivée (mapfx.prefetchLanding) garde donc ses octets en mémoire
// et les rend à la carte quand elle demande la même plage.
import { FetchSource, PMTiles, Protocol, type RangeResponse } from "pmtiles";

declare const maplibregl: any;

export const BASEMAP_STYLE = "https://map.juulieen.fr/styles/juulieen.json";
const ARCHIVE = "https://map.juulieen.fr/france.pmtiles";
// Les polices de map.juulieen.fr sont Noto Sans Regular / Medium / Italic.
export const LABEL_FONT = "Noto Sans Medium";

const KEEP_MS = 120_000; // octets préchargés jamais redemandés : oubliés

const network = new FetchSource(ARCHIVE);
const kept = new Map<string, Promise<RangeResponse>>();
const rangeKey = (offset: number, length: number) => `${offset}:${length}`;

// Source lue par la carte : ce qui a été préchargé d'abord, le réseau sinon.
const mapSource = {
  getKey: () => ARCHIVE,
  getBytes(offset: number, length: number, signal?: AbortSignal, etag?: string) {
    const key = rangeKey(offset, length);
    const hit = kept.get(key);
    if (!hit) return network.getBytes(offset, length, signal, etag);
    kept.delete(key);
    return hit;
  },
};

export function setupBasemap() {
  const protocol = new Protocol();
  protocol.add(new PMTiles(mapSource));
  maplibregl.addProtocol("pmtiles", protocol.tile);
}

// Suivi des octets d'un téléchargement : appelé avec la taille attendue, puis
// à chaque morceau reçu, puis à la fin.
export interface BytesTracker {
  part(expected: number | null): { read(n: number): void; done(): void };
}

// Lecteur de plages pour le préchargement : chaque lecture (en-tête,
// répertoires, tuile) est gardée pour la source de la carte.
function prefetchSource(tracker: BytesTracker) {
  return {
    getKey: () => ARCHIVE,
    getBytes(offset: number, length: number): Promise<RangeResponse> {
      const key = rangeKey(offset, length);
      const pending = kept.get(key);
      if (pending) return pending;
      const promise = readRange(offset, length, tracker);
      kept.set(key, promise);
      promise.catch(() => kept.delete(key));
      setTimeout(() => kept.delete(key), KEEP_MS);
      return promise;
    },
  };
}

async function readRange(offset: number, length: number, tracker: BytesTracker): Promise<RangeResponse> {
  const part = tracker.part(length);
  try {
    const res = await fetch(ARCHIVE, { headers: { Range: `bytes=${offset}-${offset + length - 1}` } });
    // 200 = serveur qui ignore la plage : on ne télécharge pas 18 Go.
    if (res.status !== 206 || !res.body) throw new Error(`plage refusée (${res.status})`);
    const data = new Uint8Array(length);
    const reader = res.body.getReader();
    let at = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      data.set(value.subarray(0, length - at), at);
      at += value.byteLength;
      part.read(value.byteLength);
    }
    return { data: data.buffer, etag: res.headers.get("etag") || undefined };
  } finally {
    part.done();
  }
}

// Précharge une tuile : même archive, lue par une instance PMTiles à part dont
// les octets restent disponibles pour la carte. Une instance par tuile, pour
// que `tracker` ne compte que ses octets ; les plages communes (en-tête,
// répertoires) ne partent qu'une fois, les suivantes attendent la première.
export async function prefetchTile(z: number, x: number, y: number, tracker: BytesTracker) {
  await new PMTiles(prefetchSource(tracker)).getZxy(z, x, y);
}
