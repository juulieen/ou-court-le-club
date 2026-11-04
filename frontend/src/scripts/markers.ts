// Icônes des courses : la forme dit le type (sommet = trail, panneau routier =
// route, losange = autre), la couleur dit la date. Dessinées une fois sur un
// canvas puis données à MapLibre (addImage) : aussi léger que des cercles.
// Le nombre de coureurs est un texte MapLibre posé par-dessus.

const RATIO = 2; // netteté sur écrans haute densité
const SHADOW = "rgba(0,0,0,0.35)";

export type RaceType = "trail" | "route" | "autre";

// Taille du canvas (px CSS) ; la pointe/base de l'icône est en bas au centre.
const SIZE: Record<string, [number, number]> = {
  trail: [42, 30],
  route: [34, 34],
  autre: [32, 34],
  "cluster-trail": [52, 39],
  "cluster-route": [48, 39],
  "cluster-mixed": [52, 39],
};

// Décalage vertical (em, texte 11 px) pour centrer le nombre dans la forme.
export const TEXT_OFFSET: Record<RaceType, [number, number]> = {
  trail: [0, -0.75],
  route: [0, -1.55],
  autre: [0, -1.4],
};

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w * RATIO;
  c.height = h * RATIO;
  const ctx = c.getContext("2d")!;
  ctx.scale(RATIO, RATIO);
  return { c, ctx };
}

function withShadow(ctx: CanvasRenderingContext2D, draw: () => void) {
  ctx.save();
  ctx.shadowColor = SHADOW;
  ctx.shadowBlur = 3;
  ctx.shadowOffsetY = 1.5;
  draw();
  ctx.restore();
}

function outline(ctx: CanvasRenderingContext2D, path: Path2D, fill: string, lineWidth: number) {
  withShadow(ctx, () => {
    ctx.fillStyle = fill;
    ctx.fill(path);
  });
  ctx.lineWidth = lineWidth;
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#fff";
  ctx.stroke(path);
}

// Chaque forme est dessinée avec sa base (ou sa pointe) en (x, y).
function trailShape(ctx: CanvasRenderingContext2D, color: string, x: number, y: number, s: number) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(s, s);
  outline(ctx, new Path2D("M-15,0 L-4,-19 L1,-11 L5,-16 L15,0 Z"), color, 2 / s);
  // Neige au sommet.
  ctx.fillStyle = "#fff";
  ctx.fill(new Path2D("M-7.6,-12.8 L-4,-19 L-0.5,-13 L-2.4,-14.4 L-4.4,-12.4 Z"));
  ctx.restore();
}

function routeShape(ctx: CanvasRenderingContext2D, color: string, x: number, y: number, s: number) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(s, s);
  const p = new Path2D("M-12,-24 H12 V-12 Q12,-4 0,0 Q-12,-4 -12,-12 Z");
  outline(ctx, p, color, 2 / s);
  // Bandeau clair en haut, comme sur les panneaux.
  ctx.clip(p);
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.fillRect(-12, -24, 24, 4.5);
  ctx.restore();
}

function autreShape(ctx: CanvasRenderingContext2D, color: string, x: number, y: number) {
  ctx.save();
  ctx.translate(x, y);
  outline(ctx, new Path2D("M0,0 L-12,-13 Q-13,-14 -12,-15 L-1,-27 Q0,-28 1,-27 L12,-15 Q13,-14 12,-13 Z"), color, 2);
  ctx.restore();
}

// Regroupement : deux formes qui se chevauchent, selon ce qu'il contient
// (deux sommets, deux panneaux, ou un de chaque).
function clusterShape(kind: ClusterKind) {
  return (ctx: CanvasRenderingContext2D, color: string) => {
    const back = kind === "trail" ? trailShape : routeShape;
    const front = kind === "route" ? routeShape : trailShape;
    back(ctx, color, kind === "trail" ? 30 : 31, kind === "trail" ? 27 : 33, kind === "trail" ? 1 : 0.95);
    front(ctx, color, kind === "route" ? 18 : 20, 36, kind === "route" ? 1.15 : 1.25);
  };
}

// Pastille blanche étirable (icon-text-fit) pour le nombre de courses.
function drawPill(ctx: CanvasRenderingContext2D, color: string) {
  const p = new Path2D();
  p.roundRect(1.5, 1.5, 17, 14, 7);
  withShadow(ctx, () => {
    ctx.fillStyle = "#fff";
    ctx.fill(p);
  });
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = color;
  ctx.stroke(p);
}

export type ClusterKind = "trail" | "route" | "mixed";

type IconKind = RaceType | `cluster-${ClusterKind}`;

const DRAW: Record<IconKind, (ctx: CanvasRenderingContext2D, c: string) => void> = {
  trail: (ctx, c) => trailShape(ctx, c, 21, 27, 1.25),
  route: (ctx, c) => routeShape(ctx, c, 17, 31, 1.15),
  autre: (ctx, c) => autreShape(ctx, c, 16, 31),
  "cluster-trail": clusterShape("trail"),
  "cluster-route": clusterShape("route"),
  "cluster-mixed": clusterShape("mixed"),
};

export const iconName = (type: string, color: string) => `mk-${type}-${color}`;

// Enregistre toutes les variantes (forme × couleur) auprès de la carte.
export function addMarkerImages(map: any, colors: string[]) {
  for (const kind of Object.keys(DRAW) as IconKind[]) {
    for (const color of colors) {
      const name = iconName(kind, color);
      if (map.hasImage(name)) continue;
      const [w, h] = SIZE[kind];
      const { c, ctx } = canvas(w, h);
      DRAW[kind](ctx, color);
      map.addImage(name, ctx.getImageData(0, 0, c.width, c.height), { pixelRatio: RATIO });
    }
  }
  // Pastille : seule sa bande centrale s'étire en largeur autour du texte
  // (icon-text-fit "width"), les bouts arrondis restent intacts.
  for (const color of colors) {
    const name = iconName("pill", color);
    if (map.hasImage(name)) continue;
    const { c, ctx } = canvas(20, 17);
    drawPill(ctx, color);
    const r = 9 * RATIO;
    map.addImage(name, ctx.getImageData(0, 0, c.width, c.height), {
      pixelRatio: RATIO,
      stretchX: [[r, r + 2 * RATIO]],
      content: [5 * RATIO, 3 * RATIO, 15 * RATIO, 14 * RATIO],
    });
  }
}

export function normalizeType(t: string | undefined): RaceType {
  return t === "trail" || t === "route" ? t : "autre";
}
