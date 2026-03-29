// RunEvent86 — "Ou court le club ?" — MapLibre GL + Protomaps
(function () {
  "use strict";

  const MAPTILER_KEY = window.location.hostname === "localhost"
    ? "a1BC84y8LOVbz39F83Di"   // dev (localhost only)
    : "i5wEDxjjkYVzkgsRe3xx";  // prod (juulieen.github.io)

  let map;
  let allRaces = [];
  let currentPopup = null;

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
  }

  // --- Data loading ---
  function loadData() {
    fetch("data/races.json")
      .then((r) => r.json())
      .then((data) => {
        allRaces = (data.races || []).filter((r) => r.member_count > 0);
        updateLastUpdated(data.last_updated);
        updateStats(allRaces);
        setupMapLayers();
        renderAll();
      })
      .catch((err) => {
        console.error("Erreur chargement donnees:", err);
        document.getElementById("race-list").innerHTML =
          '<div class="empty-state"><div class="empty-icon">&#128683;</div>Impossible de charger les donnees</div>';
      });
  }

  // --- Stats ---
  function updateStats(races) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const totalMembers = races.reduce((s, r) => s + r.member_count, 0);
    const upcoming = races.filter(
      (r) => r.date && new Date(r.date + "T00:00:00") >= today
    ).length;
    animateCounter("stat-races", races.length);
    animateCounter("stat-members", totalMembers);
    animateCounter("stat-upcoming", upcoming);
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
    // Clustered source
    map.addSource("races", {
      type: "geojson",
      data: buildGeoJSON(allRaces),
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
        "circle-radius": 10,
        "circle-stroke-width": 2.5,
        "circle-stroke-color": "#fff",
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

    // --- Interactions ---

    // Click cluster -> zoom in
    map.on("click", "clusters", async (e) => {
      const features = map.queryRenderedFeatures(e.point, { layers: ["clusters"] });
      if (!features.length) return;
      const clusterId = features[0].properties.cluster_id;
      const zoom = await map.getSource("races").getClusterExpansionZoom(clusterId);
      map.easeTo({ center: features[0].geometry.coordinates, zoom: zoom + 1 });
    });

    // Click individual point -> popup
    map.on("click", "race-points", (e) => {
      if (!e.features || !e.features.length) return;
      const coords = e.features[0].geometry.coordinates.slice();
      const p = e.features[0].properties;

      while (Math.abs(e.lngLat.lng - coords[0]) > 180) {
        coords[0] += e.lngLat.lng > coords[0] ? 360 : -360;
      }

      const dateFormatted = p.date
        ? new Date(p.date + "T00:00:00").toLocaleDateString("fr-FR", {
            weekday: "long", day: "numeric", month: "long", year: "numeric",
          })
        : "Date inconnue";

      const membersHtml = p.member_count > 0
        ? `<div class="popup-members-count">${p.member_count} membre${p.member_count > 1 ? "s" : ""} inscrit${p.member_count > 1 ? "s" : ""}</div>`
        : "";

      const linkHtml = p.url
        ? `<a class="popup-link" href="${p.url}" target="_blank" rel="noopener">Voir sur ${p.platform} &rarr;</a>`
        : "";

      if (currentPopup) currentPopup.remove();
      currentPopup = new maplibregl.Popup({ offset: 12, maxWidth: "280px" })
        .setLngLat(coords)
        .setHTML(`
          <div class="race-popup">
            <div class="popup-title" style="border-left: 3px solid ${p.color}; padding-left: 10px">${p.name}</div>
            <div class="popup-meta">${dateFormatted}${p.location ? " — " + p.location : ""}</div>
            ${membersHtml}
            ${linkHtml}
          </div>
        `)
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

  function buildGeoJSON(races) {
    return {
      type: "FeatureCollection",
      features: races
        .filter((r) => r.lat != null && r.lng != null)
        .map((r) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [r.lng, r.lat] },
          properties: {
            id: r.id,
            name: r.name,
            date: r.date || "",
            location: r.location || "",
            platform: r.platform || "",
            url: r.url || "",
            member_count: r.member_count,
            color: getColor(getTemporality(r.date)),
            temporality: getTemporality(r.date),
          },
        })),
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
    map.fitBounds(bounds, { padding: { top: 60, bottom: 60, left: 420, right: 60 }, maxZoom: 12 });
  }

  // --- Rendering ---
  function renderAll() {
    const dateFrom = document.getElementById("date-from").value;
    const dateTo = document.getElementById("date-to").value;

    let filtered = allRaces.filter((r) => {
      if (dateFrom && r.date < dateFrom) return false;
      if (dateTo && r.date > dateTo) return false;
      return true;
    });

    // Update map source
    const source = map.getSource("races");
    if (source) {
      source.setData(buildGeoJSON(filtered));
    }

    renderList(filtered);
  }

  function renderList(races) {
    const list = document.getElementById("race-list");
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const sorted = [...races].sort((a, b) => {
      const aDate = new Date(a.date + "T00:00:00");
      const bDate = new Date(b.date + "T00:00:00");
      const aFuture = aDate >= today;
      const bFuture = bDate >= today;
      if (aFuture && !bFuture) return -1;
      if (!aFuture && bFuture) return 1;
      if (aFuture) return aDate - bDate;
      return bDate - aDate;
    });

    if (sorted.length === 0) {
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">&#128270;</div>Aucune course trouvee</div>';
      return;
    }

    list.innerHTML = sorted
      .map((race) => {
        const temp = getTemporality(race.date);
        const dateFormatted = race.date
          ? new Date(race.date + "T00:00:00").toLocaleDateString("fr-FR", {
              day: "numeric", month: "short", year: "numeric",
            })
          : "?";

        return `
        <div class="race-card" data-id="${race.id}" data-temp="${temp}" data-lng="${race.lng}" data-lat="${race.lat}">
          <div class="race-name">${race.name}</div>
          <div class="race-meta">
            <span class="date">${dateFormatted}</span>
            <span class="location">${race.location || ""}</span>
            <span class="member-badge">${race.member_count} membre${race.member_count > 1 ? "s" : ""}</span>
          </div>
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

    // Date filters (debounced)
    let filterTimeout;
    function debouncedRender() {
      clearTimeout(filterTimeout);
      filterTimeout = setTimeout(renderAll, 150);
    }
    document.getElementById("date-from").addEventListener("change", debouncedRender);
    document.getElementById("date-to").addEventListener("change", debouncedRender);

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
