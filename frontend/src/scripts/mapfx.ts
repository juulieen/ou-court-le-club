// Effets visuels de la carte : ouverture sur le globe, arcs lumineux qui
// partent du club vers chaque course, apparition des points en cascade.
// Tout passe par MapLibre (pas de DOM par point) pour rester fluide sur mobile.
// « Réduire les animations » coupe tout : la carte arrive sur sa vue finale.

declare const maplibregl: any;

// Local du club (Poitiers), point de départ des arcs.
export const CLUB_HOME: [number, number] = [0.3404, 46.5802];

const ORANGE = "245,124,32";
const REVEAL_MS = 2400; // durée de la cascade (arcs + points)
const ARC_GROW = 0.35; // part de la cascade que met un arc à atteindre sa course
const PULSE_PERIOD_MS = 3500; // un éclat toutes les 3,5 s

export const reducedMotion = (): boolean =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

// Intro à chaque chargement, sauf sur un lien direct vers une course (#race/…)
// ou avec « réduire les animations ». « ?intro » la force quand même. La règle
// est dans le script en ligne d'index.astro, qui doit afficher le logo avant
// le premier rendu : il pose la classe `intro` sur <html>.
export function shouldPlayIntro(): boolean {
  return document.documentElement.classList.contains("intro");
}

// --- Écran du logo ---

const SPLASH_MIN_MS = 1200; // le logo a le temps d'apparaître, même en cache
// Style ou tuiles qui ne répondent pas. En 4G lente, la vue d'arrivée sur la
// France (3 à 4 Mo de tuiles) met 15 à 20 s : on l'attend, barre à l'appui.
const SPLASH_MAX_MS = 25000; // filet CSS de global.css (splash-giveup) après
const SPLASH_FADE_MS = 700; // doit suivre la transition de #splash.leaving

let splashLeft: (() => void) | null = null;

// Retire le logo quand deux choses sont prêtes : le globe dessiné (style
// chargé, au moins une tuile reçue, plus aucune en attente) et `landing`, les
// tuiles de la vue d'arrivée (prefetchLanding). Résout quand il commence à
// s'effacer, ou plus tôt si hideSplash() est appelé (intro passée).
export function hideSplashWhenReady(map: any, landing: Promise<void>): Promise<void> {
  return new Promise((resolve) => {
    let tiles = 0;
    let globeReady = false;
    let landed = false;
    let ready = false;
    const stopWatching = () => {
      map.off("data", onData);
      map.off("render", onRender);
    };
    splashLeft = () => {
      splashLeft = null;
      stopWatching();
      clearTimeout(maxTimer);
      resolve();
    };
    const finish = () => {
      if (ready) return;
      ready = true;
      setSplashProgress(1);
      // Au moins SPLASH_MIN_MS depuis l'ouverture de la page, et le temps de
      // voir la barre pleine.
      setTimeout(hideSplash, Math.max(400, SPLASH_MIN_MS - performance.now()));
    };
    const check = () => {
      if (globeReady && landed) finish();
    };
    const onData = (e: any) => {
      if (e.dataType === "source" && e.tile) tiles++;
    };
    const onRender = () => {
      if (globeReady || !(tiles > 0 && map.isStyleLoaded() && map.areTilesLoaded())) return;
      // La tuile est reçue ; on laisse passer l'image qui la dessine.
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          globeReady = true;
          stopWatching();
          check();
        }),
      );
    };
    map.on("data", onData);
    map.on("render", onRender);
    landing.then(() => {
      landed = true;
      check();
    });
    const maxTimer = setTimeout(finish, SPLASH_MAX_MS);
  });
}

export function hideSplash() {
  splashLeft?.();
  const el = document.getElementById("splash");
  if (!el || el.classList.contains("leaving")) return;
  el.classList.add("leaving");
  setTimeout(() => el.remove(), SPLASH_FADE_MS);
}

// Barre sous le logo, de 0 à 1 (elle part de 10 % en CSS : la page charge).
function setSplashProgress(p: number) {
  document.getElementById("splash")?.style.setProperty("--progress", String(p));
}

