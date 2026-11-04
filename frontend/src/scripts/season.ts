// « Rejouer la saison » : les courses de l'année apparaissent mois par mois,
// carte immobile. Les courses du mois en cours arrivent en grand (arc depuis
// le club, onde, nom dans le bandeau) ; le mois fini, elles deviennent de
// petits points. À la fin, la carte normale revient avec le bilan.
//
// Tout l'affichage se déduit de la position `tm` dans le film : pause, ×2 et
// barre de temps n'ont rien d'autre à faire que changer `tm`.

import * as fx from "./mapfx";

declare const maplibregl: any;

const MONTH_MS = 1800; // durée d'un mois à l'écran (en ×1)
const LEAD_MS = 1200; // cadrage, avant le premier mois
const TAIL_MS = 1200; // après le dernier mois, avant le retour à la carte
const FOCUS_MIN_MS = 1100; // une course reste en avant au moins ce temps
const HOLD_MS = 3500; // bilan affiché avant de se fermer
const MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
  "août", "septembre", "octobre", "novembre", "décembre"];
const MONTHS_SHORT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
  "août", "sept.", "oct.", "nov.", "déc."];
// Pendant le film, les courses passées sont en couleur, pas en gris : la
// légende (qui parle d'aujourd'hui) est masquée le temps du film.
const FILM_PAST_COLOR = "#F57C20";

type Padding = { top: number; bottom: number; left: number; right: number };
type Deps = {
  map: any;
  races: () => any[]; // toutes les courses (sans filtres)
  toGeoJSON: (races: any[]) => any;
  restore: () => void; // réaffiche les courses filtrées
  reframe: () => void; // recadre la carte sur les courses filtrées
  padding: () => Padding; // marges de la partie de carte visible
  immersive: (on: boolean) => void; // plein écran (panneau masqué)
  onTour: () => void; // lance la visite guidée
  canStart: () => boolean;
};

// Une course dans la chronologie du film (t de 0 à 1).
type Step = { at: number; focusEnd: number; members: number; label: string };

export type Season = { playing: () => boolean; stop: () => void };

const clamp01 = (u: number) => Math.min(1, Math.max(0, u));
const dayMs = (date: string) => new Date(date + "T12:00:00").getTime();

