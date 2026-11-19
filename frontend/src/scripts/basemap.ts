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
const ARCHIVES = ["https://map.juulieen.fr/france.pmtiles", "https://map.juulieen.fr/relief.pmtiles"];
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
  maplibregl.addProtocol("pmtiles", protocol.tile);
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