// --- Préchargement de la vue d'arrivée ---
// Le vol de l'intro finit sur les courses. Sans préchargement, ses tuiles ne
// partent qu'à l'atterrissage et la carte se remplit à vue ; on les demande
// pendant le logo. Le navigateur les garde en cache (MapTiler : max-age 4 h,
// 8 h pour le relief) et MapLibre les y retrouve.

const MAX_PREFETCH = 80; // grand écran : on s'arrête là

// Mercator normalisé (0 → 1), comme le monde de MapLibre à 512 px au zoom 0.
const mercX = (lng: number) => (lng + 180) / 360;
const mercY = (lat: number) => {
  const s = Math.sin((lat * Math.PI) / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};

type Padding = { top: number; bottom: number; left: number; right: number };

// Même cadrage que map.fitBounds (sans rotation ni inclinaison), calculé à
// part : pendant l'intro la carte est en globe, et cameraForBounds le suivrait.
function landingView(points: [number, number][], pad: Padding, w: number, h: number, maxZoom: number) {
  const xs = points.map(([lng]) => mercX(lng));
  const ys = points.map(([, lat]) => mercY(lat));
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const availW = Math.max(1, w - pad.left - pad.right);
  const availH = Math.max(1, h - pad.top - pad.bottom);
  const span = Math.max((x1 - x0) / availW, (y1 - y0) / availH, 1e-9);
  const zoom = Math.min(maxZoom, Math.log2(1 / (512 * span)));
  const world = 512 * 2 ** zoom;
  // Centre de la carte : celui des courses, décalé vers la partie visible.
  const cx = (x0 + x1) / 2 - (pad.left - pad.right) / 2 / world;
  const cy = (y0 + y1) / 2 - (pad.top - pad.bottom) / 2 / world;
  return { cx, cy, zoom };
}

// URL des tuiles que MapLibre demandera à l'arrivée : pour chaque source
// qu'une couche affiche à ce zoom, les tuiles qui couvrent l'écran. Zoom des
// tuiles comme MapLibre pour des tuiles de 512 px : arrondi inférieur pour
// les tuiles vectorielles, au plus proche pour les images (relief).
function landingTiles(map: any, view: { cx: number; cy: number; zoom: number }, w: number, h: number): string[] {
  const layers = map.getStyle().layers;
  const urls: string[] = [];
  for (const [id, def] of Object.entries<any>(map.getStyle().sources)) {
    const src = map.getSource(id);
    const template: string | undefined = src?.tiles?.[0];
    if (!template || !/^(vector|raster|raster-dem)$/.test(def.type)) continue;
    const shown = layers.some(
      (l: any) =>
        l.source === id &&
        l.layout?.visibility !== "none" &&
        (l.minzoom ?? 0) <= view.zoom &&
        view.zoom < (l.maxzoom ?? 24),
    );
    if (!shown) continue;
    const raw = def.type === "vector" ? Math.floor(view.zoom) : Math.round(view.zoom);
    if (raw < (src.minzoom ?? 0)) continue;
    const z = Math.min(raw, src.maxzoom ?? 22);
    const n = 2 ** z;
    const world = 512 * 2 ** view.zoom;
    const tx = (x: number) => Math.floor(x * n);
    const ty = (y: number) => Math.min(n - 1, Math.max(0, Math.floor(y * n)));
    const [xa, xb] = [tx(view.cx - w / 2 / world), tx(view.cx + w / 2 / world)];
    const [ya, yb] = [ty(view.cy - h / 2 / world), ty(view.cy + h / 2 / world)];
    for (let x = xa; x <= xb; x++)
      for (let y = ya; y <= yb; y++) {
        const wx = ((x % n) + n) % n; // le monde se répète en longitude
        const yy = src.scheme === "tms" ? n - 1 - y : y;
        urls.push(template.replace("{z}", String(z)).replace("{x}", String(wx)).replace("{y}", String(yy)));
      }
  }
  return urls.slice(0, MAX_PREFETCH);
}

// Télécharge les tuiles de la vue d'arrivée en faisant avancer la barre du
// logo. Ne rejette jamais : une tuile en échec sera redemandée par MapLibre.
export function prefetchLanding(map: any, points: [number, number][], pad: Padding, maxZoom: number): Promise<void> {
  if (!points.length) return Promise.resolve();
  const canvas = map.getCanvas();
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const view = landingView(points, pad, w, h, maxZoom);
  const urls = landingTiles(map, view, w, h);
  const progress = new DownloadProgress(urls.length, (p) => setSplashProgress(0.1 + 0.9 * p));
  return Promise.all(urls.map((url, i) => progress.fetch(url, i))).then(() => {});
}

// Avancement d'un lot de téléchargements, en octets transférés : une tuile
// du fond pèse 300 à 550 Ko, une du relief 150 à 370 Ko, une de la couche
// outdoor 15 à 50 Ko ; compter les tuiles faisait stagner puis sauter la
// barre. Chaque tuile pèse son Content-Length (octets sur le réseau), connu
// dès ses en-têtes ; avant, la moyenne des tailles déjà connues.
//
// Le corps lu est décompressé : pour le fond (gzip), les octets lus dépassent
// le Content-Length. Le rapport lu / transféré s'apprend sur la première
// tuile terminée de chaque type ; d'ici là, ~2,5 pour les tuiles vectorielles
// (gzip chez MapTiler), 1 pour les images (webp, déjà compressées).
class DownloadProgress {
  private expected: (number | null)[];
  private received: number[];
  private ratio = new Map<string, number>();
  private frame = 0;
  private shown = 0;
  private onProgress: (p: number) => void;

  constructor(n: number, onProgress: (p: number) => void) {
    this.onProgress = onProgress;
    this.expected = Array(n).fill(null);
    this.received = Array(n).fill(0);
  }

  async fetch(url: string, i: number): Promise<void> {
    try {
      const res = await fetch(url);
      const size = Number(res.headers.get("content-length")) || null;
      const type = res.headers.get("content-type") || "";
      this.expected[i] = size;
      if (!res.body) {
        await res.arrayBuffer();
        return;
      }
      // Lu jusqu'au bout, sinon le navigateur ne le met pas en cache.
      const reader = res.body.getReader();
      let read = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        read += value.byteLength;
        if (size) {
          const r = this.ratio.get(type) ?? (type.startsWith("image/") ? 1 : 2.5);
          this.received[i] = Math.min(0.97 * size, read / r);
          this.schedule();
        }
      }
      if (size && !this.ratio.has(type)) this.ratio.set(type, read / size);
    } catch {
      // MapLibre la redemandera.
    } finally {
      this.received[i] = this.expected[i] ??= 1;
      this.schedule();
    }
  }

  // Une mise à jour de la barre par image, pas une par morceau reçu.
  private schedule() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      const known = this.expected.filter((e): e is number => e !== null);
      const guess = known.length ? known.reduce((a, b) => a + b, 0) / known.length : 1;
      const total = this.expected.reduce<number>((a, e) => a + (e ?? guess), 0);
      const got = this.received.reduce((a, b) => a + b, 0);
      // Jamais en arrière : la taille d'une tuile qui remplace la moyenne
      // estimée peut faire grossir le total.
      this.shown = Math.max(this.shown, total ? Math.min(1, got / total) : 1);
      this.onProgress(this.shown);
    });
  }
}

