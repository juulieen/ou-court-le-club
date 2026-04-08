// RunEvent86 — "Ou court le club ?" — MapLibre GL
(function () {
  "use strict";

  const MAPTILER_KEY = "MAPTILER_KEY_REDACTED";
  const MAPTILER_STYLE = "MAPTILER_STYLE_REDACTED";

  let map;
  let allRaces = [];
  let raceGroups = []; // grouped by event (multi-edition)
  let currentPopup = null;

  function truncateNames(names, max) {
    if (!names || !names.length) return "";
    if (names.length <= max) return names.join(", ");
    return names.slice(0, max).join(", ") + ` +${names.length - max}`;
  }

  // --- Init ---
  function init() {
    map = new maplibregl.Map({
      container: "map",
      style: `https://api.maptiler.com/maps/${MAPTILER_STYLE}/style.json?key=${MAPTILER_KEY}`,
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
  }

  // --- Data loading ---
  function loadData() {
    fetch("data/races.json", { cache: "no-cache" })
      .then((r) => r.json())
      .then((data) => {
        allRaces = (data.races || []).filter((r) => r.member_count > 0);
        raceGroups = groupEditions(allRaces);
        updateLastUpdated(data.last_updated);
        updateStats(allRaces);
        populateMemberFilter(allRaces);
        setupMapLayers();
        renderAll();
      })
      .catch((err) => {
        console.error("Erreur chargement donnees:", err);
        document.getElementById("race-list").innerHTML =
          '<div class="empty-state"><div class="empty-icon">&#128683;</div>Impossible de charger les donnees</div>';
      });
  }

  // --- Member filter ---
  function populateMemberFilter(races) {
    const select = document.getElementById("filter-member");
    const nameSet = new Set();
    let hasAnonymous = false;
    for (const race of races) {
      const names = race.first_names || [];
      names.forEach((n) => nameSet.add(n));
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
  function groupEditions(races) {
    const groups = {};
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
    const result = [];
    for (const editions of Object.values(groups)) {
      editions.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
      result.push({
        latest: editions[0],
        editions: editions,
        isMulti: editions.length > 1,
      });
    }
    return result;
  }

  // --- Stats ---
  function updateStats(races) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const thisMonthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    const upcoming = races.filter(
      (r) => r.date && new Date(r.date + "T00:00:00") >= today
    ).length;
    const thisMonth = races.filter(
      (r) => r.date && new Date(r.date + "T00:00:00") >= today && new Date(r.date + "T00:00:00") <= thisMonthEnd
    ).length;
    // Unique runners: count distinct first_names + anonymous members
    const nameSet = new Set();
    let anonymousCount = 0;
    for (const r of races) {
      if (!r.date || new Date(r.date + "T00:00:00") < today) continue;
      (r.first_names || []).forEach((n) => nameSet.add(n));
      const anon = r.member_count - (r.first_names || []).length;
      if (anon > 0) anonymousCount = Math.max(anonymousCount, anon);
    }
    const uniqueRunners = nameSet.size + anonymousCount;
    animateCounter("stat-upcoming", upcoming);
    animateCounter("stat-this-month", thisMonth);
    animateCounter("stat-runners", uniqueRunners);
  }

  function animateCounter(id, target) {
    const el = document.getElementById(id);
    if (!el) return;
    if (target === 0) { el.textContent = "0"; return; }
    let current = 0;
    const step = Math.max(1, Math.floor(target / 20));
    const interval = setInterval(() => {
      current += step;
      if (current >= target) { current = target; clearInterval(interval); }
      el.textContent = current;
    }, 30);
  }

  // --- Temporal classification ---
  function getTemporality(dateStr) {
    if (!dateStr) return "future";
    const raceDate = new Date(dateStr + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = (raceDate - today) / (1000 * 60 * 60 * 24);
    if (diffDays < 0) return "past";
    if (diffDays <= 7) return "this-week";
    if (diffDays <= 30) return "this-month";
    return "future";
  }

  function getColor(temp) {
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

    // Individual race circles — larger for multi-edition
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
        "text-field": ["concat", "×", ["to-string", ["get", "edition_count"]]],
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
    map.on("click", "clusters", async (e) => {
      const features = map.queryRenderedFeatures(e.point, { layers: ["clusters"] });
      if (!features.length) return;
      const clusterId = features[0].properties.cluster_id;
      const zoom = await map.getSource("races").getClusterExpansionZoom(clusterId);
      map.easeTo({ center: features[0].geometry.coordinates, zoom: zoom + 1 });
    });

    // Click individual point -> popup with all editions
    map.on("click", "race-points", (e) => {
      if (!e.features || !e.features.length) return;
      const coords = e.features[0].geometry.coordinates.slice();
      const p = e.features[0].properties;

      while (Math.abs(e.lngLat.lng - coords[0]) > 180) {
        coords[0] += e.lngLat.lng > coords[0] ? 360 : -360;
      }

      // Parse editions JSON
      let editions = [];
      try {
        editions = JSON.parse(p.editions_json);
      } catch (_) {
        editions = [{
          name: p.name, date: p.date, member_count: p.member_count,
          platform: p.platform, url: p.url, color: p.color,
        }];
      }

      let popupHtml;
      if (editions.length > 1) {
        // Multi-edition: show timeline
        const title = p.name.replace(/\s*\d{4}\s*$/, "").trim();
        const timelineHtml = editions.map((ed) => {
          const year = ed.date ? ed.date.substring(0, 4) : "?";
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
            ? `<div class="timeline-names">${truncateNames(ed.first_names, 3)}</div>`
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

        popupHtml = `
          <div class="race-popup">
            <div class="popup-title" style="border-left: 3px solid ${p.color}; padding-left: 10px">${title}</div>
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
          ? `<div class="popup-names">${truncateNames(ed.first_names, 3)}</div>`
          : "";
        const linkHtml = ed.url
          ? `<a class="popup-link" href="${ed.url}" target="_blank" rel="noopener">Voir sur ${ed.platform} &rarr;</a>`
          : "";

        popupHtml = `
          <div class="race-popup">
            <div class="popup-title" style="border-left: 3px solid ${ed.color || p.color}; padding-left: 10px">${ed.name}</div>
            <div class="popup-meta">${dateFormatted}${p.location ? " — " + p.location : ""}</div>
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
    });

    // Cursor
    ["clusters", "race-points"].forEach((layer) => {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
    });

    // Fit bounds to all races
    fitBounds(allRaces);
  }

  function buildGeoJSON(groups) {
    return {
      type: "FeatureCollection",
      features: groups
        .filter((g) => g.latest.lat != null && g.latest.lng != null)
        .map((g) => {
          const r = g.latest;
          const totalMembers = g.editions.reduce((s, e) => s + e.member_count, 0);
          // For color: use latest future edition, or latest overall
          const futureEd = g.editions.find((e) => getTemporality(e.date) !== "past");
          const colorRef = futureEd || r;

          // Build compact editions data for popup
          const editionsData = g.editions.map((e) => ({
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

  function fitBounds(races) {
    const valid = races.filter((r) => r.lat != null && r.lng != null);
    if (valid.length === 0) return;
    if (valid.length === 1) {
      map.flyTo({ center: [valid[0].lng, valid[0].lat], zoom: 10 });
      return;
    }
    const bounds = new maplibregl.LngLatBounds();
    valid.forEach((r) => bounds.extend([r.lng, r.lat]));
    // Adapt padding for mobile (sidebar at bottom) vs desktop (sidebar on left)
    const isMobile = window.innerWidth <= 768;
    const padding = isMobile
      ? { top: 40, bottom: 40, left: 40, right: 40 }
      : { top: 60, bottom: 60, left: 420, right: 60 };
    map.fitBounds(bounds, { padding, maxZoom: 12 });
  }

  // --- Rendering ---
  let activeFilter = "upcoming"; // "upcoming", "recent", "all"

  function matchesDistance(distances, range) {
    if (!range || !distances || !distances.length) return !range;
    const [minStr, maxStr] = range.split("-");
    if (range === "42+") {
      return distances.some((d) => d >= 42);
    }
    const min = parseFloat(minStr);
    const max = parseFloat(maxStr);
    return distances.some((d) => d >= min && d <= max);
  }

  function renderAll() {
    const dateFrom = document.getElementById("date-from").value;
    const dateTo = document.getElementById("date-to").value;
    const filterType = document.getElementById("filter-type").value;
    const filterDist = document.getElementById("filter-distance").value;
    const filterMember = document.getElementById("filter-member").value;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayStr = today.toISOString().slice(0, 10);

    // "Récentes" = 3 months ago to today
    const threeMonthsAgo = new Date(today);
    threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
    const threeMonthsAgoStr = threeMonthsAgo.toISOString().slice(0, 10);

    let filtered = allRaces.filter((r) => {
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
    if (document.getElementById("filter-type").value) count++;
    if (document.getElementById("filter-distance").value) count++;
    if (document.getElementById("filter-member").value) count++;
    if (document.getElementById("date-from").value) count++;
    if (document.getElementById("date-to").value) count++;
    badge.textContent = count;
    badge.style.display = count > 0 ? "inline-block" : "none";
  }

  function renderList(groups) {
    const list = document.getElementById("race-list");
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Sort: groups with upcoming editions first, then by nearest date
    const sorted = [...groups].sort((a, b) => {
      const aFutureEd = a.editions.find((e) => e.date && new Date(e.date + "T00:00:00") >= today);
      const bFutureEd = b.editions.find((e) => e.date && new Date(e.date + "T00:00:00") >= today);
      if (aFutureEd && !bFutureEd) return -1;
      if (!aFutureEd && bFutureEd) return 1;
      const aDate = aFutureEd ? new Date(aFutureEd.date + "T00:00:00") : new Date(a.latest.date + "T00:00:00");
      const bDate = bFutureEd ? new Date(bFutureEd.date + "T00:00:00") : new Date(b.latest.date + "T00:00:00");
      if (aFutureEd && bFutureEd) return aDate - bDate;
      return bDate - aDate;
    });

    if (sorted.length === 0) {
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">&#128270;</div>Aucune course trouvee</div>';
      return;
    }

    list.innerHTML = sorted
      .map((group) => {
        const r = group.latest;
        // Use future edition for temporality if available
        const futureEd = group.editions.find((e) => getTemporality(e.date) !== "past");
        const displayEd = futureEd || r;
        const temp = getTemporality(displayEd.date);

        const dateFormatted = displayEd.date
          ? new Date(displayEd.date + "T00:00:00").toLocaleDateString("fr-FR", {
              day: "numeric", month: "short", year: "numeric",
            })
          : "?";

        const totalMembers = group.editions.reduce((s, e) => s + e.member_count, 0);
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
          ? `<div class="race-names">${truncateNames(firstNames, 3)}</div>`
          : "";

        const typeBadge = r.race_type && r.race_type !== "autre"
          ? `<span class="type-badge type-${r.race_type}">${r.race_type}</span>`
          : "";
        const distLabel = r.distances && r.distances.length
          ? r.distances.map((d) => `${d}km`).join(", ")
          : "";

        return `
        <div class="race-card" data-id="${r.id}" data-temp="${temp}" data-lng="${r.lng}" data-lat="${r.lat}">
          <div class="race-name">${displayName} ${editionBadge} ${typeBadge}</div>
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
    const sidebar = document.getElementById("sidebar");
    const toggle = document.getElementById("sidebar-toggle");
    const header = document.getElementById("sidebar-header");
    const iconClose = document.getElementById("toggle-close");
    const iconOpen = document.getElementById("toggle-open");

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
      window.__sidebarSnap = getSnapPositions;

      let startY = 0;
      let startTranslate = 0;
      let currentTranslate = 0;
      let isDragging = false;
      let dragStartTime = 0;

      function getTranslateY() {
        const matrix = new DOMMatrixReadOnly(getComputedStyle(sidebar).transform);
        return matrix.m42;
      }

      function snapTo(position) {
        sidebar.classList.remove("dragging", "collapsed");
        sidebar.style.transform = `translateY(${position}px)`;
        currentTranslate = position;
        setTimeout(() => map.resize(), 350);
      }

      header.addEventListener("touchstart", (e) => {
        isDragging = true;
        dragStartTime = Date.now();
        startY = e.touches[0].clientY;
        startTranslate = getTranslateY();
        sidebar.classList.add("dragging");
        sidebar.classList.remove("collapsed");
      }, { passive: true });

      header.addEventListener("touchmove", (e) => {
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
        activeFilter = btn.dataset.filter;
        renderAll();
      });
    });

    // Date filters (debounced)
    let filterTimeout;
    function debouncedRender() {
      clearTimeout(filterTimeout);
      filterTimeout = setTimeout(renderAll, 150);
    }
    document.getElementById("date-from").addEventListener("change", debouncedRender);
    document.getElementById("date-to").addEventListener("change", debouncedRender);
    document.getElementById("filter-type").addEventListener("change", debouncedRender);
    document.getElementById("filter-distance").addEventListener("change", debouncedRender);
    document.getElementById("filter-member").addEventListener("change", debouncedRender);

    // Mobile: filter panel toggle
    const filterToggle = document.getElementById("filter-toggle");
    const filtersPanel = document.getElementById("filters");
    if (filterToggle) {
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

    // Event delegation for race cards
    document.getElementById("race-list").addEventListener("click", (e) => {
      const card = e.target.closest(".race-card");
      if (!card) return;
      const lng = parseFloat(card.dataset.lng);
      const lat = parseFloat(card.dataset.lat);
      if (!isNaN(lng) && !isNaN(lat)) {
        map.flyTo({ center: [lng, lat], zoom: 13, duration: 800 });
      }
    });
  }

  function updateLastUpdated(ts) {
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
    const link = document.getElementById("legal-link");
    const modal = document.getElementById("legal-modal");
    const close = modal.querySelector(".modal-close");

    link.addEventListener("click", (e) => {
      e.preventDefault();
      modal.classList.remove("hidden");
    });
    close.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.classList.add("hidden");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") modal.classList.add("hidden");
    });
  }

  // --- Start ---
  document.addEventListener("DOMContentLoaded", init);
})();
