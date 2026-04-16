// RunEvent86 — "Ou court le club ?" — MapLibre GL
declare const maplibregl: any;

(function () {
  "use strict";

  const BASE_URL = (window as any).__BASE_URL__ || "/";

  const MAPTILER_KEY = import.meta.env.PUBLIC_MAPTILER_KEY || "";

  const WHATSAPP_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>';

  const SITE_URL = window.location.origin + BASE_URL;

  let map: any;
  let allRaces: any[] = [];
  let raceGroups: any[] = []; // grouped by event (multi-edition)
  let currentPopup: any = null;
  let detailMode = false;
  const counterIntervals: Record<string, ReturnType<typeof setInterval>> = {};

  // --- HTML escaping ---
  function escapeHtml(str: string): string {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function truncateNames(names: string[], max: number): string {
    if (!names || !names.length) return "";
    if (names.length <= max) return names.join(", ");
    return names.slice(0, max).join(", ") + ` +${names.length - max}`;
  }

  function buildWhatsAppUrl(name: string, dateStr: string, memberCount: number, raceId: string): string {
    const dateFormatted = dateStr
      ? new Date(dateStr + "T00:00:00").toLocaleDateString("fr-FR", {
          day: "numeric", month: "short", year: "numeric",
        })
      : "";
    const link = raceId ? `${SITE_URL}#race/${encodeURIComponent(raceId)}` : SITE_URL;
    const text = `\u{1F3C3} ${name}${dateFormatted ? " \u2014 " + dateFormatted : ""} \u2014 ${memberCount} membre${memberCount > 1 ? "s" : ""} du club inscrit${memberCount > 1 ? "s" : ""} !\n${link}`;
    return `https://wa.me/?text=${encodeURIComponent(text)}`;
  }

  function getCountdownLabel(dateStr: string): string | null {
    if (!dateStr) return null;
    const raceDate = new Date(dateStr + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = Math.round((raceDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
    if (diffDays < 0 || diffDays > 7) return null;
    if (diffDays === 0) return "Aujourd'hui";
    if (diffDays === 1) return "Demain";
    return `J-${diffDays}`;
  }

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
    window.addEventListener("hashchange", handleRoute);
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
        handleRoute();
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
      // Strip trailing year to get base name
      const base = r.name.replace(/\s*\d{4}\s*$/, "").trim().toLowerCase();
      // Group by base name + approximate location
      const locKey = r.lat != null && r.lng != null
        ? `${Math.round(r.lat * 100)},${Math.round(r.lng * 100)}`
        : "nocoords";
      const key = `${base}|${locKey}`;
      if (!groups[key]) groups[key] = [];
      groups[key].push(r);
    }

    // Sort editions by date (newest first) within each group
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
    const thisMonthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    const upcoming = races.filter(
      (r: any) => r.date && new Date(r.date + "T00:00:00") >= today
    ).length;
    const thisMonth = races.filter(
      (r: any) => r.date && new Date(r.date + "T00:00:00") >= today && new Date(r.date + "T00:00:00") <= thisMonthEnd
    ).length;
    // Unique runners: count distinct first_names + anonymous members
    const nameSet = new Set<string>();
    let anonymousCount = 0;
    for (const r of races) {
      if (!r.date || new Date(r.date + "T00:00:00") < today) continue;
      (r.first_names || []).forEach((n: string) => nameSet.add(n));
      const anon = r.member_count - (r.first_names || []).length;
      if (anon > 0) anonymousCount = Math.max(anonymousCount, anon);
    }
    const uniqueRunners = nameSet.size + anonymousCount;
    animateCounter("stat-upcoming", upcoming);
    animateCounter("stat-this-month", thisMonth);
    animateCounter("stat-runners", uniqueRunners);
  }

  function animateCounter(id: string, target: number) {
    const el = document.getElementById(id);
    if (!el) return;
    if (counterIntervals[id]) { clearInterval(counterIntervals[id]); delete counterIntervals[id]; }
    if (target === 0) { el.textContent = "0"; return; }
    let current = 0;
    const step = Math.max(1, Math.floor(target / 20));
    counterIntervals[id] = setInterval(() => {
      current += step;
      if (current >= target) {
        current = target;
        clearInterval(counterIntervals[id]);
        delete counterIntervals[id];
      }
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

  // --- Map layers ---
  function setupMapLayers() {
    map.addSource("races", {
      type: "geojson",
      data: buildGeoJSON(raceGroups),
      cluster: true,
      clusterMaxZoom: 8,
      clusterRadius: 18,
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

    // Individual race circles -- larger for multi-edition
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

    // Member count label on individual points
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

    // Edition count badge (small circle at top-right of multi-edition markers)
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

    // Click individual point -> popup with all editions
    map.on("click", "race-points", (e: any) => {
      if (!e.features || !e.features.length) return;
      const coords = e.features[0].geometry.coordinates.slice();
      const p = e.features[0].properties;

      while (Math.abs(e.lngLat.lng - coords[0]) > 180) {
        coords[0] += e.lngLat.lng > coords[0] ? 360 : -360;
      }

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
        // Multi-edition: show timeline
        const title = escapeHtml(p.name.replace(/\s*\d{4}\s*$/, "").trim());
        const timelineHtml = editions.map((ed: any) => {
          const dateFormatted = ed.date
            ? new Date(ed.date + "T00:00:00").toLocaleDateString("fr-FR", {
                day: "numeric", month: "short", year: "numeric",
              })
            : "?";
          const temp = getTemporality(ed.date);
          const color = getColor(temp);
          const linkHtml = ed.url
            ? `<a class="popup-link" href="${escapeHtml(ed.url)}" target="_blank" rel="noopener">${escapeHtml(ed.platform)} &rarr;</a>`
            : "";
          const namesHtml = ed.first_names && ed.first_names.length
            ? `<div class="timeline-names">${escapeHtml(truncateNames(ed.first_names, 3))}</div>`
            : "";
          return `
            <div class="timeline-item">
              <div class="timeline-dot" style="background: ${escapeHtml(color)}"></div>
              <div class="timeline-content">
                <div class="timeline-year">${escapeHtml(dateFormatted)}</div>
                <div class="timeline-members">${ed.member_count} membre${ed.member_count > 1 ? "s" : ""} ${linkHtml}</div>
                ${namesHtml}
              </div>
            </div>`;
        }).join("");

        popupHtml = `
          <div class="race-popup">
            <div class="popup-title" style="border-left: 3px solid ${escapeHtml(p.color)}; padding-left: 10px">${title}</div>
            <div class="popup-meta">${escapeHtml(p.location || "")}</div>
            <div class="popup-edition-badge">${editions.length} editions</div>
            <div class="timeline">${timelineHtml}</div>
            <a class="share-btn whatsapp-btn popup-whatsapp" href="${escapeHtml(buildWhatsAppUrl(p.name.replace(/\s*\d{4}\s*$/, "").trim(), editions[0].date, editions[0].member_count, editions[0].id || p.id))}" target="_blank" rel="noopener">${WHATSAPP_SVG} Partager</a>
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
          ? `<div class="popup-names">${escapeHtml(truncateNames(ed.first_names, 3))}</div>`
          : "";
        const linkHtml = ed.url
          ? `<a class="popup-link" href="${escapeHtml(ed.url)}" target="_blank" rel="noopener">Voir sur ${escapeHtml(ed.platform)} &rarr;</a>`
          : "";

        popupHtml = `
          <div class="race-popup">
            <div class="popup-title" style="border-left: 3px solid ${escapeHtml(ed.color || p.color)}; padding-left: 10px">${escapeHtml(ed.name)}</div>
            <div class="popup-meta">${escapeHtml(dateFormatted)}${p.location ? " \u2014 " + escapeHtml(p.location) : ""}</div>
            ${membersHtml}
            ${namesHtml}
            ${linkHtml}
            <a class="share-btn whatsapp-btn popup-whatsapp" href="${escapeHtml(buildWhatsAppUrl(ed.name, ed.date, ed.member_count, ed.id || p.id))}" target="_blank" rel="noopener">${WHATSAPP_SVG} Partager</a>
          </div>`;
      }

      if (currentPopup) currentPopup.remove();
      currentPopup = new maplibregl.Popup({ offset: 12, maxWidth: "300px" })
        .setLngLat(coords)
        .setHTML(popupHtml)
        .addTo(map);
    });

    // Cursor
    ["clusters", "race-points"].forEach((layer: string) => {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
    });

    // Fit bounds to all races
    fitBounds(allRaces);
  }

  function buildGeoJSON(groups: any[]) {
    return {
      type: "FeatureCollection",
      features: groups
        .filter((g: any) => g.latest.lat != null && g.latest.lng != null)
        .map((g: any) => {
          const r = g.latest;
          const totalMembers = g.editions.reduce((s: number, e: any) => s + e.member_count, 0);
          // For color: use latest future edition, or latest overall
          const futureEd = g.editions.find((e: any) => getTemporality(e.date) !== "past");
          const colorRef = futureEd || r;

          // Build compact editions data for popup
          const editionsData = g.editions.map((e: any) => ({
            id: e.id || "",
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
    // Adapt padding for mobile (sidebar at bottom) vs desktop (sidebar on left)
    const isMobile = window.innerWidth <= 768;
    const padding = isMobile
      ? { top: 40, bottom: 40, left: 40, right: 40 }
      : { top: 60, bottom: 60, left: 420, right: 60 };
    map.fitBounds(bounds, { padding, maxZoom: 12 });
  }

  // --- Rendering ---
  let activeFilter = "upcoming"; // "upcoming", "recent", "all"

  function matchesDistance(distances: number[], range: string) {
    if (!range || !distances || !distances.length) return !range;
    const [minStr, maxStr] = range.split("-");
    if (range === "42+") {
      return distances.some((d: number) => d >= 42);
    }
    const min = parseFloat(minStr);
    const max = parseFloat(maxStr);
    return distances.some((d: number) => d >= min && d <= max);
  }

  function renderAll() {
    if (detailMode) return;
    const dateFrom = (document.getElementById("date-from") as HTMLInputElement).value;
    const dateTo = (document.getElementById("date-to") as HTMLInputElement).value;
    const filterType = (document.getElementById("filter-type") as HTMLSelectElement).value;
    const filterDist = (document.getElementById("filter-distance") as HTMLSelectElement).value;
    const filterMember = (document.getElementById("filter-member") as HTMLSelectElement).value;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayStr = today.toISOString().slice(0, 10);

    // "Recentes" = 3 months ago to today
    const threeMonthsAgo = new Date(today);
    threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
    const threeMonthsAgoStr = threeMonthsAgo.toISOString().slice(0, 10);

    let filtered = allRaces.filter((r: any) => {
      // Quick filter buttons
      if (activeFilter === "upcoming" && r.date && r.date < todayStr) return false;
      if (activeFilter === "recent" && r.date && (r.date >= todayStr || r.date < threeMonthsAgoStr)) return false;
      // Date range filters
      if (dateFrom && r.date < dateFrom) return false;
      if (dateTo && r.date > dateTo) return false;
      // Type filter
      if (filterType && r.race_type !== filterType) return false;
      // Distance filter
      if (filterDist && !matchesDistance(r.distances, filterDist)) return false;
      // Member filter
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

    // Update stats to reflect current filter
    updateStats(filtered);

    // Update map source
    const source = map.getSource("races");
    if (source) {
      source.setData(buildGeoJSON(filteredGroups));
    }

    renderList(filteredGroups);
    updateFilterBadge();
  }

  function updateFilterBadge() {
    const badge = document.getElementById("filter-badge");
    if (!badge) return;
    let count = 0;
    if (activeFilter !== "all") count++;
    if ((document.getElementById("filter-type") as HTMLSelectElement).value) count++;
    if ((document.getElementById("filter-distance") as HTMLSelectElement).value) count++;
    if ((document.getElementById("filter-member") as HTMLSelectElement).value) count++;
    if ((document.getElementById("date-from") as HTMLInputElement).value) count++;
    if ((document.getElementById("date-to") as HTMLInputElement).value) count++;
    badge.textContent = String(count);
    badge.style.display = count > 0 ? "inline-block" : "none";
  }

  function renderList(groups: any[]) {
    const list = document.getElementById("race-list")!;
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Sort: groups with upcoming editions first, then by nearest date
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
        // Use future edition for temporality if available
        const futureEd = group.editions.find((e: any) => getTemporality(e.date) !== "past");
        const displayEd = futureEd || r;
        const temp = getTemporality(displayEd.date);

        const dateFormatted = displayEd.date
          ? new Date(displayEd.date + "T00:00:00").toLocaleDateString("fr-FR", {
              day: "numeric", month: "short", year: "numeric",
            })
          : "?";

        const totalMembers = group.editions.reduce((s: number, e: any) => s + e.member_count, 0);
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
          ? `<div class="race-names">${escapeHtml(truncateNames(firstNames, 3))}</div>`
          : "";

        const countdownLabel = getCountdownLabel(displayEd.date);
        const countdownBadge = countdownLabel
          ? `<span class="countdown-badge">${escapeHtml(countdownLabel)}</span>`
          : "";

        const VALID_RACE_TYPES = ["trail", "route", "autre"] as const;
        const safeRaceType = VALID_RACE_TYPES.includes(r.race_type) ? r.race_type : "";
        const typeBadge = safeRaceType && safeRaceType !== "autre"
          ? `<span class="type-badge type-${safeRaceType}">${escapeHtml(safeRaceType)}</span>`
          : "";
        const distLabel = r.distances && r.distances.length
          ? r.distances.map((d: number) => `${d}km`).join(", ")
          : "";

        const shareUrl = buildWhatsAppUrl(displayName, displayEd.date, group.isMulti ? displayEd.member_count : r.member_count, displayEd.id || r.id);

        return `
        <div class="race-card" data-id="${escapeHtml(r.id)}" data-temp="${escapeHtml(temp)}" data-lng="${escapeHtml(String(r.lng))}" data-lat="${escapeHtml(String(r.lat))}">
          <div class="race-name">${escapeHtml(displayName)} ${editionBadge} ${typeBadge}</div>
          <div class="race-meta">
            <span class="date">${escapeHtml(dateFormatted)}</span>
            ${countdownBadge}
            <span class="location">${escapeHtml(displayEd.location || r.location || "")}</span>
            ${distLabel ? `<span class="dist">${escapeHtml(distLabel)}</span>` : ""}
            <span class="member-badge">${memberLabel}</span>
          </div>
          ${namesLine}
          <div class="race-actions">
            <a class="share-btn whatsapp-btn" href="${escapeHtml(shareUrl)}" target="_blank" rel="noopener" title="Partager sur WhatsApp" onclick="event.stopPropagation()">${WHATSAPP_SVG}</a>
          </div>
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

    // --- Mobile bottom sheet drag ---
    if (window.innerWidth <= 768) {
      // Snap positions: translateY in px
      // peek = only header, half = ~55% visible, full = nearly all
      function getSnapPositions() {
        const h = sidebar.offsetHeight;
        const vh = window.innerHeight;
        return {
          full: 0,
          half: Math.max(0, h - vh * 0.55),
          peek: h - 70,
        };
      }
      // Also expose for fitBounds padding
      (window as any).__sidebarSnap = getSnapPositions;

      let startY = 0;
      let startTranslate = 0;
      let currentTranslate = 0;
      let isDragging = false;
      let dragStartTime = 0;

      function getTranslateY(): number {
        const matrix = new DOMMatrixReadOnly(getComputedStyle(sidebar).transform);
        return matrix.m42;
      }

      function snapTo(position: number) {
        sidebar.classList.remove("dragging", "collapsed");
        sidebar.style.transform = `translateY(${position}px)`;
        currentTranslate = position;
        setTimeout(() => map.resize(), 350);
      }

      header.addEventListener("touchstart", (e: TouchEvent) => {
        isDragging = true;
        dragStartTime = Date.now();
        startY = e.touches[0].clientY;
        startTranslate = getTranslateY();
        sidebar.classList.add("dragging");
        sidebar.classList.remove("collapsed");
      }, { passive: true });

      header.addEventListener("touchmove", (e: TouchEvent) => {
        if (!isDragging) return;
        const snaps = getSnapPositions();
        const deltaY = e.touches[0].clientY - startY;
        let newTranslate = startTranslate + deltaY;
        newTranslate = Math.max(snaps.full, Math.min(newTranslate, snaps.peek));
        sidebar.style.transform = `translateY(${newTranslate}px)`;
        currentTranslate = newTranslate;
      }, { passive: true });

      header.addEventListener("touchend", () => {
        if (!isDragging) return;
        isDragging = false;
        sidebar.classList.remove("dragging");

        const snaps = getSnapPositions();
        const elapsed = Date.now() - dragStartTime;
        const velocity = (currentTranslate - startTranslate) / Math.max(elapsed, 1);

        // Fast swipe
        if (Math.abs(velocity) > 0.4) {
          if (velocity > 0) {
            snapTo(currentTranslate > snaps.half ? snaps.peek : snaps.half);
          } else {
            snapTo(currentTranslate < snaps.half ? snaps.full : snaps.half);
          }
        } else {
          // Snap to nearest
          const positions = [snaps.full, snaps.half, snaps.peek];
          positions.sort((a, b) => Math.abs(currentTranslate - a) - Math.abs(currentTranslate - b));
          snapTo(positions[0]);
        }
      }, { passive: true });

      // Initialize at half
      setTimeout(() => snapTo(getSnapPositions().half), 100);
    }

    // Quick filter buttons
    document.querySelectorAll(".filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeFilter = (btn as HTMLElement).dataset.filter || "upcoming";
        renderAll();
      });
    });

    // Date filters (debounced)
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

    // Mobile: filter panel toggle
    const filterToggle = document.getElementById("filter-toggle");
    const filtersPanel = document.getElementById("filters");
    if (filterToggle && filtersPanel) {
      filterToggle.addEventListener("click", () => {
        filtersPanel.classList.toggle("filters-open");
        filterToggle.classList.toggle("active");
      });
    }

    // Mobile: date filter toggle
    const dateToggle = document.getElementById("date-toggle");
    const datesPanel = document.getElementById("filter-dates-panel");
    if (dateToggle && datesPanel) {
      dateToggle.addEventListener("click", () => {
        datesPanel.classList.toggle("dates-open");
        dateToggle.classList.toggle("active");
        dateToggle.textContent = datesPanel.classList.contains("dates-open") ? "- Dates" : "+ Dates";
      });
    }

    // Event delegation for race cards -> open detail view
    document.getElementById("race-list")!.addEventListener("click", (e: Event) => {
      const card = (e.target as HTMLElement).closest(".race-card") as HTMLElement | null;
      if (!card) return;
      const raceId = card.dataset.id;
      if (raceId) {
        window.location.hash = `race/${raceId}`;
      }
    });
  }

  // --- Routing ---
  function handleRoute() {
    const hash = window.location.hash;
    const match = hash.match(/^#race\/(.+)$/);
    if (match && allRaces.length > 0) {
      const raceId = decodeURIComponent(match[1]);
      showRaceDetail(raceId);
    } else if (allRaces.length > 0 && detailMode) {
      showDefaultView();
    }
  }

  function showRaceDetail(raceId: string) {
    const race = allRaces.find((r: any) => r.id === raceId);
    if (!race) {
      history.replaceState(null, "", window.location.pathname);
      if (detailMode) showDefaultView();
      return;
    }

    detailMode = true;

    const dateFormatted = race.date
      ? new Date(race.date + "T00:00:00").toLocaleDateString("fr-FR", {
          weekday: "long", day: "numeric", month: "long", year: "numeric",
        })
      : "Date inconnue";

    const temp = getTemporality(race.date);
    const countdownLabel = getCountdownLabel(race.date);
    const countdownBadge = countdownLabel
      ? `<span class="countdown-badge">${escapeHtml(countdownLabel)}</span>`
      : "";

    const typeBadge = race.race_type && race.race_type !== "autre"
      ? `<span class="type-badge type-${escapeHtml(race.race_type)}">${escapeHtml(race.race_type)}</span>`
      : "";

    const distLabel = race.distances && race.distances.length
      ? race.distances.map((d: number) => `${d} km`).join(", ")
      : "";

    const namesHtml = race.first_names && race.first_names.length
      ? `<div class="detail-names">${escapeHtml(race.first_names.join(", "))}</div>`
      : "";

    const linkHtml = race.url
      ? `<a class="detail-link" href="${escapeHtml(race.url)}" target="_blank" rel="noopener">S'inscrire sur ${escapeHtml(race.platform)} &rarr;</a>`
      : "";

    const shareUrl = buildWhatsAppUrl(race.name, race.date, race.member_count, raceId);

    const html = `
      <div class="race-detail" data-temp="${escapeHtml(temp)}">
        <button class="detail-back" id="detail-back">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>
          Retour
        </button>
        <h2 class="detail-title">${escapeHtml(race.name)}</h2>
        <div class="detail-badges">${typeBadge} ${countdownBadge}</div>
        <div class="detail-meta">
          <div class="detail-date">${escapeHtml(dateFormatted)}</div>
          <div class="detail-location">${escapeHtml(race.location || "")}</div>
          ${distLabel ? `<div class="detail-distances">${escapeHtml(distLabel)}</div>` : ""}
        </div>
        <div class="detail-members">
          <span class="member-badge">${race.member_count} membre${race.member_count > 1 ? "s" : ""} inscrit${race.member_count > 1 ? "s" : ""}</span>
        </div>
        ${namesHtml}
        ${linkHtml}
        <div class="detail-actions">
          <a class="share-btn whatsapp-btn" href="${escapeHtml(shareUrl)}" target="_blank" rel="noopener">${WHATSAPP_SVG} Partager sur WhatsApp</a>
        </div>
      </div>`;

    document.getElementById("race-list")!.innerHTML = html;

    document.getElementById("detail-back")!.addEventListener("click", () => {
      history.pushState(null, "", window.location.pathname);
      showDefaultView();
    });

    // Hide filters in detail mode
    const filters = document.getElementById("filters");
    const filterToggle = document.getElementById("filter-toggle");
    if (filters) {
      filters.classList.remove("filters-open");
      filters.style.display = "none";
    }
    if (filterToggle) filterToggle.style.display = "none";

    // Zoom map to race
    if (race.lat != null && race.lng != null) {
      map.flyTo({ center: [race.lng, race.lat], zoom: 13, duration: 800 });
    }

    // Mobile: snap to half
    if (window.innerWidth <= 768) {
      const sidebar = document.getElementById("sidebar")!;
      sidebar.classList.remove("collapsed");
      if ((window as any).__sidebarSnap) {
        const snaps = (window as any).__sidebarSnap();
        sidebar.style.transform = `translateY(${snaps.half}px)`;
      }
    }
  }

  function showDefaultView() {
    if (!detailMode) return;
    detailMode = false;

    // Restore filters
    const filters = document.getElementById("filters");
    const filterToggle = document.getElementById("filter-toggle");
    if (filters) filters.style.display = "";
    if (filterToggle) filterToggle.style.display = "";

    renderAll();
    fitBounds(allRaces);
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