// --- Globe ---

export function setupGlobe(map: any) {
  map.on("style.load", () => {
    // Globe vu de loin, carte plate dès la vue régionale : en globe, le fond
    // outdoor (≈120 couches) se dessine trop lentement et la carte saccade.
    map.setProjection({
      type: ["interpolate", ["linear"], ["zoom"], 2, "vertical-perspective", 4, "mercator"],
    });
    map.setSky({
      "sky-color": "#1d3b6e",
      "horizon-color": "#f6c9a3",
      "fog-color": "#ffffff",
      "sky-horizon-blend": 0.6,
      "horizon-fog-blend": 0.5,
      "fog-ground-blend": 0.8,
      // Atmosphère visible de loin, effacée en vue régionale.
      "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 2, 1, 4, 0],
    });
  });
}

// Zoom minimal d'apparition des détails du fond outdoor (identifiants de
// couches MapTiler). Le style les montre très tôt (sentiers balisés dès le
// zoom 5, villages dès 8) et ils noient les courses en vue régionale ; on les
// garde, mais seulement quand on s'approche.
const DETAIL_MINZOOM: [RegExp, number][] = [
  [/^Longdistance trail|^Bicycle longdistance/, 11],
  [/trail|^Bicycle local|^Via ferrata/i, 13],
  [/^(Path|Track|Steps|Tunnel path|Pedestrian)/, 14],
  [/^(Glacier )?[Cc]ontour/, 12],
  [/^(Peak|Volcano) labels/, 12],
  [/^(Village|Place) labels$/, 10],
  [/^(Road|River|Aerialway) labels$/, 11],
  [/^(Airport|Lake) labels$/, 11],
  [/^Park outline$/, 11],
  [/^National park labels$/, 9],
  [/^Protected area labels$/, 13],
  [/^(Outdoor shop|Sport|Outdoor water|Transport|Food|Park labels|Healthcare|Tourism|Culture|Outdoor|Station|Castle)$/, 15],
];