export function setupSeason(d: Deps): Season {
  // Saisons au calendrier scolaire, comme celles du club : de septembre à
  // août. Une saison est désignée par son année de départ (2026 = 2026-2027).
  const located = d.races().filter((r) => r.lat != null && r.lng != null && r.date);
  const seasonOf = (date: string) => {
    const [y, m] = date.split("-").map(Number);
    return m >= 9 ? y : y - 1;
  };
  const seasonRaces = (y: number) => located.filter((r) => seasonOf(r.date) === y);
  // Saisons rejouables : au moins 3 courses.
  const counts = new Map<number, number>();
  for (const r of located) counts.set(seasonOf(r.date), (counts.get(seasonOf(r.date)) || 0) + 1);
  const seasons = [...counts].filter(([, n]) => n >= 3).map(([y]) => y).sort((a, b) => a - b);
  const noop: Season = { playing: () => false, stop: () => {} };
  if (fx.reducedMotion() || !seasons.length) return noop;
  const today = new Date();
  const current = today.getMonth() >= 8 ? today.getFullYear() : today.getFullYear() - 1;
  // Par défaut la saison en cours, sinon la plus récente rejouable.
  const defaultSeason = seasons.includes(current) ? current : seasons[seasons.length - 1];
  let year = defaultSeason;
  const longName = (y: number) => `${y}-${y + 1}`;
  const shortName = (y: number) => `${String(y).slice(2)}-${String(y + 1).slice(2)}`;
  const map = d.map;

  // --- Bouton, dans les contrôles de la carte sous le zoom ---
  const ctrl = document.createElement("div");
  ctrl.className = "maplibregl-ctrl maplibregl-ctrl-group season-ctrl";
  const openBtn = document.createElement("button");
  openBtn.type = "button";
  openBtn.className = "season-btn";
  openBtn.title = `Rejouer la saison ${longName(year)}`;
  openBtn.innerHTML =
    `<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2.5 1.5v9l8-4.5z" fill="currentColor"/></svg>` +
    `<span>Saison ${shortName(year)}</span>`;
  ctrl.appendChild(openBtn);
  map.addControl({ onAdd: () => ctrl, onRemove: () => ctrl.remove() }, "top-right");

  // --- Bandeau : mois, compteurs, course qui arrive, contrôles ---
  const hud = document.createElement("div");
  hud.id = "season-hud";
  hud.hidden = true;
  hud.innerHTML = `
    <div class="season-name">
      <button type="button" class="season-prev" aria-label="Saison précédente">‹</button>
      <span class="season-label"></span>
      <button type="button" class="season-next" aria-label="Saison suivante">›</button>
    </div>
    <div class="season-month"></div>
    <div class="season-stats"><b class="season-n">0</b> <span class="season-nl">course</span> · <b class="season-m">0</b> <span class="season-ml">inscription</span></div>
    <div class="season-caption"></div>
    <div class="season-controls">
      <button type="button" class="season-play" aria-label="Pause"></button>
      <div class="season-bar">
        <span class="season-fill"></span><i class="season-today" title="Aujourd'hui"></i>
        <input type="range" class="season-seek" min="0" max="1000" step="1" value="0" aria-label="Position dans la saison">
      </div>
      <button type="button" class="season-speed" aria-label="Vitesse">×1</button>
    </div>
    <div class="season-end-actions">
      <button type="button" class="season-other"></button>
      <button type="button" class="season-tour">Découvrir la carte →</button>
    </div>
    <button type="button" class="season-stop" aria-label="Fermer la saison">✕</button>`;
  document.body.appendChild(hud);
  const $ = (sel: string) => hud.querySelector(sel) as HTMLElement;
  const monthEl = $(".season-month");
  const labelEl = $(".season-label");
  const prevBtn = $(".season-prev") as HTMLButtonElement;
  const nextBtn = $(".season-next") as HTMLButtonElement;
  const otherBtn = $(".season-other") as HTMLButtonElement;
  const nEl = $(".season-n");
  const mEl = $(".season-m");
  const nLabel = $(".season-nl");
  const mLabel = $(".season-ml");
  const captionEl = $(".season-caption");
  const fillEl = $(".season-fill");
  const todayEl = $(".season-today");
  const playBtn = $(".season-play") as HTMLButtonElement;
  const speedBtn = $(".season-speed") as HTMLButtonElement;
  const seek = $(".season-seek") as HTMLInputElement;

  const ICON_PAUSE = `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M3 2h2v8H3zM7 2h2v8H7z" fill="currentColor"/></svg>`;
  const ICON_PLAY = `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M3 1.5v9l7.5-4.5z" fill="currentColor"/></svg>`;
  const ICON_AGAIN = `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M6 2a4 4 0 1 0 4 4h-1.5A2.5 2.5 0 1 1 6 3.5V5l2.5-2.25L6 .5z" fill="currentColor"/></svg>`;

  // --- État ---
  // off : fermé ; film : le film tourne ou est en pause ; bilan : carte
  // normale revenue, bandeau du bilan encore affiché.
  let phase: "off" | "film" | "bilan" = "off";
  let playing = false;
  let speed = 1;
  let touched = false; // l'utilisateur a manipulé le film : pas de fermeture auto
  let tm = 0; // position dans le film (ms, en ×1)
  let total = 1;
  let raf = 0;
  let holdTimer = 0;
  let steps: Step[] = []; // triées par arrivée
  let totalMembers = 0;
  let t0 = 0; // 1er jour du premier mois
  let t1 = 0; // 1er jour après le dernier mois
  let monthsMs = 1;
  let shownN = -1;
  let shownMonth = "";
  let shownCaption = "";

  function render() {
    const t = tm / total;
    fx.seasonApply(map, t);

    fillEl.style.width = `${t * 100}%`;
    seek.value = String(Math.round(t * 1000));
    const p = clamp01((tm - LEAD_MS) / monthsMs);
    const date = new Date(t0 + p * (t1 - t0 - 1));
    const name = MONTHS[date.getMonth()];
    const month = `${name[0].toUpperCase()}${name.slice(1)} ${date.getFullYear()}`;
    if (month !== shownMonth) {
      shownMonth = month;
      monthEl.textContent = month;
    }

    // Compteurs, et course la plus récente encore mise en avant.
    let n = 0;
    let members = 0;
    let caption = "";
    for (const s of steps) {
      if (s.at > t) break;
      n++;
      members += s.members;
      caption = t <= s.focusEnd ? s.label : "";
    }
    if (n !== shownN) {
      shownN = n;
      setCounts(n, members);
    }
    if (caption !== shownCaption) {
      shownCaption = caption;
      captionEl.textContent = caption;
    }
  }

  function setCounts(n: number, members: number) {
    nEl.textContent = String(n);
    mEl.textContent = String(members);
    nLabel.textContent = n > 1 ? "courses" : "course";
    mLabel.textContent = members > 1 ? "inscriptions" : "inscription";
  }

  function loop() {
    cancelAnimationFrame(raf);
    let last = performance.now();
    const tick = (now: number) => {
      tm = Math.min(total, tm + Math.min(now - last, 100) * speed);
      last = now;
      render();
      if (tm >= total) finale();
      else if (playing) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }

  function setPlaying(on: boolean) {
    playing = on;
    const again = phase === "bilan";
    playBtn.innerHTML = on ? ICON_PAUSE : again ? ICON_AGAIN : ICON_PLAY;
    playBtn.setAttribute("aria-label", on ? "Pause" : again ? "Revoir" : "Lecture");
    if (on) loop();
    else cancelAnimationFrame(raf);
  }

  // Quitte le film : couches habituelles, légende et panneau reviennent.
  function leaveFilm(reframe: boolean) {
    cancelAnimationFrame(raf);
    playing = false;
    phase = "off"; // avant restore() : le rendu des filtres interromprait le film
    hud.classList.remove("season-done");
    fx.stopSeason(map);
    document.body.classList.remove("season-on");
    d.immersive(false);
    d.restore();
    // Sur mobile, attendre que le panneau soit redéplié (transition de 0,3 s).
    if (reframe) setTimeout(d.reframe, window.innerWidth <= 768 ? 320 : 0);
  }

  // Fin du film : la carte normale revient, le bandeau garde le bilan.
  function finale() {
    leaveFilm(true);
    phase = "bilan";
    setPlaying(false);
    hud.classList.add("season-done");
    monthEl.textContent = `Saison ${longName(year)}`;
    setCounts(steps.length, totalMembers);
    captionEl.textContent = "";
    fillEl.style.width = "100%";
    seek.value = "1000";
    seek.disabled = true;
    clearTimeout(holdTimer);
    if (!touched) holdTimer = window.setTimeout(close, HOLD_MS);
  }

  function close() {
    clearTimeout(holdTimer);
    if (phase === "film") leaveFilm(true);
    phase = "off";
    hud.hidden = true;
    openBtn.disabled = false;
  }

  // restart : changement de saison pendant le film (on enchaîne sans le quitter).
  function start(restart = false) {
    if ((phase === "film" && !restart) || !d.canStart()) return;
    const races = seasonRaces(year);
    if (!races.length) return;
    cancelAnimationFrame(raf);
    const i = seasons.indexOf(year);
    labelEl.textContent = `Saison ${longName(year)}`;
    // Les flèches portent le nom de la saison voisine : on voit qu'elle existe.
    prevBtn.disabled = i <= 0;
    nextBtn.disabled = i >= seasons.length - 1;
    if (i > 0) {
      prevBtn.textContent = `‹ ${shortName(seasons[i - 1])}`;
      prevBtn.setAttribute("aria-label", `Saison ${longName(seasons[i - 1])}`);
    }
    if (i < seasons.length - 1) {
      nextBtn.textContent = `${shortName(seasons[i + 1])} ›`;
      nextBtn.setAttribute("aria-label", `Saison ${longName(seasons[i + 1])}`);
    }
    // Au bilan : revoir la saison précédente (ou la suivante, depuis une ancienne).
    const other = i > 0 ? seasons[i - 1] : seasons[i + 1];
    otherBtn.hidden = other === undefined;
    if (other !== undefined) {
      otherBtn.textContent = `${i > 0 ? "Revoir" : "Voir"} la saison ${longName(other)}`;
      otherBtn.dataset.delta = i > 0 ? "-1" : "1";
    }
    clearTimeout(holdTimer);
    phase = "film";
    if (!restart) touched = false;
    speed = 1;
    speedBtn.textContent = "×1";

    // Chronologie : du 1er septembre au lendemain du dernier mois avec des courses.
    const dates = races.map((r) => dayMs(r.date));
    const first = new Date(year, 8, 1);
    const lastD = new Date(Math.max(...dates));
    t0 = first.getTime();
    t1 = new Date(lastD.getFullYear(), lastD.getMonth() + 1, 1).getTime();
    const months = (lastD.getFullYear() - first.getFullYear()) * 12 + lastD.getMonth() - first.getMonth() + 1;
    monthsMs = months * MONTH_MS;
    total = LEAD_MS + monthsMs + TAIL_MS;
    const toT = (ms: number) => (LEAD_MS + ((ms - t0) / (t1 - t0)) * monthsMs) / total;

    const geo = d.toGeoJSON(races);
    for (const f of geo.features) {
      const pr = f.properties;
      const day = new Date(dayMs(pr.date));
      const monthEnd = new Date(day.getFullYear(), day.getMonth() + 1, 1).getTime();
      pr.reveal_at = toT(day.getTime());
      pr.focus_end = Math.max(toT(monthEnd), pr.reveal_at + FOCUS_MIN_MS / total);
      if (pr.temporality === "past") {
        pr.color = FILM_PAST_COLOR;
        pr.urgency = 2;
      }
    }
    steps = geo.features
      .map((f: any) => {
        const pr = f.properties;
        const day = new Date(dayMs(pr.date));
        return {
          at: pr.reveal_at,
          focusEnd: pr.focus_end,
          members: pr.member_count,
          label: `${day.getDate()} ${MONTHS_SHORT[day.getMonth()]} · ${pr.name}`,
        };
      })
      .sort((a: Step, b: Step) => a.at - b.at);
    totalMembers = steps.reduce((s, x) => s + x.members, 0);

    const now = Date.now();
    todayEl.hidden = now < t0 || now > t1;
    todayEl.style.left = `${toT(now) * 100}%`;

    openBtn.disabled = true;
    hud.hidden = false;
    hud.classList.remove("season-done");
    seek.disabled = false;
    document.body.classList.add("season-on");
    // Sur mobile, le bandeau prend la place de la légende (masquée).
    hud.style.top = window.innerWidth <= 768 ? "10px" : "";
    d.immersive(true);

    // Carte immobile pendant tout le film : cadrée une fois sur toutes les
    // courses de l'année, sous le bandeau.
    const pad = d.padding();
    pad.top = Math.max(pad.top, hud.getBoundingClientRect().bottom + 44);
    const bounds = new maplibregl.LngLatBounds();
    races.forEach((r) => bounds.extend([r.lng, r.lat]));
    map.fitBounds(bounds, { padding: pad, maxZoom: 9, pitch: 0, bearing: 0, duration: 900 });

    fx.seasonBegin(map, geo.features, {
      grow: 700 / total,
      fade: 250 / total,
      ring: 1100 / total,
      flash: 450 / total,
      out: 500 / total,
    });
    tm = 0;
    shownN = -1;
    shownMonth = "";
    shownCaption = "";
    setPlaying(true);
  }

  // --- Contrôles ---
  openBtn.addEventListener("click", () => {
    year = defaultSeason;
    start();
  });
  const switchSeason = (delta: number) => {
    const i = seasons.indexOf(year) + delta;
    if (i < 0 || i >= seasons.length) return;
    touched = true;
    year = seasons[i];
    start(true);
  };
  prevBtn.addEventListener("click", () => switchSeason(-1));
  otherBtn.addEventListener("click", () => switchSeason(Number(otherBtn.dataset.delta)));
  nextBtn.addEventListener("click", () => switchSeason(1));
  $(".season-stop").addEventListener("click", close);
  $(".season-tour").addEventListener("click", () => {
    close();
    d.onTour();
  });

  playBtn.addEventListener("click", () => {
    touched = true;
    if (phase === "bilan") return start();
    setPlaying(!playing);
  });

  speedBtn.addEventListener("click", () => {
    touched = true;
    speed = speed === 1 ? 2 : 1;
    speedBtn.textContent = `×${speed}`;
  });

  // Barre de temps : glisser met en pause, relâcher reprend si le film tournait.
  let resumeAfterSeek = false;
  seek.addEventListener("pointerdown", () => {
    touched = true;
    resumeAfterSeek = playing;
    if (playing) setPlaying(false);
  });
  seek.addEventListener("input", () => {
    if (phase !== "film") return;
    touched = true;
    // Juste avant la fin : aller au bout de la barre ne déclenche pas le bilan.
    tm = Math.min(total - 1, (Number(seek.value) / 1000) * total);
    shownN = -1;
    if (!playing) render();
  });
  seek.addEventListener("change", () => {
    if (resumeAfterSeek && phase === "film") setPlaying(true);
    resumeAfterSeek = false;
  });

  return {
    playing: () => phase === "film",
    // Interruption (filtre, fiche de course) : ils cadrent eux-mêmes la carte.
    stop: () => {
      if (phase === "film") leaveFilm(false);
      phase = "off";
      clearTimeout(holdTimer);
      hud.hidden = true;
      openBtn.disabled = false;
    },
  };
}
