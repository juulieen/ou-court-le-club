// RunEvent86 - Interactive race map
(function () {
  "use strict";

  const MAP_CENTER = [46.58, 0.34];
  const MAP_ZOOM = 9;

  let map;
  let markers = L.markerClusterGroup();
  let allRaces = [];
  let markerMap = {};

  // --- Init ---
  function init() {
    map = L.map("map", { zoomControl: false }).setView(MAP_CENTER, MAP_ZOOM);

    L.control.zoom({ position: "topright" }).addTo(map);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 18,
    }).addTo(map);

    map.addLayer(markers);

    setupSidebar();
    loadData();
  }

  // --- Data loading ---
  function loadData() {
    fetch("data/races.json")
      .then((r) => r.json())
      .then((data) => {
        allRaces = (data.races || []).filter((r) => r.member_count > 0);
        updateLastUpdated(data.last_updated);
        renderAll();
      })
      .catch((err) => {
        console.error("Erreur chargement donnees:", err);
        document.getElementById("race-list").innerHTML =
          '<div style="padding:20px;color:#999;text-align:center">Aucune donnee disponible</div>';
      });
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

  function getMarkerClass(temporality) {
    return "marker-" + temporality;
  }

  function getMarkerColor(temporality) {
    switch (temporality) {
      case "past": return "#9e9e9e";
      case "this-week": return "#e53935";
      case "this-month": return "#fb8c00";
      default: return "#1e88e5";
    }
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

    renderMarkers(filtered);
    renderList(filtered);
  }

  function renderMarkers(races) {
    markers.clearLayers();
    markerMap = {};

    races.forEach((race) => {
      if (race.lat == null || race.lng == null) return;

      const temp = getTemporality(race.date);
      const icon = L.divIcon({
        className: "",
        html: `<div class="marker-icon ${getMarkerClass(temp)}">${race.member_count}</div>`,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      });

      const marker = L.marker([race.lat, race.lng], { icon });
      marker.bindPopup(createPopup(race));
      markers.addLayer(marker);
      markerMap[race.id] = marker;
    });
  }

  function createPopup(race) {
    const temp = getTemporality(race.date);
    const color = getMarkerColor(temp);
    const dateFormatted = race.date
      ? new Date(race.date + "T00:00:00").toLocaleDateString("fr-FR", {
          weekday: "long",
          day: "numeric",
          month: "long",
          year: "numeric",
        })
      : "Date inconnue";

    let membersHtml = "";
    if (race.member_count > 0) {
      membersHtml = `<div class="popup-members-count">${race.member_count} membre${race.member_count > 1 ? "s" : ""} du club inscrit${race.member_count > 1 ? "s" : ""}</div>`;
    }

    let linkHtml = "";
    if (race.url) {
      linkHtml = `<a class="popup-link" href="${race.url}" target="_blank" rel="noopener">Voir sur ${race.platform}</a>`;
    }

    return `
      <div class="race-popup">
        <div class="popup-title" style="border-left: 3px solid ${color}; padding-left: 8px">${race.name}</div>
        <div class="popup-meta">${dateFormatted} — ${race.location}</div>
        ${membersHtml}
        ${linkHtml}
      </div>
    `;
  }

  function renderList(races) {
    const list = document.getElementById("race-list");
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Sort: upcoming first (by date asc), then past (by date desc)
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
      list.innerHTML = '<div style="padding:20px;color:#999;text-align:center">Aucune course trouvee</div>';
      return;
    }

    list.innerHTML = sorted
      .map((race) => {
        const temp = getTemporality(race.date);
        const dateFormatted = race.date
          ? new Date(race.date + "T00:00:00").toLocaleDateString("fr-FR", {
              day: "numeric",
              month: "short",
              year: "numeric",
            })
          : "?";
        const isPast = temp === "past" ? " past" : "";

        return `
        <div class="race-card${isPast}" data-id="${race.id}">
          <div class="race-name">${race.name}</div>
          <div class="race-meta">
            <span class="date">${dateFormatted}</span>
            <span>${race.location}</span>
            <span class="member-badge">${race.member_count} membre${race.member_count > 1 ? "s" : ""}</span>
          </div>
        </div>`;
      })
      .join("");

    // Click handlers for fly-to
    list.querySelectorAll(".race-card").forEach((card) => {
      card.addEventListener("click", () => {
        const id = card.dataset.id;
        const marker = markerMap[id];
        if (marker) {
          map.flyTo(marker.getLatLng(), 13, { duration: 0.8 });
          markers.zoomToShowLayer(marker, () => marker.openPopup());
        }
      });
    });
  }

  // --- Sidebar ---
  function setupSidebar() {
    const sidebar = document.getElementById("sidebar");
    const toggle = document.getElementById("sidebar-toggle");
    const header = document.getElementById("sidebar-header");

    toggle.addEventListener("click", () => {
      sidebar.classList.toggle("collapsed");
      toggle.textContent = sidebar.classList.contains("collapsed") ? "☰" : "✕";
      setTimeout(() => map.invalidateSize(), 350);
    });

    // Mobile: tap header to toggle
    header.addEventListener("click", () => {
      if (window.innerWidth <= 768) {
        sidebar.classList.toggle("collapsed");
        setTimeout(() => map.invalidateSize(), 350);
      }
    });

    // Date filters
    document.getElementById("date-from").addEventListener("change", renderAll);
    document.getElementById("date-to").addEventListener("change", renderAll);
  }

  function updateLastUpdated(ts) {
    if (!ts) return;
    const el = document.getElementById("last-updated");
    if (el) {
      const d = new Date(ts);
      el.textContent = "Mis a jour : " + d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
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
  }

  // --- Start ---
  document.addEventListener("DOMContentLoaded", () => {
    init();
    setupLegalModal();
  });
})();