export function calmBasemap(map: any) {
  map.on("style.load", () => {
    for (const layer of map.getStyle().layers) {
      const rule = DETAIL_MINZOOM.find(([re]) => re.test(layer.id));
      if (!rule || (layer.minzoom ?? 0) >= rule[1]) continue;
      map.setLayerZoomRange(layer.id, rule[1], layer.maxzoom ?? 24);
    }
  });
}

// Fait tourner le globe en attendant les données. Renvoie la fonction d'arrêt.
export function startSpin(map: any): () => void {
  let raf = 0;
  let last = performance.now();
  const tick = (now: number) => {
    const dt = now - last;
    last = now;
    const c = map.getCenter();
    map.jumpTo({ center: [c.lng + dt * 0.012, c.lat] });
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(raf);
}

// --- Ordre d'apparition ---

function distKm(a: [number, number], b: [number, number]): number {
  const toRad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toRad;
  const dLng = (b[0] - a[0]) * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLng / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

// Ajoute à chaque point `reveal_at` (0 → 1) : instant où il apparaît, du plus
// proche du club au plus lointain. Par rang plutôt que par distance, sinon une
// course outre-mer écraserait toute la cascade sur la première demi-seconde.
export function assignRevealOrder(features: any[]) {
  const sorted = [...features].sort(
    (a, b) =>
      distKm(CLUB_HOME, a.geometry.coordinates) - distKm(CLUB_HOME, b.geometry.coordinates),
  );
  const n = sorted.length;
  sorted.forEach((f, i) => {
    f.properties.reveal_at = ARC_GROW + (1 - ARC_GROW) * (n > 1 ? i / (n - 1) : 0);
  });
}

// --- Arcs ---

const ARC_STEPS = 48;

// Courbe de Bézier quadratique, bombée vers le nord : les arcs « décollent ».
function arcCoords(from: [number, number], to: [number, number]): [number, number][] {
  const [x0, y0] = from;
  const [x1, y1] = to;
  const dx = x1 - x0;
  const dy = y1 - y0;
  const len = Math.hypot(dx, dy);
  let nx = -dy / len;
  let ny = dx / len;
  if (ny < 0) { nx = -nx; ny = -ny; }
  const bend = len * 0.22;
  const cx = (x0 + x1) / 2 + nx * bend;
  const cy = (y0 + y1) / 2 + ny * bend;
  const pts: [number, number][] = [];
  for (let i = 0; i <= ARC_STEPS; i++) {
    const t = i / ARC_STEPS;
    const u = 1 - t;
    pts.push([u * u * x0 + 2 * u * t * cx + t * t * x1, u * u * y0 + 2 * u * t * cy + t * t * y1]);
  }
  return pts;
}

let arcFeatures: any[] = [];

// Un arc par course à venir (les courses passées restent sans arc, sauf
// pendant « Rejouer la saison »).
function buildArcs(raceFeatures: any[], withPast = false) {
  return raceFeatures
    .filter((f) => withPast || f.properties.temporality !== "past")
    .filter((f) => distKm(CLUB_HOME, f.geometry.coordinates) > 3)
    .map((f) => ({
      type: "Feature",
      id: f.id, // pendant le film : même id que la course (feature-state)
      geometry: { type: "LineString", coordinates: arcCoords(CLUB_HOME, f.geometry.coordinates) },
      properties: {
        member_count: f.properties.member_count,
        reveal_at: f.properties.reveal_at ?? 1,
      },
    }));
}

const fc = (features: any[]) => ({ type: "FeatureCollection", features });

// Durées (en part de l'animation) de la pousse d'un arc et du fondu d'un point :
// ARC_GROW et 0,1 pour la cascade, plus courtes pour la saison qui dure plus.
let growWin = ARC_GROW;
let fadeWin = 0.1;

// Tronque chaque arc selon l'avancement t de la cascade (0 → 1).
function growArcs(t: number) {
  return fc(
    arcFeatures.flatMap((f) => {
      const end = f.properties.reveal_at;
      const p = Math.min(1, Math.max(0, (t - (end - growWin)) / growWin));
      if (p <= 0) return [];
      const coords = f.geometry.coordinates;
      const k = p * ARC_STEPS;
      const i = Math.floor(k);
      const part = coords.slice(0, i + 1);
      if (i < ARC_STEPS) {
        const [a, b] = [coords[i], coords[i + 1]];
        const r = k - i;
        part.push([a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r]);
      }
      return part.length > 1 ? [{ ...f, geometry: { type: "LineString", coordinates: part } }] : [];
    }),
  );
}

// Arc en croissance : la tête est la plus lumineuse (effet comète).
const GROW_GRADIENT = [
  "interpolate", ["linear"], ["line-progress"],
  0, `rgba(${ORANGE},0)`,
  0.6, `rgba(${ORANGE},0.55)`,
  1, "rgba(255,240,220,1)",
];

// Arc au repos : un éclat parcourt la ligne du club vers la course (tête en p).
function restGradient(p: number) {
  const base = `rgba(${ORANGE},0.5)`;
  const stops: [number, string][] = [
    [0, `rgba(${ORANGE},0.08)`],
    [0.2, base],
    [p - 0.12, base],
    [p, "rgba(255,240,220,0.95)"],
    [p + 0.04, base],
    [1, base],
  ];
  const flat: any[] = [];
  let prev = -1;
  for (const [s, c] of stops) {
    if (s <= prev || s < 0 || s > 1) continue;
    flat.push(s, c);
    prev = s;
  }
  return ["interpolate", ["linear"], ["line-progress"], ...flat];
}

// Sources et couches des arcs et du club, sous les points des courses.
export function addArcLayers(map: any, beforeId: string) {
  map.addSource("arcs", { type: "geojson", data: fc([]), lineMetrics: true });
  map.addSource("club-home", {
    type: "geojson",
    data: fc([{ type: "Feature", geometry: { type: "Point", coordinates: CLUB_HOME }, properties: {} }]),
  });
  const width = ["interpolate", ["linear"], ["get", "member_count"], 1, 1.4, 10, 3.2];
  map.addLayer({
    id: "arcs-glow",
    type: "line",
    source: "arcs",
    layout: { "line-cap": "round" },
    paint: {
      "line-color": `rgb(${ORANGE})`,
      "line-width": ["*", width, 4],
      "line-blur": 6,
      "line-opacity": ["*", 0.18, ["coalesce", ["feature-state", "a"], 1]],
    },
  }, beforeId);
  map.addLayer({
    id: "arcs",
    type: "line",
    source: "arcs",
    layout: { "line-cap": "round" },
    paint: {
      "line-width": width,
      "line-gradient": restGradient(1),
      // Pendant le film, les arcs des mois passés s'estompent.
      "line-opacity": ["coalesce", ["feature-state", "a"], 1],
    },
  }, beforeId);
  map.addLayer({
    id: "club-dot",
    type: "circle",
    source: "club-home",
    paint: {
      "circle-color": "#6B2D5B",
      "circle-radius": 6,
      "circle-stroke-width": 2.5,
      "circle-stroke-color": "#fff",
    },
  }, beforeId);
  // Halo du club en CSS (marqueur HTML) : le navigateur l'anime seul, sans
  // redessiner la carte à chaque image comme le ferait une couche MapLibre.
  const halo = document.createElement("div");
  halo.className = "club-halo";
  halo.setAttribute("aria-hidden", "true");
  new maplibregl.Marker({ element: halo }).setLngLat(CLUB_HOME).addTo(map);
}

// --- Cascade ---

type RevealSpec = { layer: string; prop: string; base: any; key: string; pop: boolean };
let revealSpecs: RevealSpec[] = [];
let revealT = 1; // 1 = tout est affiché
let revealRaf = 0;

// Couche dont la propriété `prop` (rayon, opacité) suit la cascade : elle vaut
// 0 avant l'instant `key` du point, puis rebondit jusqu'à `base`.
export function registerReveal(layer: string, prop: string, base: any, key: string, pop = true) {
  revealSpecs.push({ layer, prop, base, key, pop });
}

function revealFactor(t: number, key: string, pop: boolean) {
  const x = ["-", t, ["coalesce", ["get", key], 0]];
  return pop
    ? ["interpolate", ["linear"], x, 0, 0, 0.08, 1.25, 0.16, 1]
    : ["interpolate", ["linear"], x, 0, 0, fadeWin, 1];
}

function applyReveal(map: any, t: number) {
  for (const s of revealSpecs) {
    map.setPaintProperty(
      s.layer,
      s.prop,
      t >= 1 + fadeWin * 2 ? s.base : ["*", s.base, revealFactor(t, s.key, s.pop)],
    );
  }
  updateArcSource(map, t);
  map.setPaintProperty("arcs", "line-gradient", t >= 1 ? restGradient(1) : GROW_GRADIENT);
}

// Renvoyer les arcs fait retraiter leur source par MapLibre : on ne le fait
// que si quelque chose change, et au plus toutes les `arcGapMs` ms (pendant le
// film, pour laisser les workers préparer les tuiles du fond qui défile).
let arcsKey = "";
let arcsAt = 0;
let arcGapMs = 0;

function updateArcSource(map: any, t: number) {
  let key: string;
  if (t >= 1) key = "all";
  else {
    let growing = false;
    let done = 0;
    for (const f of arcFeatures) {
      const p = (t - (f.properties.reveal_at - growWin)) / growWin;
      if (p >= 1) done++;
      else if (p > 0) growing = true;
    }
    key = growing ? "grow" : `rest${done}`;
  }
  const now = performance.now();
  if (key === arcsKey && (key !== "grow" || now - arcsAt < arcGapMs)) return;
  arcsKey = key;
  arcsAt = now;
  map.getSource("arcs")?.setData(t >= 1 ? fc(arcFeatures) : growArcs(t));
}

// Masque points et arcs, en attendant la cascade.
export function hideForReveal(map: any) {
  if (reducedMotion()) return;
  cancelAnimationFrame(revealRaf);
  stopGlint();
  revealT = 0;
  applyReveal(map, 0);
}

// À appeler à chaque changement des courses affichées (filtres).
export function updateArcs(map: any, raceFeatures: any[]) {
  arcFeatures = buildArcs(raceFeatures);
  arcsKey = "";
  if (revealT >= 1) map.getSource("arcs")?.setData(fc(arcFeatures));
}

// La cascade attend que les tuiles soient chargées (au plus 1,5 s) : lancée
// pendant le chargement, elle sautait des images et semblait instantanée.
// Elle avance au rythme des images affichées, pas de l'horloge : sur un
// appareil lent elle dure plus longtemps, mais elle reste visible.
export function playReveal(map: any) {
  cancelAnimationFrame(revealRaf);
  stopGlint();
  if (reducedMotion()) {
    revealT = 1.2;
    applyReveal(map, revealT);
    return;
  }
  let begun = false;
  const begin = () => {
    if (begun) return;
    begun = true;
    let last = performance.now();
    let lastApply = 0;
    const tick = (now: number) => {
      revealT = Math.min(1.2, revealT + (Math.min(now - last, 100) / REVEAL_MS) * 1.2);
      last = now;
      // ~30 mises à jour par seconde suffisent et ménagent les mobiles.
      if (now - lastApply >= 30 || revealT >= 1.2) {
        lastApply = now;
        applyReveal(map, revealT);
      }
      if (revealT < 1.2) revealRaf = requestAnimationFrame(tick);
      else startGlint(map);
    };
    revealRaf = requestAnimationFrame(tick);
  };
  if (map.areTilesLoaded()) begin();
  else {
    map.once("idle", begin);
    setTimeout(begin, 1500);
  }
}

// --- Éclat sur les arcs après la cascade ---
// Chaque mise à jour redessine toute la carte, coûteux avec le fond outdoor :
// l'éclat passe GLINT_COUNT fois puis la carte redevient immobile.

const GLINT_MS = 1400;
const GLINT_COUNT = 3;
let glintRaf = 0;

function stopGlint() {
  cancelAnimationFrame(glintRaf);
}

function startGlint(map: any) {
  cancelAnimationFrame(glintRaf);
  const start = performance.now();
  let last = 0;
  let resting = false;
  const tick = (now: number) => {
    const elapsed = now - start;
    const cycle = Math.floor(elapsed / PULSE_PERIOD_MS);
    const inCycle = elapsed % PULSE_PERIOD_MS;
    if (cycle >= GLINT_COUNT) {
      map.setPaintProperty("arcs", "line-gradient", restGradient(1));
      return;
    }
    glintRaf = requestAnimationFrame(tick);
    if (now - last < 33) return;
    last = now;
    // Hors de l'éclat : un seul retour au repos, puis plus aucun redessin.
    if (inCycle > GLINT_MS) {
      if (!resting) map.setPaintProperty("arcs", "line-gradient", restGradient(1));
      resting = true;
      return;
    }
    resting = false;
    map.setPaintProperty("arcs", "line-gradient", restGradient(0.15 + (inCycle / GLINT_MS) * 0.9));
  };
  glintRaf = requestAnimationFrame(tick);
}

// --- Rejouer la saison ---
// Pilotée de l'extérieur (season.ts) : l'affichage ne dépend que de l'instant
// t du film (0 → 1), ce qui permet la pause, la vitesse et la barre de temps.
//
// Seul le mois en cours est mis en avant : ses courses arrivent en grand avec
// arc, onde et flash ; à la fin de leur mois (`focus_end`), elles deviennent un
// petit point et leur arc s'estompe. L'écran reste lisible jusqu'au bout.
//
// Le film n'utilise pas les couches habituelles. Y changer à chaque image une
// propriété qui dépend de chaque course (comme la cascade le fait) oblige
// MapLibre à recharger les tuiles de la source, et les marqueurs clignotent ou
// disparaissent. Le film a ses propres couches, sans regroupement, animées par
// feature-state (aucun rechargement).

const NO_FX = fc([]);
const MAIN_LAYERS = ["clusters", "cluster-count", "race-points", "edition-badge"];
const ARC_TRACE = 0.12; // opacité restante des arcs des mois passés
let filmFeatures: any[] = [];
let filmState: string[] = []; // dernier état envoyé, par course
let arcState: string[] = [];
let ringWin = 0.05;
let flashWin = 0.02;
let outWin = 0.02; // passage du grand marqueur au petit point

// `pointLayout` : mise en forme des marqueurs de course, reprise telle quelle.
export function addSeasonLayers(map: any, beforeId: string, pointLayout: any) {
  map.addSource("season-fx", { type: "geojson", data: NO_FX });
  const state = (key: string, dflt: number) => ["coalesce", ["feature-state", key], dflt];
  // Trace des mois passés : un petit point de la couleur de la course.
  map.addLayer({
    id: "season-dots",
    type: "circle",
    source: "season-fx",
    paint: {
      "circle-color": ["get", "color"],
      "circle-radius": 4,
      "circle-stroke-color": "#fff",
      "circle-stroke-width": 1.2,
      "circle-opacity": state("d", 0),
      "circle-stroke-opacity": state("d", 0),
    },
  }, beforeId);
  // Onde : un anneau qui s'élargit en s'effaçant (âge de 0 à 1).
  map.addLayer({
    id: "season-ring",
    type: "circle",
    source: "season-fx",
    paint: {
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": ["get", "color"],
      "circle-stroke-width": 2.5,
      "circle-radius": ["interpolate", ["linear"], state("ring", -1), 0, 4, 1, 50],
      "circle-stroke-opacity": ["interpolate", ["linear"], state("ring", -1), -0.01, 0, 0, 0.95, 1, 0],
    },
  }, beforeId);
  // Flash : un disque clair et flou, plus bref.
  map.addLayer({
    id: "season-flash",
    type: "circle",
    source: "season-fx",
    paint: {
      "circle-color": "#fff8ee",
      "circle-blur": 0.7,
      "circle-radius": ["interpolate", ["linear"], state("flash", -1), 0, 6, 1, 28],
      "circle-opacity": ["interpolate", ["linear"], state("flash", -1), -0.01, 0, 0, 0.9, 1, 0],
    },
  }, beforeId);
  map.addLayer({
    id: "season-points",
    type: "symbol",
    source: "season-fx",
    layout: pointLayout,
    paint: { "text-color": "#fff", "icon-opacity": state("o", 0), "text-opacity": state("o", 0) },
  });
}

// Prépare le film. Les durées sont en part du film (t de 0 à 1). Chaque course
// porte `reveal_at` (arrivée) et `focus_end` (fin de sa mise en avant).
export function seasonBegin(
  map: any,
  raceFeatures: any[],
  win: { grow: number; fade: number; ring: number; flash: number; out: number },
) {
  cancelAnimationFrame(revealRaf);
  stopGlint();
  growWin = win.grow;
  fadeWin = win.fade;
  ringWin = win.ring;
  flashWin = win.flash;
  outWin = win.out;
  filmFeatures = raceFeatures.map((f, i) => ({ ...f, id: i }));
  filmState = [];
  arcState = [];
  arcFeatures = buildArcs(filmFeatures, true);
  arcsKey = "";
  arcGapMs = 90;
  map.getSource("season-fx")?.setData(fc(filmFeatures));
  for (const id of MAIN_LAYERS) map.setLayoutProperty(id, "visibility", "none");
  map.setPaintProperty("arcs", "line-gradient", GROW_GRADIENT);
  seasonApply(map, 0);
}

// Affiche le film à l'instant t.
export function seasonApply(map: any, t: number) {
  revealT = t;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  filmFeatures.forEach((f, i) => {
    const x = t - f.properties.reveal_at;
    const after = clamp((t - f.properties.focus_end) / outWin); // 0 → 1 : passage au point
    const o = clamp(x / fadeWin) * (1 - after);
    const d = x < 0 ? 0 : after;
    // Hors de leur fenêtre, onde et flash valent -1 (invisibles).
    const ring = x < 0 || x > ringWin ? -1 : x / ringWin;
    const flash = x < 0 || x > flashWin ? -1 : x / flashWin;
    const key = `${o.toFixed(2)}|${d.toFixed(2)}|${ring.toFixed(2)}|${flash.toFixed(2)}`;
    if (filmState[i] !== key) {
      filmState[i] = key;
      map.setFeatureState({ source: "season-fx", id: i }, { o, d, ring, flash });
    }
    const a = (1 - (1 - ARC_TRACE) * after).toFixed(2);
    if (arcState[i] !== a) {
      arcState[i] = a;
      map.setFeatureState({ source: "arcs", id: i }, { a: Number(a) });
    }
  });
  updateArcSource(map, Math.min(t, 0.999)); // les arcs gardent leur dégradé « comète »
}

// Arrête le film et rend les couches habituelles, entièrement affichées.
// L'appelant remet ensuite les courses filtrées.
export function stopSeason(map: any) {
  growWin = ARC_GROW;
  fadeWin = 0.1;
  arcGapMs = 0;
  filmFeatures = [];
  arcsKey = "";
  map.removeFeatureState({ source: "arcs" });
  map.getSource("season-fx")?.setData(NO_FX);
  for (const id of MAIN_LAYERS) map.setLayoutProperty(id, "visibility", "visible");
  revealT = 1.2;
  applyReveal(map, revealT);
}
