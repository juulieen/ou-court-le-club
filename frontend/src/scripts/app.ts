// RunEvent86 — "Ou court le club ?" — MapLibre GL
declare const maplibregl: any;

(function () {
  "use strict";

  const BASE_URL = (window as any).__BASE_URL__ || "/";

  const MAPTILER_KEY = window.location.hostname === "localhost"
    ? "MAPTILER_KEY_REDACTED"   // dev (localhost only)
    : "MAPTILER_KEY_REDACTED";  // prod (juulieen.github.io)

  let map: any;
  let allRaces: any[] = [];
  let raceGroups: any[] = []; // grouped by event (multi-edition)
  let currentPopup: any = null;

  // --- Init ---
  function init() {
    map = new maplibregl.Map({
      container: "map",
      style: `https://api.maptiler.com/maps/outdoor-v2/style.json?key=${MAPTILER_KEY}`,
      center: [0.34, 46.58],
      zoom: 6,
      maxZoom: 17,
    });

    map.addControl(new maplibregl.NavigationControl(), "top-right");

    map.on("load", () => {
      loadData();
    });

    setupSidebar();
    setupLegalModal();
    setupHashRouting();
  }

  // --- Data loading ---
  function loadData() {
    fetch(`${BASE_URL}data/races.json`, { cache: "no-cache" })
      .then((r: Response) => r.json())
      .then((data: any) => {
        allRaces = (data.races || []).filter((r: any) => r.member_count > 0);
        raceGroups = groupEditions(allRaces);
        updateLastUpdated(data.last_updated);
        updateStats(allRaces);
        populateMemberFilter(allRaces);
        setupMapLayers();
        renderAll();
        // Handle initial deep link
        handleHash();
      })
      .catch((err: Error) => {
        console.error("Erreur chargement donnees:", err);
        document.getElementById("race-list")!.innerHTML =
          '<div class="empty-state"><div class="empty-icon">&#128683;</div>Impossible de charger les donnees</div>';
      });
  }

  // --- Member filter ---
  function populateMemberFilter(races: any[]) {
    const select = document.getElementById("filter-member") as HTMLSelectElement;
    const nameSet = new Set<string>();
    let hasAnonymous = false;
    for (const race of races) {
      const names = race.first_names || [];
      names.forEach((n: string) => nameSet.add(n));
      if (race.member_count > names.length) hasAnonymous = true;
    }
    const sorted = [...nameSet].sort((a, b) => a.localeCompare(b, "fr"));
    // Reset options (keep first default)
    while (select.options.length > 1) select.remove(1);
    for (const name of sorted) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    if (hasAnonymous) {
      const opt = document.createElement("option");
      opt.value = "__anonymous__";
      opt.textContent = "Autres membres";
      select.appendChild(opt);
    }
  }

  // --- Group editions of the same event ---
  function groupEditions(races: any[]) {
    const groups: Record<string, any[]> = {};
    for (const r of races) {
      const base = r.name.replace(/\s*\d{4}\s*$/, "").trim().toLowerCase();
      const locKey = r.lat != null && r.lng != null
        ? `${Math.round(r.lat * 100)},${Math.round(r.lng * 100)}`
        : "nocoords";
      const key = `${base}|${locKey}`;
      if (!groups[key]) groups[key] = [];
      groups[key].push(r);
    }

    const result: any[] = [];
    for (const editions of Object.values(groups)) {
      editions.sort((a: any, b: any) => (b.date || "").localeCompare(a.date || ""));
      result.push({
        latest: editions[0],
        editions: editions,
        isMulti: editions.length > 1,
      });
    }
    return result;
  }

  // --- Stats ---
  function updateStats(races: any[]) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const totalMembers = races.reduce((s: number, r: any) => s + r.member_count, 0);
    const upcoming = races.filter(
      (r: any) => r.date && new Date(r.date + "T00:00:00") >= today
    ).length;
    animateCounter("stat-races", races.length);
    animateCounter("stat-members", totalMembers);
    animateCounter("stat-upcoming", upcoming);
  }

  function animateCounter(id: string, target: number) {
    const el = document.getElementById(id);
    if (!el) return;
    if (target === 0) { el.textContent = "0"; return; }
    let current = 0;
    const step = Math.max(1, Math.floor(target / 20));
    const interval = setInterval(() => {
      current += step;
      if (current >= target) { current = target; clearInterval(interval); }
      el.textContent = String(current);
    }, 30);
  }

  // --- Temporal classification ---
  function getTemporality(dateStr: string) {
    if (!dateStr) return "future";
    const raceDate = new Date(dateStr + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = (raceDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24);
    if (diffDays < 0) return "past";
    if (diffDays <= 7) return "this-week";
    if (diffDays <= 30) return "this-month";
    return "future";
  }

  function getColor(temp: string) {
    switch (temp) {
      case "past": return "#aaaaaa";
      case "this-week": return "#E53935";
      case "this-month": return "#F57C20";
      default: return "#6B2D5B";
    }
  }

  // --- Countdown badge ---
  function getCountdownBadge(dateStr: string): string {
    if (!dateStr) return "";
    const raceDate = new Date(dateStr + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = Math.round((raceDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
    if (diffDays < 0) return "";
    if (diffDays === 0) return '<span class="countdown-badge countdown-today">Aujourd\'hui</span>';
    if (diffDays === 1) return '<span class="countdown-badge countdown-tomorrow">Demain</span>';
    if (diffDays <= 30) return `<span class="countdown-badge countdown-soon">J-${diffDays}</span>`;
    return "";
  }

  // --- Truncate first names (3 + "+N") ---
  function formatFirstNames(names: string[], maxShow: number = 3): string {
    if (!names || !names.length) return "";
    if (names.length <= maxShow) return names.join(", ");
    return names.slice(0, maxShow).join(", ") + ` +${names.length - maxShow}`;
  }

  // --- WhatsApp share ---
  function getShareUrl(raceId: string): string {
    // Use the /race/{id}/ page URL which has og:tags for rich preview
    const site = window.location.origin;
    return `${site}${BASE_URL}race/${raceId}/`;
  }

  function buildWhatsAppLink(raceName: string, raceId: string): string {
    const url = getShareUrl(raceId);
    const text = encodeURIComponent(`${raceName}\n${url}`);
    return `https://wa.me/?text=${text}`;
  }

  // --- Map layers ---
  function setupMapLayers() {
    map.addSource("races", {
      type: "geojson",
      data: buildGeoJSON(raceGroups),
      cluster: true,
      clusterMaxZoom: 13,
      clusterRadius: 50,
      clusterProperties: {
        sum_members: ["+", ["get", "member_count"]],
      },
    });

    // Cluster circles
    map.addLayer({
      id: "clusters",
      type: "circle",
      source: "races",
      filter: ["has", "point_count"],
      paint: {
        "circle-color": "#F57C20",
        "circle-opacity": 0.85,
        "circle-radius": ["step", ["get", "point_count"], 18, 3, 24, 6, 30],
        "circle-stroke-width": 2,
        "circle-stroke-color": "#fff",
      },
    });

    // Cluster count label
    map.addLayer({
      id: "cluster-count",
      type: "symbol",
      source: "races",
      filter: ["has", "point_count"],
      layout: {
        "text-field": "{point_count}",
        "text-font": ["Noto Sans Bold"],
        "text-size": 13,
      },
      paint: {
        "text-color": "#fff",
      },
    });

    // Individual race circles
    map.addLayer({
      id: "race-points",
      type: "circle",
      source: "races",
      filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": ["case", ["get", "is_multi"], 13, 10],
        "circle-stroke-width": ["case", ["get", "is_multi"], 3.5, 2.5],
        "circle-stroke-color": ["case",
          ["get", "is_multi"], "#F57C20",
          "#fff"
        ],
      },
    });

    // Member count label
    map.addLayer({
      id: "race-labels",
      type: "symbol",
      source: "races",
      filter: ["!", ["has", "point_count"]],
      layout: {
        "text-field": ["to-string", ["get", "member_count"]],
        "text-font": ["Noto Sans Bold"],
        "text-size": 11,
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#fff",
      },
    });

    // Edition count badge
    map.addLayer({
      id: "edition-badge",
      type: "symbol",
      source: "races",
      filter: ["all", ["!", ["has", "point_count"]], ["get", "is_multi"]],
      layout: {
        "text-field": ["concat", "\u00D7", ["to-string", ["get", "edition_count"]]],
        "text-font": ["Noto Sans Bold"],
        "text-size": 9,
        "text-offset": [1.2, -1.2],
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#F57C20",
        "text-halo-color": "#fff",
        "text-halo-width": 1.5,
      },
    });

    // --- Interactions ---

    // Click cluster -> zoom in
    map.on("click", "clusters", async (e: any) => {
      const features = map.queryRenderedFeatures(e.point, { layers: ["clusters"] });
      if (!features.length) return;
      const clusterId = features[0].properties.cluster_id;
      const zoom = await map.getSource("races").getClusterExpansionZoom(clusterId);
      map.easeTo({ center: features[0].geometry.coordinates, zoom: zoom + 1 });
    });

    // Click individual point -> popup
    map.on("click", "race-points", (e: any) => {
      if (!e.features || !e.features.length) return;
      const coords = e.features[0].geometry.coordinates.slice();
      const p = e.features[0].properties;

      while (Math.abs(e.lngLat.lng - coords[0]) > 180) {
        coords[0] += e.lngLat.lng > coords[0] ? 360 : -360;
      }

      showPopup(coords, p);
    });

    // Cursor
    ["clusters", "race-points"].forEach((layer: string) => {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
    });

    // Fit bounds
    fitBounds(allRaces);
  }

  function showPopup(coords: [number, number], p: any) {
    // Parse editions JSON
    let editions: any[] = [];
    try {
      editions = JSON.parse(p.editions_json);
    } catch (_) {
      editions = [{
        name: p.name, date: p.date, member_count: p.member_count,
        first_names: [], platform: p.platform, url: p.url, color: p.color,
      }];
    }

    let popupHtml: string;
    if (editions.length > 1) {
      // Multi-edition: timeline
      const title = p.name.replace(/\s*\d{4}\s*$/, "").trim();
      const timelineHtml = editions.map((ed: any) => {
        const dateFormatted = ed.date
          ? new Date(ed.date + "T00:00:00").toLocaleDateString("fr-FR", {
              day: "numeric", month: "short", year: "numeric",
            })
          : "?";
        const temp = getTemporality(ed.date);
        const color = getColor(temp);
        const linkHtml = ed.url
          ? `<a class="popup-link" href="${ed.url}" target="_blank" rel="noopener">${ed.platform} &rarr;</a>`
          : "";
        const namesHtml = ed.first_names && ed.first_names.length
          ? `<div class="timeline-names">${formatFirstNames(ed.first_names)}</div>`
          : "";
        return `
          <div class="timeline-item">
            <div class="timeline-dot" style="background: ${color}"></div>
            <div class="timeline-content">
              <div class="timeline-year">${dateFormatted}</div>
              <div class="timeline-members">${ed.member_count} membre${ed.member_count > 1 ? "s" : ""} ${linkHtml}</div>
              ${namesHtml}
            </div>
          </div>`;
      }).join("");

      const whatsappBtn = `<a class="popup-share-btn" href="${buildWhatsAppLink(title, p.id)}" target="_blank" rel="noopener" title="Partager sur WhatsApp">&#128172;</a>`;

      popupHtml = `
        <div class="race-popup">
          <div class="popup-header">
            <div class="popup-title" style="border-left: 3px solid ${p.color}; padding-left: 10px">${title}</div>
            ${whatsappBtn}
          </div>
          <div class="popup-meta">${p.location || ""}</div>
          <div class="popup-edition-badge">${editions.length} editions</div>
          <div class="timeline">${timelineHtml}</div>
        </div>`;
    } else {
      // Single edition
      const ed = editions[0];
      const dateFormatted = ed.date
        ? new Date(ed.date + "T00:00:00").toLocaleDateString("fr-FR", {
            weekday: "long", day: "numeric", month: "long", year: "numeric",
          })
        : "Date inconnue";
      const membersHtml = ed.member_count > 0
        ? `<div class="popup-members-count">${ed.member_count} membre${ed.member_count > 1 ? "s" : ""} inscrit${ed.member_count > 1 ? "s" : ""}</div>`
        : "";
      const namesHtml = ed.first_names && ed.first_names.length
        ? `<div class="popup-names">${formatFirstNames(ed.first_names)}</div>`
        : "";
      const linkHtml = ed.url
        ? `<a class="popup-link" href="${ed.url}" target="_blank" rel="noopener">Voir sur ${ed.platform} &rarr;</a>`
        : "";
      const whatsappBtn = `<a class="popup-share-btn" href="${buildWhatsAppLink(ed.name, p.id)}" target="_blank" rel="noopener" title="Partager sur WhatsApp">&#128172;</a>`;

      popupHtml = `
        <div class="race-popup">
          <div class="popup-header">
            <div class="popup-title" style="border-left: 3px solid ${ed.color || p.color}; padding-left: 10px">${ed.name}</div>
            ${whatsappBtn}
          </div>
          <div class="popup-meta">${dateFormatted}${p.location ? " \u2014 " + p.location : ""}</div>
          ${membersHtml}
          ${namesHtml}
          ${linkHtml}
        </div>`;
    }

    if (currentPopup) currentPopup.remove();
    currentPopup = new maplibregl.Popup({ offset: 12, maxWidth: "300px" })
      .setLngLat(coords)
      .setHTML(popupHtml)
      .addTo(map);

    // Update hash without triggering handleHash
    window.history.replaceState(null, "", `#race/${p.id}`);
  }

  function buildGeoJSON(groups: any[]) {
    return {
      type: "FeatureCollection",
      features: groups
        .filter((g: any) => g.latest.lat != null && g.latest.lng != null)
        .map((g: any) => {
          const r = g.latest;
          const totalMembers = g.editions.reduce((s: number, e: any) => s + e.member_count, 0);
          const futureEd = g.editions.find((e: any) => getTemporality(e.date) !== "past");
          const colorRef = futureEd || r;

          const editionsData = g.editions.map((e: any) => ({
            name: e.name,
            date: e.date || "",
            member_count: e.member_count,
            first_names: e.first_names || [],
            platform: e.platform || "",
            url: e.url || "",
            color: getColor(getTemporality(e.date)),
          }));

          return {
            type: "Feature",
            geometry: { type: "Point", coordinates: [r.lng, r.lat] },
            properties: {
              id: r.id,
              name: r.name,
              date: r.date || "",
              location: r.location || "",
              platform: r.platform || "",
              url: r.url || "",
              member_count: g.isMulti ? totalMembers : r.member_count,
              color: getColor(getTemporality(colorRef.date)),
              temporality: getTemporality(colorRef.date),
              is_multi: g.isMulti,
              edition_count: g.editions.length,
              editions_json: JSON.stringify(editionsData),
            },
          };
        }),
    };
  }

  function fitBounds(races: any[]) {
    const valid = races.filter((r: any) => r.lat != null && r.lng != null);
    if (valid.length === 0) return;
    if (valid.length === 1) {
      map.flyTo({ center: [valid[0].lng, valid[0].lat], zoom: 10 });
      return;
    }
    const bounds = new maplibregl.LngLatBounds();
    valid.forEach((r: any) => bounds.extend([r.lng, r.lat]));
    const isMobile = window.innerWidth <= 768;
    const padding = isMobile
      ? { top: 40, bottom: 40, left: 40, right: 40 }
      : { top: 60, bottom: 60, left: 420, right: 60 };
    map.fitBounds(bounds, { padding, maxZoom: 12 });
  }

  // --- Deep link / hash routing ---
  function setupHashRouting() {
    window.addEventListener("hashchange", handleHash);
  }

  function handleHash() {
    const hash = window.location.hash;
    const match = hash.match(/^#race\/(.+)$/);
    if (!match) return;

    const raceId = match[1];
    // Find the race in allRaces
    const race = allRaces.find((r: any) => r.id === raceId);
    if (!race || race.lat == null || race.lng == null) return;

    // Fly to race and open popup
    map.flyTo({ center: [race.lng, race.lat], zoom: 14, duration: 1000 });

    // Wait for fly animation then simulate a click
    setTimeout(() => {
      // Find the group this race belongs to
      const group = raceGroups.find((g: any) =>
        g.editions.some((e: any) => e.id === raceId)
      );
      if (!group) return;

      const r = group.latest;
      const editionsData = group.editions.map((e: any) => ({
        name: e.name,
        date: e.date || "",
        member_count: e.member_count,
        first_names: e.first_names || [],
        platform: e.platform || "",
        url: e.url || "",
        color: getColor(getTemporality(e.date)),
      }));

      const futureEd = group.editions.find((e: any) => getTemporality(e.date) !== "past");
      const colorRef = futureEd || r;

      showPopup([r.lng, r.lat], {
        id: r.id,
        name: r.name,
        date: r.date || "",
        location: r.location || "",
        platform: r.platform || "",
        url: r.url || "",
        member_count: group.isMulti
          ? group.editions.reduce((s: number, e: any) => s + e.member_count, 0)
          : r.member_count,
        color: getColor(getTemporality(colorRef.date)),
        is_multi: group.isMulti,
        edition_count: group.editions.length,
        editions_json: JSON.stringify(editionsData),
      });

      // Highlight card in sidebar
      const card = document.querySelector(`.race-card[data-id="${raceId}"]`) as HTMLElement;
      if (!card) return;
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("highlighted");
      setTimeout(() => card.classList.remove("highlighted"), 2000);
    }, 1100);
  }

  // --- Rendering ---
  let activeFilter = "all";

  function matchesDistance(distances: number[], range: string) {
    if (!range || !distances || !distances.length) return !range;
    if (range === "42+") return distances.some((d: number) => d >= 42);
    const [minStr, maxStr] = range.split("-");
    const min = parseFloat(minStr);
    const max = parseFloat(maxStr);
    return distances.some((d: number) => d >= min && d <= max);
  }

  function renderAll() {
    const dateFrom = (document.getElementById("date-from") as HTMLInputElement).value;
    const dateTo = (document.getElementById("date-to") as HTMLInputElement).value;
    const filterType = (document.getElementById("filter-type") as HTMLSelectElement).value;
    const filterDist = (document.getElementById("filter-distance") as HTMLSelectElement).value;
    const filterMember = (document.getElementById("filter-member") as HTMLSelectElement).value;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayStr = today.toISOString().slice(0, 10);

    let filtered = allRaces.filter((r: any) => {
      if (activeFilter === "upcoming" && r.date && r.date < todayStr) return false;
      if (activeFilter === "past" && r.date && r.date >= todayStr) return false;
      if (dateFrom && r.date < dateFrom) return false;
      if (dateTo && r.date > dateTo) return false;
      if (filterType && r.race_type !== filterType) return false;
      if (filterDist && !matchesDistance(r.distances, filterDist)) return false;
      if (filterMember) {
        const names = r.first_names || [];
        if (filterMember === "__anonymous__") {
          if (r.member_count <= names.length) return false;
        } else {
          if (!names.includes(filterMember)) return false;
        }
      }
      return true;
    });

    const filteredGroups = groupEditions(filtered);
    updateStats(filtered);

    const source = map.getSource("races");
    if (source) {
      source.setData(buildGeoJSON(filteredGroups));
    }

    renderList(filteredGroups);
  }

  function renderList(groups: any[]) {
    const list = document.getElementById("race-list")!;
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const sorted = [...groups].sort((a: any, b: any) => {
      const aFutureEd = a.editions.find((e: any) => e.date && new Date(e.date + "T00:00:00") >= today);
      const bFutureEd = b.editions.find((e: any) => e.date && new Date(e.date + "T00:00:00") >= today);
      if (aFutureEd && !bFutureEd) return -1;
      if (!aFutureEd && bFutureEd) return 1;
      const aDate = aFutureEd ? new Date(aFutureEd.date + "T00:00:00") : new Date(a.latest.date + "T00:00:00");
      const bDate = bFutureEd ? new Date(bFutureEd.date + "T00:00:00") : new Date(b.latest.date + "T00:00:00");
      if (aFutureEd && bFutureEd) return aDate.getTime() - bDate.getTime();
      return bDate.getTime() - aDate.getTime();
    });

    if (sorted.length === 0) {
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">&#128270;</div>Aucune course trouvee</div>';
      return;
    }

    list.innerHTML = sorted
      .map((group: any) => {
        const r = group.latest;
        const futureEd = group.editions.find((e: any) => getTemporality(e.date) !== "past");
        const displayEd = futureEd || r;
        const temp = getTemporality(displayEd.date);

        const dateFormatted = displayEd.date
          ? new Date(displayEd.date + "T00:00:00").toLocaleDateString("fr-FR", {
              day: "numeric", month: "short", year: "numeric",
            })
          : "?";

        const countdown = getCountdownBadge(displayEd.date);

        const displayName = group.isMulti
          ? r.name.replace(/\s*\d{4}\s*$/, "").trim()
          : r.name;

        const editionBadge = group.isMulti
          ? `<span class="edition-badge">${group.editions.length} ed.</span>`
          : "";

        const memberLabel = group.isMulti
          ? `${displayEd.member_count} membre${displayEd.member_count > 1 ? "s" : ""}`
          : `${r.member_count} membre${r.member_count > 1 ? "s" : ""}`;

        const firstNames = (group.isMulti ? displayEd.first_names : r.first_names) || [];
        const namesLine = firstNames.length
          ? `<div class="race-names">${formatFirstNames(firstNames)}</div>`
          : "";

        const typeBadge = r.race_type && r.race_type !== "autre"
          ? `<span class="type-badge type-${r.race_type}">${r.race_type}</span>`
          : "";
        const distLabel = r.distances && r.distances.length
          ? r.distances.map((d: number) => `${d}km`).join(", ")
          : "";

        const whatsappLink = buildWhatsAppLink(displayName, r.id);

        return `
        <div class="race-card" data-id="${r.id}" data-temp="${temp}" data-lng="${r.lng}" data-lat="${r.lat}">
          <div class="race-name">
            ${displayName} ${editionBadge} ${typeBadge} ${countdown}
            <a class="card-share-btn" href="${whatsappLink}" target="_blank" rel="noopener" title="Partager" onclick="event.stopPropagation()">&#128279;</a>
          </div>
          <div class="race-meta">
            <span class="date">${dateFormatted}</span>
            <span class="location">${displayEd.location || r.location || ""}</span>
            ${distLabel ? `<span class="dist">${distLabel}</span>` : ""}
            <span class="member-badge">${memberLabel}</span>
          </div>
          ${namesLine}
        </div>`;
      })
      .join("");
  }

  // --- Sidebar ---
  function setupSidebar() {
    const sidebar = document.getElementById("sidebar")!;
    const toggle = document.getElementById("sidebar-toggle")!;
    const header = document.getElementById("sidebar-header")!;
    const iconClose = document.getElementById("toggle-close") as HTMLElement;
    const iconOpen = document.getElementById("toggle-open") as HTMLElement;

    toggle.addEventListener("click", () => {
      sidebar.classList.toggle("collapsed");
      const collapsed = sidebar.classList.contains("collapsed");
      iconClose.style.display = collapsed ? "none" : "block";
      iconOpen.style.display = collapsed ? "block" : "none";
      setTimeout(() => map.resize(), 350);
    });

    header.addEventListener("click", () => {
      if (window.innerWidth <= 768) {
        sidebar.classList.toggle("collapsed");
        setTimeout(() => map.resize(), 350);
      }
    });

    // Quick filter buttons
    document.querySelectorAll(".filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeFilter = (btn as HTMLElement).dataset.filter || "all";
        renderAll();
      });
    });

    // Date/type/distance/member filters (debounced)
    let filterTimeout: ReturnType<typeof setTimeout>;
    function debouncedRender() {
      clearTimeout(filterTimeout);
      filterTimeout = setTimeout(renderAll, 150);
    }
    document.getElementById("date-from")!.addEventListener("change", debouncedRender);
    document.getElementById("date-to")!.addEventListener("change", debouncedRender);
    document.getElementById("filter-type")!.addEventListener("change", debouncedRender);
    document.getElementById("filter-distance")!.addEventListener("change", debouncedRender);
    document.getElementById("filter-member")!.addEventListener("change", debouncedRender);

    // Event delegation for race cards
    document.getElementById("race-list")!.addEventListener("click", (e: Event) => {
      const card = (e.target as HTMLElement).closest(".race-card") as HTMLElement | null;
      if (!card) return;
      const lng = parseFloat(card.dataset.lng || "");
      const lat = parseFloat(card.dataset.lat || "");
      const raceId = card.dataset.id || "";
      if (!isNaN(lng) && !isNaN(lat)) {
        map.flyTo({ center: [lng, lat], zoom: 13, duration: 800 });
        // Update hash for deep linking
        if (raceId) window.history.replaceState(null, "", `#race/${raceId}`);
      }
    });
  }

  function updateLastUpdated(ts: string) {
    if (!ts) return;
    const el = document.getElementById("last-updated");
    if (el) {
      const d = new Date(ts);
      el.textContent =
        "Maj " +
        d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }) +
        " " +
        d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    }
  }

  // --- Legal modal ---
  function setupLegalModal() {
    const link = document.getElementById("legal-link")!;
    const modal = document.getElementById("legal-modal")!;
    const close = modal.querySelector(".modal-close")!;

    link.addEventListener("click", (e: Event) => {
      e.preventDefault();
      modal.classList.remove("hidden");
    });
    close.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", (e: Event) => {
      if (e.target === modal) modal.classList.add("hidden");
    });
    document.addEventListener("keydown", (e: KeyboardEvent) => {
      if (e.key === "Escape") modal.classList.add("hidden");
    });
  }

  // --- Start ---
  document.addEventListener("DOMContentLoaded", init);
})();
