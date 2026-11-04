// Visite guidée « Découvrir la carte » : quelques étapes, chacune éclaire un
// élément de la page et l'explique dans une bulle. Lancée par le bouton « ? »,
// depuis le bilan de la saison, ou proposée une fois après l'intro.

const SEEN_KEY = "rev86-tour-seen";

type Step = {
  target: () => Element | null;
  title: string;
  text: string;
};

const isMobile = () => window.innerWidth <= 768;

const STEPS: Step[] = [
  {
    target: () => document.getElementById("legend"),
    title: "Lire la carte",
    text: "Chaque marqueur est une course où des membres du club sont inscrits. La forme dit le type (sommet = trail, panneau = route), la couleur dit quand.",
  },
  {
    target: () => document.querySelector("#race-list .race-card"),
    title: "Les courses",
    text: "Touchez une course, sur la carte ou dans la liste, pour ouvrir sa fiche : date, distances et membres inscrits.",
  },
  {
    target: () => document.querySelector("#race-list .race-card .whatsapp-btn"),
    title: "Partager",
    text: "Envoyez une course au groupe WhatsApp du club en un clic.",
  },
  {
    target: () => document.getElementById(isMobile() ? "filter-toggle" : "filters"),
    title: "Filtrer",
    text: "Courses à venir, récentes ou toutes ; par type, distance ou membre.",
  },
  {
    target: () => document.getElementById("calendar-btn"),
    title: "Agenda",
    text: "Ajoutez les courses du club à votre agenda : il se met à jour tout seul.",
  },
  {
    target: () => document.querySelector(".season-btn"),
    title: "Revivre la saison",
    text: "Le film de la saison du club, de septembre à août, mois par mois.",
  },
];

function visible(el: Element | null): el is HTMLElement {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 &&
    r.left < window.innerWidth && r.top < window.innerHeight;
}

function markSeen() {
  try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* stockage indisponible */ }
}

function seen() {
  try { return localStorage.getItem(SEEN_KEY) === "1"; } catch { return true; }
}

export type Tour = { start: () => void; offer: () => void };

// `prepare` : rend visibles les éléments visés (panneau ouvert, à mi-hauteur
// sur mobile). Renvoie le délai à attendre avant de mesurer (transition).
export function setupTour(prepare: () => number): Tour {
  let overlay: HTMLElement | null = null;
  let spot: HTMLElement | null = null;
  let bubble: HTMLElement | null = null;
  let steps: Step[] = [];
  let index = 0;

  function place() {
    if (!spot || !bubble) return;
    const el = steps[index].target();
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    const m = 6; // marge de l'éclairage autour de l'élément
    Object.assign(spot.style, {
      left: `${r.left - m}px`, top: `${r.top - m}px`,
      width: `${r.width + 2 * m}px`, height: `${r.height + 2 * m}px`,
    });
    // Bulle du côté où il y a le plus de place, sans sortir de l'écran.
    const b = bubble.getBoundingClientRect();
    const gap = 14;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const room = {
      right: vw - r.right, left: r.left, bottom: vh - r.bottom, top: r.top,
    };
    let x: number;
    let y: number;
    if (room.right >= b.width + gap * 2 && !isMobile()) {
      x = r.right + gap;
      y = r.top + r.height / 2 - b.height / 2;
    } else if (room.bottom >= b.height + gap * 2 || room.bottom >= room.top) {
      x = r.left + r.width / 2 - b.width / 2;
      y = r.bottom + gap;
    } else {
      x = r.left + r.width / 2 - b.width / 2;
      y = r.top - gap - b.height;
    }
    x = Math.min(Math.max(12, x), vw - b.width - 12);
    y = Math.min(Math.max(12, y), vh - b.height - 12);
    bubble.style.left = `${x}px`;
    bubble.style.top = `${y}px`;
  }

  function show(i: number) {
    if (!bubble) return;
    index = i;
    const s = steps[i];
    s.target()?.scrollIntoView({ block: "nearest" });
    bubble.querySelector(".tour-count")!.textContent = `${i + 1} / ${steps.length}`;
    bubble.querySelector(".tour-title")!.textContent = s.title;
    bubble.querySelector(".tour-text")!.textContent = s.text;
    (bubble.querySelector(".tour-prev") as HTMLButtonElement).hidden = i === 0;
    const next = bubble.querySelector(".tour-next") as HTMLButtonElement;
    next.textContent = i === steps.length - 1 ? "Terminer" : "Suivant →";
    requestAnimationFrame(place);
    next.focus({ preventScroll: true });
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") end();
    else if (e.key === "ArrowRight") go(1);
    else if (e.key === "ArrowLeft") go(-1);
  }

  function go(delta: number) {
    const i = index + delta;
    if (i < 0) return;
    if (i >= steps.length) end();
    else show(i);
  }

  function end() {
    markSeen();
    overlay?.remove();
    spot?.remove();
    bubble?.remove();
    overlay = spot = bubble = null;
    window.removeEventListener("resize", place);
    document.removeEventListener("keydown", onKey);
  }

  function start() {
    if (bubble) return;
    document.getElementById("tour-offer")?.remove();
    const wait = prepare();
    setTimeout(() => {
      steps = STEPS.filter((s) => visible(s.target()));
      if (!steps.length) return;
      overlay = document.createElement("div");
      overlay.className = "tour-overlay";
      overlay.addEventListener("click", () => go(1));
      spot = document.createElement("div");
      spot.className = "tour-spot";
      bubble = document.createElement("div");
      bubble.className = "tour-bubble";
      bubble.setAttribute("role", "dialog");
      bubble.setAttribute("aria-modal", "true");
      bubble.setAttribute("aria-labelledby", "tour-title");
      bubble.innerHTML = `
        <div class="tour-head"><span class="tour-count"></span>
          <button type="button" class="tour-close" aria-label="Fermer la visite">✕</button></div>
        <div class="tour-title" id="tour-title"></div>
        <p class="tour-text"></p>
        <div class="tour-actions">
          <button type="button" class="tour-prev">← Précédent</button>
          <button type="button" class="tour-next">Suivant →</button>
        </div>`;
      bubble.querySelector(".tour-close")!.addEventListener("click", end);
      bubble.querySelector(".tour-prev")!.addEventListener("click", () => go(-1));
      bubble.querySelector(".tour-next")!.addEventListener("click", () => go(1));
      document.body.append(overlay, spot, bubble);
      window.addEventListener("resize", place);
      document.addEventListener("keydown", onKey);
      show(0);
    }, wait);
  }

  // Proposition discrète après l'intro, une seule fois.
  function offer() {
    if (seen() || bubble || document.getElementById("tour-offer")) return;
    const box = document.createElement("div");
    box.id = "tour-offer";
    box.setAttribute("role", "status");
    box.innerHTML = `<span>Première visite ? <b>Découvrez la carte</b> en quelques étapes.</span>
      <button type="button" class="tour-offer-go">Découvrir</button>
      <button type="button" class="tour-offer-close" aria-label="Non merci">✕</button>`;
    const dismiss = () => { markSeen(); box.remove(); };
    box.querySelector(".tour-offer-go")!.addEventListener("click", start);
    box.querySelector(".tour-offer-close")!.addEventListener("click", dismiss);
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 15000); // sans réponse : on reproposera plus tard
  }

  return { start, offer };
}
