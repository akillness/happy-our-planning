/* 놓치마 가족 행사 탐색 — 정적 JSON을 읽어 필터·지도·목록을 함께 갱신한다. */
(function () {
  "use strict";

  const DATA = "./data/";
  const state = {
    events: [], regions: [], facets: {}, fuse: null,
    map: null, markers: null, userMarker: null, mapReady: false, tileFailures: new Set(),
    mapPoints: [], mapFitPending: false, lastAutoFitCenter: null, lastAutoFitZoom: null, markerGroups: [],
    filteredRows: [], wired: false, retryWired: false, dataReady: false,
    page: 0, lastPageSize: 0, sortMode: "relevance", userPos: null, lastQuery: "",
    activePopup: null, viewportFrame: 0,
    _search: null, lastUpdated: null,
  };

  const MAP_ICON_SIZE = [44, 48];
  const MAP_ICON_ANCHOR = [22, 44];
  const MAP_POPUP_ANCHOR = [0, -40];
  const MAP_POPUP_OFFSET = [0, 0];
  const MAP_BASE_PADDING = 24;
  const MAP_OVERLAY_GAP = 8;
  const eventIconCache = new Map();

  const $ = (id) => document.getElementById(id);
  const dpart = (value) => (value || "").slice(0, 10);
  const kstFormatter = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" });
  const today = () => kstFormatter.format(new Date());
  const safeId = (id) => String(id).replace(/[^0-9A-Za-z._-]/g, "_");
  const isFree = (price) => price === "free" || price === 0 || price === "0";
  const hasFamilySignal = (event) => event.family_friendly === true;
  const EXPERIENCE_ORDER = ["자연·생태", "과학·탐구", "만들기·예술", "농촌·먹거리", "신체·놀이", "기타 체험"];
  const LINK_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
  const isExperience = (event) => event && event.is_experience === true;
  const experienceCategories = (event) => Array.isArray(event && event.experience_categories) ? event.experience_categories.filter(Boolean) : [];
  function isPrivateLookingHost(hostname) {
    const host = String(hostname || "").toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal") || host.endsWith(".test") || host.endsWith(".invalid") || host === "example.org" || host.endsWith(".example.org")) return true;
    // Published proofs require a trusted DNS name; reject every IP literal,
    // including IPv4-mapped IPv6 and NAT64 forms, without duplicating IP parsing.
    return host.includes(":") || /^\d+\.\d+\.\d+\.\d+$/.test(host);
  }

  function safeVerifiedUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(String(value));
      if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443") || isPrivateLookingHost(url.hostname)) return "";
      return url.href;
    } catch (_) {
      return "";
    }
  }

  function formatTimestamp(value) {
    if (!value) return "";
    const timestamp = Date.parse(String(value));
    if (!Number.isFinite(timestamp)) return "";
    const locale = i18n() && i18n().getLanguage() === "en" ? "en-US" : "ko-KR";
    return new Intl.DateTimeFormat(locale, { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp));
  }

  // Shared freshness/safety projection reused by both the canonical info-URL
  // proof and the independently-derived booking-URL proof (same policy, same
  // 7-day cache semantics) so neither path can drift from the other.
  function freshnessProof(status, checkedAtRaw, rawUrlValue, reasonRaw, now) {
    const normalizedStatus = String(status || "unchecked").toLowerCase();
    const checkedAt = Date.parse(String(checkedAtRaw || ""));
    const rawHref = String(rawUrlValue || "");
    const href = safeVerifiedUrl(rawHref);
    const unsafeHref = Boolean(rawHref && !href);
    const fresh = normalizedStatus === "verified" && href && Number.isFinite(checkedAt)
      && checkedAt <= now && now - checkedAt <= LINK_MAX_AGE_MS;
    return {
      status: normalizedStatus, unsafeHref, fresh: Boolean(fresh),
      href: fresh ? href : "", reason: String(reasonRaw || "").trim(),
      checked: formatTimestamp(checkedAtRaw),
    };
  }

  function linkProof(event, now = Date.now()) {
    const proof = freshnessProof(event && event.link_status, event && event.link_checked_at,
      event && event.link_url, event && event.link_reason, now);
    let label = t("link.status.unverified");
    if (proof.status === "blocked" || proof.unsafeHref) label = t("link.status.blocked");
    else if (proof.status === "dead") label = t("link.status.dead");
    else if (proof.status === "verified" && !proof.fresh) label = t("link.status.expired");
    else if (proof.fresh) label = t("link.status.verified");
    return { allowed: proof.fresh, href: proof.href, label, reason: proof.reason, checked: proof.checked };
  }
  function proofReason(reason) {
    const raw = String(reason || "").trim();
    const keys = {
      "연결 확인": "link.status.verified",
      "검증 기록 만료": "link.status.expired",
      "원본 URL 없음": "link.reason.noSource",
      "신뢰 도메인 아님": "link.reason.untrustedDomain",
    };
    return raw && keys[raw] ? t(keys[raw]) : raw;
  }

  function linkStatusNode(event) {
    const proof = linkProof(event);
    const visible = `${proof.label} · ${proof.checked ? t("link.checkedAt", { time: proof.checked }) : t("link.checkedNone")}${proof.reason ? ` · ${proofReason(proof.reason)}` : ""}`;
    const node = make("span", `link-status${proof.allowed ? " verified" : ""}`, visible);
    node.title = visible;
    return node;
  }

  // Provider-explicit booking destination (event.offers.url), verified
  // independently of the canonical info URL via the same proof policy/cache.
  // No sales/remaining-seat feed exists anywhere in this build, so a fresh
  // verified booking link still never implies 신청가능/예약가능 — the caller
  // always shows the constant 예매처에서 확인 disclaimer; only the clickable
  // anchor depends on freshness, and never for an already-ended event.
  function bookingProof(event, now = Date.now()) {
    const proof = freshnessProof(event && event.booking_link_status, event && event.booking_link_checked_at,
      event && event.booking_link_url, event && event.booking_link_reason, now);
    const allowed = proof.fresh && !isEnded(event);
    return { allowed, href: allowed ? proof.href : "", reason: proof.reason, checked: proof.checked };
  }

  function bookingStatusNode(event) {
    const proof = bookingProof(event);
    const visible = `${t("link.bookingDisclaimer")}${proof.checked ? ` · ${t("link.checkedAt", { time: proof.checked })}` : ""}${proof.reason ? ` · ${proofReason(proof.reason)}` : ""}`;
    const node = make("span", `link-status booking-status${proof.allowed ? " verified" : ""}`, visible);
    node.title = visible;
    return node;
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  // --- i18n bridge (web/public/i18n.js, window.NotchimaI18n) ---------------
  // app.js never hardcodes catalog loading/URL/storage logic itself — that
  // all lives in i18n.js. Here we only consume its stable API surface, with
  // a defensive fallback (loud console.error, not a silent duplicate
  // catalog) if the script tag is ever missing from index.html.
  let i18nMissingWarned = false;
  function i18n() {
    if (window.NotchimaI18n) return window.NotchimaI18n;
    if (!i18nMissingWarned) {
      i18nMissingWarned = true;
      console.error("NotchimaI18n not found — add <script src=\"./i18n.js\"></script> before app.js.");
    }
    return null;
  }
  function t(key, vars) {
    const engine = i18n();
    return engine ? engine.t(key, vars) : key;
  }
  function vocab(value, kind) {
    const engine = i18n();
    return engine ? engine.label(value, kind) : value;
  }
  function localDate(value) {
    const engine = i18n();
    return engine ? engine.formatDate(value) : (value ? String(value).slice(0, 10) : "일정 정보 없음");
  }
  function localMoney(value) {
    const engine = i18n();
    return engine ? engine.formatMoney(value) : String(value == null ? "" : value);
  }
  function countLabel(n) {
    return `${n}${t("common.countUnit")}`;
  }
  // Icon-only mobile tools keep their SVG (aria-hidden) intact and only
  // update the visually-hidden span.control-label — never the button's own
  // textContent, which would erase the icon. Falls back to plain textContent
  // for controls that don't (yet) have that span.
  function setControlLabel(id, text) {
    const node = $(id);
    if (!node) return;
    const span = node.querySelector && node.querySelector(".control-label");
    if (span) span.textContent = text;
    else node.textContent = text;
    node.setAttribute("aria-label", text);
    node.title = text;
  }

  function setHidden(id, hidden) {
    const node = $(id);
    if (node) node.hidden = hidden;
  }
  function setDataReady(ready) {
    state.dataReady = Boolean(ready);
    const ids = ["q", "sido", "theme", "age", "activity", "from", "to", "kids", "freeOnly", "applyable", "reset", "aiBtn", "sortRel", "sortNew", "sortDist", "pagePrev", "pageNext"];
    ids.forEach((id) => {
      const node = $(id);
      if (node) node.disabled = !state.dataReady;
    });
  }
  function tileFailureKey(event) {
    const coords = event && event.coords;
    if (coords && Number.isFinite(coords.z) && Number.isFinite(coords.x) && Number.isFinite(coords.y)) {
      return `${coords.z}/${coords.x}/${coords.y}`;
    }
    return "unknown-tile";
  }

  function updateMapTileStatus() {
    const fallback = $("mapFallback");
    if (!fallback || !state.map) return false;
    const wasHidden = fallback.hidden;
    if (state.tileFailures.size) {
      fallback.textContent = t("map.tileFallback");
      fallback.hidden = false;
    } else {
      fallback.hidden = true;
    }
    return wasHidden !== fallback.hidden;
  }

  function bindTileStatus(tileLayer) {
    if (!tileLayer || typeof tileLayer.on !== "function") return;
    tileLayer.on("tileerror", (event) => {
      state.tileFailures.add(tileFailureKey(event));
      if (updateMapTileStatus()) scheduleMapViewportRefresh();
    });
    const clearFailure = (event) => {
      state.tileFailures.delete(tileFailureKey(event));
      if (updateMapTileStatus()) scheduleMapViewportRefresh();
    };
    tileLayer.on("tileload tileunload", clearFailure);
  }

  function disposeState() {
    // 동적 메모리 정리: 이전 인덱스·마커·검색 캐시·페이지 상태를 버린다.
    state.fuse = null;
    state._search = null;
    state.page = 0;
    state.mapPoints = [];
    state.mapFitPending = false;
    state.markerGroups = [];
    if (state.markers) state.markers.clearLayers();
    const list = $("list");
    if (list) clear(list);
  }

  function escHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  function highlight(text, query) {
    const safe = escHtml(text);
    const needle = String(query || "").trim();
    if (!needle || needle.length < 2) return safe;
    const idx = safe.toLowerCase().indexOf(needle.toLowerCase());
    if (idx < 0) return safe;
    return `${safe.slice(0, idx)}<mark>${safe.slice(idx, idx + needle.length)}</mark>${safe.slice(idx + needle.length)}`;
  }

  function haversineKm(aLat, aLng, bLat, bLng) {
    const rad = (d) => (d * Math.PI) / 180;
    const R = 6371;
    const dLat = rad(bLat - aLat);
    const dLng = rad(bLng - aLng);
    const s = Math.sin(dLat / 2) ** 2
      + Math.cos(rad(aLat)) * Math.cos(rad(bLat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(s));
  }

  function eventDist(event) {
    if (!state.userPos || typeof event.lat !== "number" || typeof event.lng !== "number") return null;
    return haversineKm(state.userPos.lat, state.userPos.lng, event.lat, event.lng);
  }

  function fmtDist(km) {
    if (km == null) return "";
    return km < 1 ? t("dist.meters", { value: Math.round(km * 1000) }) : t("dist.km", { value: km.toFixed(1) });
  }


  function isDemoEvent(event) {
    if (String(event.source || "").toLowerCase() === "example") return true;
    try {
      const url = new URL(String(event.url || ""), location.href);
      return url.hostname === "example.org" || url.hostname.endsWith(".example.org");
    } catch (_) {
      return false;
    }
  }

  async function getJSON(name) {
    const response = await fetch(DATA + name, { cache: "no-cache" });
    if (!response.ok) throw new Error(t("error.loadNamed", { name, status: response.status }));
    return response.json();
  }

  function overlayRects(mapRect) {
    const nodes = [];
    const add = (node) => {
      if (node && !nodes.includes(node)) nodes.push(node);
    };
    add($("filters"));
    add($("filterMenu"));
    add($("resultsPanel"));
    add($("mapFallback"));
    if (document.querySelectorAll) {
      document.querySelectorAll(".search-dock, .filter-menu, .brand-tile, .results, .map-tools, .leaflet-control-zoom, .leaflet-control-attribution").forEach(add);
    }
    return nodes.map((node) => {
      if (!node || node.hidden || typeof node.getBoundingClientRect !== "function") return null;
      const rect = node.getBoundingClientRect();
      if (!rect.width || !rect.height
        || rect.right <= mapRect.left || rect.left >= mapRect.right
        || rect.bottom <= mapRect.top || rect.top >= mapRect.bottom) return null;
      const styles = typeof window.getComputedStyle === "function" ? window.getComputedStyle(node) : null;
      const declaredEdge = styles && typeof styles.getPropertyValue === "function"
        ? styles.getPropertyValue("--map-edge").trim().toLowerCase() : "";
      const edge = declaredEdge || (node.id === "mapFallback" ? "bottom" : "");
      if (!["top", "right", "bottom", "left"].includes(edge)) return null;
      return {
        rect,
        top: edge === "top",
        right: edge === "right",
        bottom: edge === "bottom",
        left: edge === "left",
      };
    }).filter(Boolean);
  }
  function captureChipFocus(root) {
    const active = document.activeElement;
    if (!root || !active || !root.contains(active) || typeof active.closest !== "function") return null;
    const chip = active.closest("button.region-chip");
    if (!chip || !root.contains(chip)) return null;
    const dim = chip.dataset.dim || (root.id === "regionQuick" ? "sido" : "") || (chip.dataset.activity ? "activity" : "");
    const value = chip.dataset.value || chip.dataset.sido || chip.dataset.activity || "";
    return dim && value ? { dim, value } : null;
  }

  function restoreChipFocus(root, focus) {
    if (!root || !focus || typeof root.querySelectorAll !== "function") return;
    const replacement = Array.from(root.querySelectorAll("button.region-chip")).find((chip) => {
      const dim = chip.dataset.dim || (root.id === "regionQuick" ? "sido" : "") || (chip.dataset.activity ? "activity" : "");
      const value = chip.dataset.value || chip.dataset.sido || chip.dataset.activity || "";
      return dim === focus.dim && value === focus.value;
    });
    if (replacement && typeof replacement.focus === "function") replacement.focus({ preventScroll: true });
  }

  function viewportGeometry() {
    const mapNode = $("map");
    const mapRect = mapNode && typeof mapNode.getBoundingClientRect === "function"
      ? mapNode.getBoundingClientRect() : { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    const mapSize = state.map && typeof state.map.getSize === "function" ? state.map.getSize() : { x: 0, y: 0 };
    const width = mapRect.width || mapSize.x || 0;
    const height = mapRect.height || mapSize.y || 0;
    let left = MAP_BASE_PADDING;
    let top = MAP_BASE_PADDING;
    let right = MAP_BASE_PADDING;
    let bottom = MAP_BASE_PADDING;
    for (const item of overlayRects({
      left: mapRect.left,
      top: mapRect.top,
      right: mapRect.left + width,
      bottom: mapRect.top + height,
    })) {
      const { rect } = item;
      if (item.top) top = Math.max(top, rect.bottom - mapRect.top + MAP_OVERLAY_GAP);
      if (item.right) right = Math.max(right, mapRect.left + width - rect.left + MAP_OVERLAY_GAP);
      if (item.bottom) bottom = Math.max(bottom, mapRect.top + height - rect.top + MAP_OVERLAY_GAP);
      if (item.left) left = Math.max(left, rect.right - mapRect.left + MAP_OVERLAY_GAP);
    }
    const availableWidth = width - left - right;
    const popupGroundClearance = Math.abs(MAP_POPUP_ANCHOR[1]);
    const availableHeight = height - top - bottom;
    // Keep the marker head-to-ground span clear, then reserve Leaflet's native popup pointer and outer margin.
    const contentHeight = Math.floor(availableHeight - popupGroundClearance - 24);
    const maxHeight = contentHeight > 0 ? Math.min(420, contentHeight) : 0;
    // Leaflet's own popup chrome before any real DOM exists to measure against:
    // 1px wrapper padding each side (leaflet.css default) + 14px content margin
    // each side (.leaflet-popup-content override below) = 30px total horizontal.
    const contentWidth = Math.floor(availableWidth - 30);
    // Cap at Leaflet's own prior desktop default (300) as well as the
    // overlay-reserved space: the cream wrapper's own CSS max-width stays
    // <=340px regardless, so an uncapped huge desktop availableWidth must not
    // be handed to Leaflet's content-measurement step, or a long unwrapped
    // title can size the content node wider than the visually-capped wrapper
    // and spill outside it.
    const maxWidth = Math.max(1, Math.min(300, contentWidth));
    // Never let Leaflet's own minWidth clamp back up past our reserved-space
    // maxWidth (Leaflet applies maxWidth then minWidth, so minWidth > maxWidth
    // would silently re-widen the popup past the available overlay-free area).
    const minWidth = Math.min(50, maxWidth);
    return {
      paddingTopLeft: [Math.ceil(left), Math.ceil(top)],
      paddingBottomRight: [Math.ceil(right), Math.ceil(bottom + popupGroundClearance)],
      maxHeight,
      maxWidth,
      minWidth,
      availableWidth,
      availableHeight,
      // A merely-positive available span is not necessarily usable: once
      // Leaflet's own popup chrome (30px horizontal) and the marker
      // head-to-ground clearance/chrome (handled above for contentHeight)
      // are reserved, the popup also needs at least Leaflet's native 50px
      // minWidth to render as a readable popup, not just slivers of width.
      hasSpace: contentWidth >= 50 && contentHeight > 0,
    };
  }

  function eventPopupOptions() {
    const geometry = viewportGeometry();
    return {
      offset: MAP_POPUP_OFFSET.slice(),
      maxHeight: geometry.maxHeight,
      maxWidth: geometry.maxWidth,
      minWidth: geometry.minWidth,
      autoPan: geometry.hasSpace,
      autoPanPaddingTopLeft: geometry.paddingTopLeft.slice(),
      autoPanPaddingBottomRight: geometry.paddingBottomRight.slice(),
    };
  }

  function updatePopupViewport(popup) {
    if (!popup || !popup._map) return;
    const geometry = viewportGeometry();
    if (!geometry.hasSpace) {
      popup.close();
      return;
    }
    if (popup._source && popup._source._notchimaEventMarker) popup.options.offset = MAP_POPUP_OFFSET.slice();
    const popupNode = typeof popup.getElement === "function" ? popup.getElement() : popup._container;
    const contentNode = popupNode && typeof popupNode.querySelector === "function"
      ? popupNode.querySelector(".leaflet-popup-content") : null;
    let popupChrome = 24;
    let popupHorizontalChrome = 30;
    if (popupNode && contentNode
      && typeof popupNode.getBoundingClientRect === "function"
      && typeof contentNode.getBoundingClientRect === "function"
      && typeof window.getComputedStyle === "function") {
      const popupRect = popupNode.getBoundingClientRect();
      const contentRect = contentNode.getBoundingClientRect();
      const marginBottom = Number.parseFloat(window.getComputedStyle(popupNode).marginBottom) || 0;
      const measuredChrome = popupRect.height - contentRect.height + marginBottom;
      if (Number.isFinite(measuredChrome) && measuredChrome > 0) popupChrome = Math.ceil(measuredChrome);
      const measuredHorizontalChrome = popupRect.width - contentRect.width;
      if (Number.isFinite(measuredHorizontalChrome) && measuredHorizontalChrome > 0) popupHorizontalChrome = Math.ceil(measuredHorizontalChrome);
    }
    const popupGroundClearance = Math.abs(MAP_POPUP_ANCHOR[1]);
    const contentHeight = Math.floor(geometry.availableHeight - popupGroundClearance - popupChrome);
    const contentWidth = Math.floor(geometry.availableWidth - popupHorizontalChrome);
    popup.options.maxHeight = contentHeight > 0 ? Math.min(420, contentHeight) : 0;
    popup.options.maxWidth = Math.max(1, Math.min(300, contentWidth));
    popup.options.minWidth = Math.min(50, popup.options.maxWidth);
    popup.options.autoPan = true;
    popup.options.autoPanPaddingTopLeft = geometry.paddingTopLeft.slice();
    popup.options.autoPanPaddingBottomRight = geometry.paddingBottomRight.slice();
    popup.update();
  }

  function currentViewMatchesAutoFit() {
    if (!state.map || !state.lastAutoFitCenter || typeof state.map.getCenter !== "function") return false;
    const center = state.map.getCenter();
    const zoom = typeof state.map.getZoom === "function" ? state.map.getZoom() : null;
    return zoom === state.lastAutoFitZoom
      && Math.abs(center.lat - state.lastAutoFitCenter.lat) < 1e-7
      && Math.abs(center.lng - state.lastAutoFitCenter.lng) < 1e-7;
  }

  function refreshMapViewport() {
    if (!state.mapReady || !state.map) return;
    const wasAutoFit = currentViewMatchesAutoFit();
    state.map.invalidateSize({ pan: false });
    const filterMenu = $("filterMenu");
    if (state.mapFitPending && (!filterMenu || !filterMenu.open)) fitCurrentMap();
    else if (wasAutoFit && state.mapPoints.length && (!filterMenu || !filterMenu.open)) fitCurrentMap();
    const popupLayer = state.activePopup;
    if (popupLayer && typeof popupLayer.isOpen === "function" && popupLayer.isOpen()) updatePopupViewport(popupLayer);
  }

  function scheduleMapViewportRefresh() {
    if (state.viewportFrame) return;
    const raf = typeof window.requestAnimationFrame === "function" ? window.requestAnimationFrame : window.setTimeout;
    state.viewportFrame = raf(() => {
      state.viewportFrame = 0;
      refreshMapViewport();
    });
  }

  function mapFitOptions() {
    const geometry = viewportGeometry();
    if (!geometry.hasSpace) return null;
    return {
      padding: [MAP_BASE_PADDING, MAP_BASE_PADDING],
      paddingTopLeft: geometry.paddingTopLeft,
      paddingBottomRight: geometry.paddingBottomRight,
      maxZoom: 15,
      animate: false,
    };
  }

  function fitCurrentMap() {
    if (!state.mapReady || !state.map) return false;
    const filterMenu = $("filterMenu");
    if (filterMenu && filterMenu.open) {
      state.mapFitPending = true;
      return false;
    }
    const options = mapFitOptions();
    if (!options) {
      state.mapFitPending = true;
      return false;
    }
    state.mapFitPending = false;
    if (state.mapPoints.length) state.map.fitBounds(state.mapPoints, options);
    else if (!state.userPos) state.map.setView([36.5, 127.8], 7, { animate: false });
    if (typeof state.map.getCenter === "function") state.lastAutoFitCenter = state.map.getCenter();
    if (typeof state.map.getZoom === "function") state.lastAutoFitZoom = state.map.getZoom();
    return true;
  }

  function eventIcon(count) {
    const key = count > 1 ? count : 1;
    if (eventIconCache.has(key)) return eventIconCache.get(key);
    const countMarkup = count > 1 ? `<b class="marker-count">${count}</b>` : "";
    const icon = window.L.divIcon({
      className: "event-marker",
      html: `<svg class="event-marker-svg" width="32" height="40" viewBox="0 0 32 40" aria-hidden="true" focusable="false"><path d="M16 40C14 37 4 26 4 16C4 7.163 9.373 0 16 0C22.627 0 28 7.163 28 16C28 26 18 37 16 40" fill="var(--green)"></path><circle cx="16" cy="16" r="4" fill="var(--orange)"></circle></svg>${countMarkup}`,
      iconSize: MAP_ICON_SIZE.slice(),
      iconAnchor: MAP_ICON_ANCHOR.slice(),
      popupAnchor: MAP_POPUP_ANCHOR.slice(),
    });
    eventIconCache.set(key, icon);
    return icon;
  }

  function initMap() {
    if (!$("map")) return;
    if (!window.L) {
      setHidden("mapFallback", false);
      return;
    }
    try {
      state.map = window.L.map("map", { scrollWheelZoom: false }).setView([36.5, 127.8], 7);
      localizeNativeMapControls();
      const tileLayer = window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
      }).addTo(state.map);
      bindTileStatus(tileLayer);
      state.markers = window.L.layerGroup().addTo(state.map);
      state.map.on("popupopen", (event) => {
        state.activePopup = event.popup;
        // Collapse mobile drawers (filter conditions / results) first, so
        // the popup has room and nothing covers it, before fitting it into
        // the viewport. Uses the existing viewportGeometry/mapFitOptions
        // contracts — no invented coordinates, no CSS transform hacks.
        collapseMobileDrawersForPopup();
        updatePopupViewport(event.popup);
      });
      state.map.on("popupclose", (event) => {
        if (state.activePopup === event.popup) state.activePopup = null;
      });
      state.mapReady = true;
    } catch (error) {
      console.error("Map initialization failed", error);
      try {
        if (state.markers && typeof state.markers.clearLayers === "function") state.markers.clearLayers();
        if (state.map && typeof state.map.remove === "function") state.map.remove();
      } catch (cleanupError) {
        console.error("Map initialization cleanup failed", cleanupError);
      }
      state.markers = null;
      state.map = null;
      state.mapReady = false;
      state.activePopup = null;
      state.mapPoints = [];
      state.mapFitPending = false;
      state.tileFailures.clear();
      setHidden("mapFallback", false);
    }
  }
  function localizeNativeMapControls() {
    if (!document.querySelector) return;
    const zoom = document.querySelector(".leaflet-control-zoom");
    if (!zoom || !zoom.querySelector) return;
    const labels = [[".leaflet-control-zoom-in", "map.zoomIn"], [".leaflet-control-zoom-out", "map.zoomOut"]];
    for (const [selector, key] of labels) {
      const button = zoom.querySelector(selector);
      if (!button) continue;
      const label = t(key);
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    }
  }

  function fillSelect(select, items) {
    while (select.options.length > 1) select.remove(1);
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      select.appendChild(option);
    }
  }

  // Canonical Korean filter VALUES never change — only each option's
  // *display* label is translated (vocab()). Safe to call again on a
  // language change: captures/restores each select's current value since
  // fillSelect() clears and rebuilds the <option> list.
  function buildFilters() {
    const selected = { sido: $("sido").value, theme: $("theme").value, age: $("age").value, activity: $("activity").value };
    fillSelect($("sido"), state.regions.map((region) => ({
      value: region.sido, label: `${vocab(region.sido, "sido")} (${region.count})`,
    })));
    const themes = Object.entries(state.facets.theme || {}).sort((a, b) => b[1] - a[1]);
    fillSelect($("theme"), themes.map(([theme, count]) => ({ value: theme, label: `${vocab(theme, "theme")} (${count})` })));
    const ageValues = new Map(Object.keys(state.facets.age_band || {}).map((age) => [age, 0]));
    const activityValues = new Map();
    for (const event of state.events) {
      for (const age of new Set(event.age_bands || [])) ageValues.set(age, (ageValues.get(age) || 0) + 1);
      if (isExperience(event)) for (const category of experienceCategories(event)) activityValues.set(category, (activityValues.get(category) || 0) + 1);
    }
    fillSelect($("age"), Array.from(ageValues.entries()).map(([age, count]) => ({
      value: age, label: count ? `${vocab(age, "age")} (${count})` : vocab(age, "age"),
    })));
    const activities = Array.from(activityValues.entries()).sort((a, b) => (EXPERIENCE_ORDER.indexOf(a[0]) - EXPERIENCE_ORDER.indexOf(b[0])) || b[1] - a[1]);
    fillSelect($("activity"), activities.map(([category, count]) => ({ value: category, label: `${vocab(category, "activity")} (${count})` })));
    for (const [id, value] of Object.entries(selected)) if (value) $(id).value = value;
  }
  function currentFilters() {
    return {
      q: $("q").value.trim(),
      sido: $("sido").value,
      theme: $("theme").value,
      age: $("age").value,
      activity: $("activity").value,
      from: $("from").value,
      to: $("to").value,
      applyable: $("applyable").checked,
      kids: $("kids").checked,
      freeOnly: $("freeOnly").checked,
    };
  }

  const URL_KEYS = ["q", "sido", "theme", "age", "activity", "from", "to"];

  function writeURL(filters) {
    const params = new URLSearchParams();
    const currentLang = new URLSearchParams(location.search).get("lang");
    if (currentLang === "ko" || currentLang === "en") params.set("lang", currentLang);
    for (const key of URL_KEYS) if (filters[key]) params.set(key, filters[key]);
    if (filters.applyable) params.set("applyable", "1");
    if (!filters.kids) params.set("kids", "0");
    if (filters.freeOnly) params.set("free_only", "1");
    const query = params.toString();
    history.replaceState(null, "", query ? `?${query}` : location.pathname);
  }

  function restoreFromURL() {
    const params = new URLSearchParams(location.search);
    for (const key of URL_KEYS) {
      if (!params.has(key)) continue;
      const control = $(key);
      const value = params.get(key);
      if (control.tagName === "SELECT" && value && !Array.from(control.options).some((option) => option.value === value)) {
        const option = make("option", null, t("filter.unlistedOption", { value: vocab(value, key) }));
        option.value = value;
        control.appendChild(option);
      }
      control.value = value;
    }
    $("applyable").checked = params.get("applyable") === "1";
    $("kids").checked = params.get("kids") !== "0";
    $("freeOnly").checked = params.get("free_only") === "1" || params.get("free") === "1";
  }

  function isEnded(event) {
    const end = dpart(event.end_date || event.start_date);
    return Boolean(end && end < today());
  }

  // Explicit source-stated application/sales window only — never derived
  // from performance start/end dates, which are a different fact (when the
  // show happens, not when applications are open).
  function hasValidApplicationPeriod(event, now) {
    const start = dpart(event.application_start);
    const end = dpart(event.application_end);
    if (!start && !end) return false;
    if (start && now < start) return false;
    if (end && now > end) return false;
    return true;
  }

  // "지금 신청 가능" requires BOTH an explicit, currently-valid source
  // application/sales window AND an independently verified, fresh, safe
  // booking URL (scripts.common.links policy via booking_link_*) — never
  // inferred from performance dates/status or from the info/source-page link.
  function isApplyable(event) {
    if (isEnded(event)) return false;
    return hasValidApplicationPeriod(event, today()) && bookingProof(event).allowed;
  }

  function statusInfo(event) {
    if (isEnded(event)) return { label: t("status.ended"), className: "ended" };
    // Neutral calendar-only labels (예정/진행중/정보 없음) cover events with no
    // explicit application window; they never infer app-availability from
    // event.status, and a missing/unmapped display_status always resolves to
    // the Unknown label+className rather than being hidden or guessed.
    const labelKeys = {
      신청전: "status.notYetOpen", 오픈: "status.open", 종료: "status.ended",
      마감임박: "status.closingSoon", 마감: "status.closed",
      예정: "status.upcoming", 진행중: "status.ongoing", "정보 없음": "status.unknown",
    };
    const classNames = {
      신청전: "before", 오픈: "open", 종료: "ended", 마감임박: "imminent", 마감: "closed",
      예정: "upcoming", 진행중: "ongoing", "정보 없음": "unknown",
    };
    const rawStatus = Object.prototype.hasOwnProperty.call(labelKeys, event.display_status)
      ? event.display_status : "정보 없음";
    return { label: t(labelKeys[rawStatus]), className: classNames[rawStatus] };
  }

  function textForSearch(event) {
    return [event.name, event.description, event.sido, event.sigungu, event.experience_summary, (event.experience_categories || []).join(" "), event.family_evidence, ...(event.themes || [])].filter(Boolean).join(" ").toLowerCase();
  }

  function searchedEvents(query) {
    if (!query) { state._search = null; return state.events; }
    if (state._search && state._search.q === query) return state._search.items;
    let items;
    if (state.fuse) items = state.fuse.search(query).map((result) => result.item);
    else {
      const needle = query.toLowerCase();
      items = state.events.filter((event) => textForSearch(event).includes(needle));
    }
    state._search = { q: query, items };
    return items;
  }

  function filterRows(filters, includeSido) {
    return searchedEvents(filters.q).filter((event) => {
      if (includeSido && filters.sido && event.sido !== filters.sido) return false;
      if (filters.theme && !(event.themes || []).includes(filters.theme)) return false;
      if (filters.age && !(event.age_bands || []).includes(filters.age)) return false;
      if (filters.activity && (!isExperience(event) || !experienceCategories(event).includes(filters.activity))) return false;
      if (filters.from && dpart(event.end_date || event.start_date) < filters.from) return false;
      if (filters.to && dpart(event.start_date) > filters.to) return false;
      if (filters.applyable && !isApplyable(event)) return false;
      if (filters.kids && !hasFamilySignal(event)) return false;
      if (filters.freeOnly && !isFree(event.price)) return false;
      return true;
    });
  }

  function badge(text, className) {
    return make("span", `badge${className ? ` ${className}` : ""}`, text);
  }

  function renderBadges(event) {
    const wrap = make("span", "card-badges");
    if (isFree(event.price)) wrap.appendChild(badge(t("badge.free"), "free"));
    const status = statusInfo(event);
    if (status) wrap.appendChild(badge(status.label, status.className));
    if (event.event_type) wrap.appendChild(badge(vocab(event.event_type, "eventType")));
    if (isExperience(event)) wrap.appendChild(badge(t("badge.experience"), "experience"));
    if (isDemoEvent(event)) wrap.appendChild(badge(t("badge.demo"), "discovered"));
    if (event.verification === "web-discovered") {
      const confidence = event.confidence != null ? ` ${Math.round(event.confidence * 100)}%` : "";
      const found = badge(t("badge.discovered", { confidence }), "discovered");
      found.title = t("badge.discoveredTitle");
      wrap.appendChild(found);
    }
    for (const category of experienceCategories(event)) wrap.appendChild(badge(vocab(category, "activity"), "experience-category"));
    for (const theme of event.themes || []) wrap.appendChild(badge(vocab(theme, "theme")));
    return wrap;
  }

  function dateLabel(event) {
    const start = dpart(event.start_date);
    const end = dpart(event.end_date);
    if (end && end !== start) return t("date.range", { start: localDate(start), end: localDate(end) });
    return start ? localDate(start) : t("date.noSchedule");
  }

  function priceLabel(event) {
    // price_text is the raw source phrasing (e.g. multi-tier pricing) and is
    // preserved verbatim, same treatment as duration_text — never translated,
    // never reformatted. Falls back to the canonical localized price when
    // the source gave no free-text price at all.
    if (event.price_text) return event.price_text;
    return localMoney(event.price);
  }

  function localDetailUrl(event) {
    return `events/${safeId(event.id)}.html`;
  }

  function venueLabel(event) {
    if (event.venue && event.address) return `${event.venue} (${event.address})`;
    if (event.venue) return event.venue;
    if (event.address) return event.address;
    return t("common.noInfo");
  }

  // Appends the canonical info-URL proof row and the independently-derived
  // booking-URL proof row to `container`, in that order. The info anchor
  // ("공식 정보 보기") and the booking anchor ("예매 사이트 바로가기") are
  // never conflated: each only appears when its own fresh/verified/safe
  // HTTPS proof allows it, and the booking disclaimer ("예매처에서 확인")
  // is always shown next to the booking slot regardless of proof status —
  // no verified booking link and no source application/sales-period
  // evidence is ever read as 신청가능/예약가능.
  function appendProofActions(container, event, { anchorClass = "button button-primary" } = {}) {
    const info = linkProof(event);
    container.appendChild(linkStatusNode(event));
    if (info.allowed) {
      const link = make("a", anchorClass, t("link.infoLabel"));
      link.href = info.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", t("link.infoAria", { name: event.name || t("common.event") }));
      container.appendChild(link);
    }
    // Booking CTA is withheld entirely once an event has ended — there is
    // nothing left to book, so neither the disclaimer nor an anchor belongs
    // here (an ended event must never expose apply-now).
    if (isEnded(event)) return;
    container.appendChild(bookingStatusNode(event));
    const booking = bookingProof(event);
    if (booking.allowed) {
      const bookingLink = make("a", anchorClass, t("link.bookingLabel"));
      bookingLink.href = booking.href;
      bookingLink.target = "_blank";
      bookingLink.rel = "noopener noreferrer";
      bookingLink.setAttribute("aria-label", t("link.bookingAria", { name: event.name || t("common.event") }));
      container.appendChild(bookingLink);
    }
  }

  function card(event, query) {
    const li = make("li", "card");
    const article = make("article");
    const heading = make("h3", "card-title");
    const open = make("button", "card-open", "");
    open.type = "button";
    open.innerHTML = highlight(event.name || t("card.unnamed"), query);
    open.setAttribute("aria-label", t("card.openDetailAria", { name: event.name || t("card.unnamed") }));
    open.addEventListener("click", () => showDetail(event));
    heading.appendChild(open);
    article.appendChild(heading);
    if (i18n() && i18n().getLanguage() === "en") {
      article.appendChild(make("p", "source-language-notice", t("detail.sourceLanguageNotice")));
    }

    const meta = make("div", "card-meta");
    meta.appendChild(make("span", "card-location", [vocab(event.sido, "sido"), event.sigungu].filter(Boolean).join(" ") || t("card.noLocation")));
    if (event.venue) meta.appendChild(make("span", "card-venue", event.venue));
    // event-date/is-ended are the contract's strikethrough hook — driven only
    // by isEnded() (actual event end), never by an application deadline.
    meta.appendChild(make("span", `card-date event-date${isEnded(event) ? " is-ended" : ""}`, dateLabel(event)));
    const dist = eventDist(event);
    if (dist != null) meta.appendChild(make("span", "card-distance", fmtDist(dist)));
    if (event.age) meta.appendChild(make("span", "card-age", t("card.ageTarget", { age: event.age })));
    meta.appendChild(make("span", "card-price", priceLabel(event)));
    meta.appendChild(renderBadges(event));
    article.appendChild(meta);
    if (isExperience(event) && event.experience_summary) {
      article.appendChild(make("p", "card-experience", t("card.experiencePrefix", { summary: event.experience_summary })));
    }
    if (hasFamilySignal(event) && event.family_evidence) {
      article.appendChild(make("p", "card-evidence", t("card.familyEvidencePrefix", { evidence: event.family_evidence })));
    }

    const actions = make("div", "card-actions");
    const detailButton = make("button", "button button-quiet", t("card.detailButton"));
    detailButton.type = "button";
    detailButton.addEventListener("click", () => showDetail(event));
    actions.appendChild(detailButton);
    appendProofActions(actions, event);
    article.appendChild(actions);
    li.appendChild(article);
    return li;
  }
  function popup(event) {
    const wrap = make("div", "map-popup");
    const open = make("button", "map-popup-title", "");
    open.type = "button";
    open.innerHTML = escHtml(event.name || t("card.unnamed"));
    open.addEventListener("click", () => showDetail(event));
    wrap.appendChild(open);
    if (i18n() && i18n().getLanguage() === "en") {
      wrap.appendChild(make("p", "source-language-notice", t("detail.sourceLanguageNotice")));
    }
    if (event.venue) wrap.appendChild(make("div", "map-popup-venue", event.venue));
    const metaNode = make("div", "map-popup-meta");
    metaNode.appendChild(document.createTextNode(`${vocab(event.sido, "sido") || t("card.noLocation")} · `));
    metaNode.appendChild(make("span", `event-date${isEnded(event) ? " is-ended" : ""}`, dateLabel(event)));
    wrap.appendChild(metaNode);
    if (event.address) wrap.appendChild(make("div", "map-popup-addr", event.address));
    const status = statusInfo(event);
    if (status) wrap.appendChild(badge(status.label, status.className));
    if (isFree(event.price)) wrap.appendChild(badge(t("badge.free"), "free"));
    const dist = eventDist(event);
    if (dist != null) wrap.appendChild(make("div", "map-popup-dist", fmtDist(dist)));
    const row = make("div", "map-popup-actions");
    const detail = make("button", "button button-quiet", t("card.detailButton"));
    detail.type = "button";
    detail.addEventListener("click", () => showDetail(event));
    row.appendChild(detail);
    appendProofActions(row, event);
    wrap.appendChild(row);
    return wrap;
  }

  function popupGroup(events) {
    if (events.length === 1) return popup(events[0]);
    const wrap = make("div", "map-popup-list");
    wrap.appendChild(make("strong", null, t("map.markerGroupTitle", { count: events.length })));
    if (i18n() && i18n().getLanguage() === "en") {
      wrap.appendChild(make("p", "source-language-notice", t("detail.sourceLanguageNotice")));
    }
    for (const event of events) {
      const row = make("div", "map-popup-event");
      const button = make("button", null, event.name || t("card.unnamed"));
      button.type = "button";
      button.addEventListener("click", () => {
        showDetail(event);
      });
      appendProofActions(row, event, { anchorClass: "map-source-link" });
      row.appendChild(button);
      const small = make("small");
      small.appendChild(document.createTextNode(`${vocab(event.sido, "sido") || t("card.noLocation")} · `));
      small.appendChild(make("span", `event-date${isEnded(event) ? " is-ended" : ""}`, dateLabel(event)));
      row.appendChild(small);
      wrap.appendChild(row);
    }
    return wrap;
  }


  function renderMap(rows) {
    if (!state.mapReady || !state.markers) return;
    state.map.invalidateSize({ pan: false });
    state.markers.clearLayers();
    const groups = new Map();
    for (const event of rows) {
      if (typeof event.lat !== "number" || typeof event.lng !== "number") continue;
      const key = `${event.lat},${event.lng}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    const points = [];
    state.markerGroups = [];
    for (const events of groups.values()) {
      const first = events[0];
      const marker = window.L.marker([first.lat, first.lng], {
        icon: eventIcon(events.length),
        title: events.length > 1 ? t("map.markerGroupTitle", { count: events.length }) : (first.name || t("map.markerFallbackTitle")),
      });
      marker._notchimaEventMarker = true;
      // Collapse mobile drawers and refresh popup pan options before Leaflet's
      // bindPopup click handler opens the popup. This prevents the first
      // auto-pan from measuring a drawer that is about to close.
      marker.on("click", () => {
        collapseMobileDrawersForPopup();
        const popup = marker.getPopup && marker.getPopup();
        if (popup) Object.assign(popup.options, eventPopupOptions());
      });
      marker.bindPopup(popupGroup(events), eventPopupOptions());
      marker.addTo(state.markers);
      points.push([first.lat, first.lng]);
      state.markerGroups.push({ marker, events });
    }
    state.mapPoints = points;
    if (points.length || !state.userPos) fitCurrentMap();
  }

  // Re-labels existing markers/popups in place after a language change —
  // never clears layers, never touches the map's pan/zoom/fitBounds. Avoids
  // the "rebuild/refit the map just to switch language" anti-pattern.
  function relabelMarkers() {
    if (!state.markerGroups || !state.markerGroups.length) return;
    for (const { marker, events } of state.markerGroups) {
      const title = events.length > 1 ? t("map.markerGroupTitle", { count: events.length }) : (events[0].name || t("map.markerFallbackTitle"));
      marker.options.title = title;
      if (marker.getElement && marker.getElement()) marker.getElement().setAttribute("title", title);
      const popup = marker.getPopup && marker.getPopup();
      if (popup && typeof popup.setContent === "function") {
        // Popup content is replaced in place so an open popup stays open. Its
        // own language-change relayout must not auto-pan the map.
        const autoPan = popup.options.autoPan;
        popup.options.autoPan = false;
        popup.setContent(popupGroup(events));
        popup.options.autoPan = autoPan;
      } else {
        marker.bindPopup(popupGroup(events), eventPopupOptions());
      }
    }
  }

  function renderRegionQuick(baseRows) {
    const root = $("regionQuick");
    const focus = captureChipFocus(root);
    clear(root);
    const counts = new Map();
    for (const event of baseRows) if (event.sido) counts.set(event.sido, (counts.get(event.sido) || 0) + 1);
    const regions = state.regions.filter((region) => counts.has(region.sido)).sort((a, b) => counts.get(b.sido) - counts.get(a.sido));
    if (!regions.length) {
      root.appendChild(make("span", "result-hint", t("region.emptyHint")));
      restoreChipFocus(root, focus);
      return;
    }
    for (const region of regions) {
      const item = make("div");
      item.setAttribute("role", "listitem");
      const button = make("button", "region-chip");
      button.type = "button";
      button.dataset.dim = "sido";
      button.dataset.value = region.sido;
      button.setAttribute("aria-pressed", $("sido").value === region.sido ? "true" : "false");
      button.appendChild(make("span", null, vocab(region.sido, "sido")));
      button.appendChild(make("strong", null, countLabel(counts.get(region.sido))));
      button.addEventListener("click", () => {
        $("sido").value = $("sido").value === region.sido ? "" : region.sido;
        applyFilters();
      });
      item.appendChild(button);
      root.appendChild(item);
    }
    restoreChipFocus(root, focus);
  }
  function renderActivityQuick(baseRows) {
    const root = $("activityQuick");
    const focus = captureChipFocus(root);
    clear(root);
    const counts = new Map();
    for (const event of baseRows) {
      if (!isExperience(event)) continue;
      for (const category of experienceCategories(event)) counts.set(category, (counts.get(category) || 0) + 1);
    }
    const categories = Array.from(counts.keys()).sort((a, b) => (EXPERIENCE_ORDER.indexOf(a) - EXPERIENCE_ORDER.indexOf(b)) || a.localeCompare(b, "ko"));
    if (!categories.length) {
      root.appendChild(make("span", "result-hint", t("activity.emptyHint")));
      restoreChipFocus(root, focus);
      return;
    }
    for (const category of categories) {
      const item = make("div");
      item.setAttribute("role", "listitem");
      const button = make("button", "region-chip activity-chip");
      button.type = "button";
      button.dataset.dim = "activity";
      button.dataset.value = category;
      button.setAttribute("aria-pressed", $("activity").value === category ? "true" : "false");
      button.dataset.activity = category;
      const artwork = category === "과학·탐구" ? "family-science-spot.webp"
        : category === "만들기·예술" ? "family-craft-spot.webp" : null;
      if (artwork) {
        const image = make("img", "activity-art");
        image.src = `./assets/${artwork}`;
        image.alt = "";
        image.width = 56;
        image.height = 56;
        image.loading = "lazy";
        button.appendChild(image);
      }
      const copy = make("span", "activity-copy");
      copy.appendChild(make("span", null, vocab(category, "activity")));
      copy.appendChild(make("strong", null, countLabel(counts.get(category))));
      button.appendChild(copy);
      button.addEventListener("click", () => {
        $("activity").value = $("activity").value === category ? "" : category;
        applyFilters();
      });
      item.appendChild(button);
      root.appendChild(item);
    }
    restoreChipFocus(root, focus);
  }

  function sortRows(rows, rankOf) {
    const rank = rankOf || new Map();
    let seq = 0;
    const order = new Map();
    for (const event of rows) if (!order.has(event)) order.set(event, seq++);
    return rows.slice().sort((a, b) => {
      const endedDelta = Number(isEnded(a)) - Number(isEnded(b));
      if (endedDelta) return endedDelta;
      const experienceDelta = Number(isExperience(b)) - Number(isExperience(a));
      if (experienceDelta) return experienceDelta;
      if (state.sortMode === "distance" && state.userPos) {
        const da = eventDist(a);
        const db = eventDist(b);
        const delta = (da == null ? Infinity : da) - (db == null ? Infinity : db);
        if (delta) return delta;
      } else if (state.sortMode === "upcoming") {
        const delta = String(a.start_date || "9999").localeCompare(String(b.start_date || "9999"));
        if (delta) return delta;
      } else {
        const delta = (rank.get(a) ?? order.get(a) ?? 0) - (rank.get(b) ?? order.get(b) ?? 0);
        if (delta) return delta;
      }
      return 0;
    });
  }

  function measurePageSize() {
    // 화면 높이에 맞는 페이지 크기: 렌더된 결과 패널의 내부 스크롤 영역만 측정.
    const scroll = $("resultsScroll");
    const rect = scroll && typeof scroll.getBoundingClientRect === "function" ? scroll.getBoundingClientRect() : null;
    if (!rect || rect.height <= 0) return null;
    return Math.max(5, Math.min(12, Math.floor(rect.height / 140)));
  }

  function pageSize() {
    if (state.lastPageSize > 0) return state.lastPageSize;
    const measured = measurePageSize();
    state.lastPageSize = measured == null ? 5 : measured;
    return state.lastPageSize;
  }

  function refreshPageSize(options) {
    const previous = pageSize();
    const next = measurePageSize();
    if (next == null || next === previous) return false;
    const firstVisible = state.page * previous;
    state.lastPageSize = next;
    if (!options || options.preservePage !== false) {
      state.page = Math.max(0, Math.floor(firstVisible / next));
    }
    return true;
  }

  function refreshPageSizeAndRender(options) {
    if (!refreshPageSize(options)) return false;
    if (state.filteredRows.length) {
      render(state.filteredRows, { updateMap: false, resetScroll: false });
    }
    return true;
  }

  function render(rows, options) {
    if (!state.dataReady) return;
    $("count").textContent = String(rows.length);
    if ($("heroCount")) $("heroCount").textContent = String(rows.length);
    const size = pageSize();
    const pages = Math.max(1, Math.ceil(rows.length / size));
    if (state.page > pages - 1) state.page = pages - 1;
    if (state.page < 0) state.page = 0;
    const start = state.page * size;
    const shown = rows.slice(start, start + size);
    clear($("list"));
    for (const event of shown) $("list").appendChild(card(event, state.lastQuery));
    if (!options || options.resetScroll !== false) {
      const scroll = $("resultsScroll");
      if (scroll) scroll.scrollTop = 0;
    }
    setHidden("empty", rows.length > 0);
    renderPager(rows.length, start, shown.length, pages);
    const ended = rows.filter(isEnded).length;
    const unmapped = rows.filter((e) => typeof e.lat !== "number" || typeof e.lng !== "number").length;
    const note = ended ? t("results.hintEnded", { count: ended }) : t("results.hintCheckSource");
    const mapNote = unmapped ? t("results.hintUnmapped", { count: unmapped }) : "";
    const sortNote = state.sortMode === "distance" && state.userPos ? t("results.hintDistanceSort") : "";
    $("resultHint").textContent = `${t("results.hintCount", { count: rows.length })} ${note}${mapNote}${sortNote}`;
    if (!options || options.updateMap !== false) renderMap(rows);
  }

  function renderPager(total, start, shownCount, pages) {
    const nav = $("pager");
    if (!nav) return;
    nav.hidden = total === 0;
    const prev = $("pagePrev");
    const next = $("pageNext");
    const info = $("pageInfo");
    if (prev) {
      prev.disabled = state.page <= 0;
      prev.setAttribute("aria-label", t("pager.prevAria"));
    }
    if (next) {
      next.disabled = state.page >= pages - 1;
      next.setAttribute("aria-label", t("pager.nextAria"));
    }
    if (info) {
      info.textContent = total === 0
        ? ""
        : t("pager.info", { page: state.page + 1, pages, total, from: start + 1, to: start + shownCount });
    }
  }

  function gotoPage(delta) {
    state.page += delta;
    render(state.filteredRows, { updateMap: false });
    const list = $("list");
    if (list && typeof list.scrollIntoView === "function") {
      const reduced = typeof window.matchMedia === "function"
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      list.scrollIntoView({ block: "start", behavior: reduced ? "auto" : "smooth" });
    }
  }

  function renderSubFacets(rows, filters) {
    const root = $("subFacets");
    if (!root) return;
    const focus = captureChipFocus(root);
    clear(root);
    const groups = [];
    if (!filters.theme) {
      const counts = new Map();
      for (const event of rows) for (const theme of event.themes || []) counts.set(theme, (counts.get(theme) || 0) + 1);
      groups.push({ dim: "theme", label: t("subfacets.theme"), items: topN(counts, 5) });
    }
    if (!filters.sido) {
      const counts = new Map();
      for (const event of rows) if (event.sido) counts.set(event.sido, (counts.get(event.sido) || 0) + 1);
      groups.push({ dim: "sido", label: t("subfacets.sido"), items: topN(counts, 5) });
    }
    if (!filters.age) {
      const counts = new Map();
      for (const event of rows) for (const age of event.age_bands || []) counts.set(age, (counts.get(age) || 0) + 1);
      groups.push({ dim: "age", label: t("subfacets.age"), items: topN(counts, 5) });
    }
    if (!filters.activity) {
      const counts = new Map();
      for (const event of rows) {
        if (!isExperience(event)) continue;
        for (const category of experienceCategories(event)) counts.set(category, (counts.get(category) || 0) + 1);
      }
      groups.push({ dim: "activity", label: t("subfacets.activity"), items: topN(counts, 5) });
    }
    const visible = groups.filter((group) => group.items.length > 0);
    setHidden("subFacetsSection", visible.length === 0);
    for (const group of visible) {
      const box = make("div", "subfacet-group");
      box.appendChild(make("span", "subfacet-label", group.label));
      for (const [value, count] of group.items) {
        const chip = make("button", "region-chip subfacet-chip");
        chip.type = "button";
        chip.dataset.dim = group.dim;
        chip.dataset.value = value;
        chip.appendChild(make("span", null, vocab(value, group.dim)));
        chip.appendChild(make("strong", null, countLabel(count)));
        chip.addEventListener("click", () => {
          const control = $(group.dim === "sido" ? "sido" : group.dim);
          if (control) {
            if (control.tagName === "SELECT"
              && !Array.from(control.options).some((option) => option.value === value)) {
              const option = make("option", null, t("filter.unlistedOption", { value: vocab(value, group.dim) }));
              option.value = value;
              control.appendChild(option);
            }
            control.value = value;
          }
          applyFilters();
        });
        box.appendChild(chip);
      }
      root.appendChild(box);
    }
    restoreChipFocus(root, focus);
  }

  function topN(counts, n) {
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, n);
  }

  function applyFilters(options) {
    if (!state.dataReady) return;
    const filters = currentFilters();
    state._search = null;
    const preservePage = Boolean(options && options.preservePage === true);
    if (!preservePage) {
      state.page = 0;
      refreshPageSize({ preservePage: false });
    }
    state.lastQuery = filters.q;
    const activityBaseRows = filterRows({ ...filters, activity: "" }, true);
    const baseRows = filterRows(filters, false);
    const inScope = filters.sido ? baseRows.filter((event) => event.sido === filters.sido) : baseRows;
    const rankOf = new Map();
    if (state._search) state._search.items.forEach((event, idx) => rankOf.set(event, idx));
    const rows = sortRows(inScope, rankOf);
    state.filteredRows = rows;
    renderRegionQuick(baseRows);
    renderActivityQuick(activityBaseRows);
    renderSubFacets(inScope, filters);
    updateSortUI();
    render(rows, options);
    writeURL(filters);
  }

  function openDialog(dialog, returnTarget) {
    const target = returnTarget || document.activeElement;
    dialog.__skipFocusRestore = false;
    dialog.showModal();
    const close = dialog.querySelector(".close");
    if (close) close.focus();
    dialog.onclose = () => {
      const anotherDialogOpen = document.querySelector && document.querySelector("dialog[open]");
      const skip = dialog.__skipFocusRestore || anotherDialogOpen;
      dialog.__skipFocusRestore = false;
      if (skip) return;
      if (target && typeof target.focus === "function") target.focus();
    };
  }

  function addClose(dialog) {
    const close = make("button", "close", t("detail.closeButton"));
    close.type = "button";
    close.setAttribute("aria-label", t("detail.closeAria"));
    close.addEventListener("click", () => dialog.close());
    dialog.appendChild(close);
  }

  function showDetail(event, returnTarget) {
    const dialog = $("detail");
    clear(dialog);
    addClose(dialog);
    const title = make("h2", null, event.name || t("detail.fallbackTitle"));
    title.id = "detailTitle";
    dialog.appendChild(title);
    // Official name/description/venue/address/schedule numbers are source
    // Korean with no official translation — never machine-translated. English
    // UI gets an explicit notice instead of silently relabeling them.
    if (i18n() && i18n().getLanguage() === "en") {
      dialog.appendChild(make("p", "source-language-notice", t("detail.sourceLanguageNotice")));
    }
    if (event.description) dialog.appendChild(make("p", null, event.description));
    else dialog.appendChild(make("p", null, t("detail.noDescription")));
    if (isExperience(event)) {
      const experience = make("section", "experience-detail");
      experience.appendChild(make("strong", null, t("detail.experienceTitle")));
      if (experienceCategories(event).length) {
        const categories = experienceCategories(event).map((category) => vocab(category, "activity")).join(" · ");
        experience.appendChild(make("p", null, t("detail.experienceCategories", { categories })));
      }
      if (event.experience_summary) experience.appendChild(make("p", null, event.experience_summary));
      dialog.appendChild(experience);
    }
    if (hasFamilySignal(event) && event.family_evidence) {
      dialog.appendChild(make("p", "card-evidence", t("card.familyEvidencePrefix", { evidence: event.family_evidence })));
    }
    // 원천이 제공한 사실만 보존한다 — 비어 있으면 "정보 없음"으로 명시하고
    // 추측으로 채우지 않는다(AC-F2.11: 목록/지도 상세 == 공유용 정적 상세).
    const meta = make("div", "detail-meta");
    meta.appendChild(make("span", null, t("detail.region", { value: [vocab(event.sido, "sido"), event.sigungu].filter(Boolean).join(" ") || t("common.noInfo") })));
    meta.appendChild(make("span", null, t("detail.venue", { value: venueLabel(event) })));
    const scheduleSpan = make("span");
    scheduleSpan.appendChild(document.createTextNode(t("detail.schedule")));
    scheduleSpan.appendChild(make("span", `event-date${isEnded(event) ? " is-ended" : ""}`, dateLabel(event)));
    meta.appendChild(scheduleSpan);
    meta.appendChild(make("span", null, t("detail.age", { value: event.age || t("common.noInfo") })));
    meta.appendChild(make("span", null, t("detail.price", { value: priceLabel(event) })));
    meta.appendChild(make("span", null, t("detail.duration", { value: event.duration_text || t("common.noInfo") })));
    const status = statusInfo(event);
    if (status) {
      const statusLine = make("span");
      statusLine.appendChild(make("span", null, t("status.prefix")));
      statusLine.appendChild(badge(status.label, status.className));
      meta.appendChild(statusLine);
    }
    meta.appendChild(make("span", null, t("detail.organizer", { value: event.organizer || t("common.noInfo") })));
    if (event.application_end) meta.appendChild(make("span", null, t("detail.applicationDeadline", { value: localDate(event.application_end) })));
    dialog.appendChild(meta);
    dialog.appendChild(make("p", null, `${t("detail.source", { value: event.source || t("common.noInfo") })}${isDemoEvent(event) ? t("detail.demoSuffix") : ""}`));
    const actions = make("div", "dialog-actions");
    appendProofActions(actions, event);
    const local = make("a", "button button-quiet", t("detail.shareLink"));
    local.href = localDetailUrl(event);
    local.rel = "noopener";
    actions.appendChild(local);
    dialog.appendChild(actions);
    openDialog(dialog, returnTarget);
  }

  function recommend(rows) {
    return rows.filter((event) => !isEnded(event)).map((event) => {
      let score = 0;
      const reasons = [];
      if (isExperience(event)) { score += 2; reasons.push(t("ai.reason.experience")); }
      if (hasFamilySignal(event)) { score += 3; reasons.push(t("ai.reason.family")); }
      if (isFree(event.price)) { score += 1; reasons.push(t("ai.reason.free")); }
      // Display-only wording: confirms the application window + booking link
      // proof, never a purchase/reservation claim (no sales/seat feed exists).
      if (isApplyable(event)) { score += 2; reasons.push(t("ai.reason.applyable")); }
      if ((event.themes || []).length) { score += 1; reasons.push(t("ai.reason.theme", { theme: vocab(event.themes[0], "theme") })); }
      return { event, score, reasons };
    }).sort((a, b) => b.score - a.score).slice(0, 5);
  }

  function showAI() {
    const dialog = $("aiDialog");
    clear(dialog);
    addClose(dialog);
    const title = make("h2", null, t("ai.title"));
    title.id = "aiTitle";
    dialog.appendChild(title);
    dialog.appendChild(make("p", null, t("ai.intro")));
    if (i18n() && i18n().getLanguage() === "en") {
      dialog.appendChild(make("p", "source-language-notice", t("detail.sourceLanguageNotice")));
    }
    const recs = recommend(state.filteredRows);
    if (!recs.length) {
      dialog.appendChild(make("p", null, t("ai.empty")));
    } else {
      const list = make("ol", "recommendations");
      for (const item of recs) {
        const li = make("li");
        const open = make("button", "rec-open", item.event.name || t("card.unnamed"));
        open.type = "button";
        open.addEventListener("click", () => {
          dialog.__skipFocusRestore = true;
          dialog.close();
          showDetail(item.event, $("aiBtn"));
        });
        li.appendChild(open);
        li.appendChild(make("span", null, ` · ${vocab(item.event.sido, "sido") || t("card.noLocation")}`));
        li.appendChild(make("br"));
        li.appendChild(make("small", null, item.reasons.join(" · ") || t("ai.matchDefault")));
        list.appendChild(li);
      }
      dialog.appendChild(list);
    }
    openDialog(dialog, $("aiBtn"));
  }

  function updateSortUI() {
    const modes = [["relevance", "sortRel"], ["upcoming", "sortNew"], ["distance", "sortDist"]];
    for (const [mode, id] of modes) {
      const btn = $(id);
      if (!btn) continue;
      btn.setAttribute("aria-pressed", state.sortMode === mode ? "true" : "false");
      if (mode === "distance") btn.disabled = !state.userPos;
    }
    const hint = $("locateHint");
    if (hint) {
      hint.textContent = state.userPos
        ? t("map.locateHint.active")
        : t("map.locateHint.default");
    }
  }

  function locateUser() {
    const btn = $("locateBtn");
    const hint = $("locateHint");
    if (!state.mapReady) {
      if (hint) hint.textContent = t("map.locateHint.unavailable");
      return;
    }
    if (!("geolocation" in navigator)) {
      if (hint) hint.textContent = t("map.locateHint.unsupported");
      return;
    }
    if (btn) btn.disabled = true;
    if (hint) hint.textContent = t("map.locateHint.checking");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userPos = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        if (state.userMarker && state.map) state.map.removeLayer(state.userMarker);
        state.userMarker = window.L.circleMarker([state.userPos.lat, state.userPos.lng], {
          radius: 9, color: "#fff", weight: 3, fillColor: "#24755c", fillOpacity: 1,
        }).addTo(state.map).bindPopup(t("map.userPopup"));
        state.map.setView([state.userPos.lat, state.userPos.lng], 14);
        state.sortMode = "distance";
        if (btn) btn.disabled = false;
        applyFilters({ updateMap: false });
      },
      () => {
        if (btn) btn.disabled = false;
        if (hint) hint.textContent = t("map.locateHint.failed");
      },
      { timeout: 8000, maximumAge: 600000 },
    );
  }
  function bindRetry() {
    if (state.retryWired) return;
    const retry = $("retry");
    if (!retry) return;
    state.retryWired = true;
    retry.addEventListener("click", () => {
      const engine = i18n();
      if (engine && typeof engine.isLoaded === "function" && !engine.isLoaded()) {
        if (window.location && typeof window.location.reload === "function") window.location.reload();
        return;
      }
      loadData();
    });
  }

  // Compact phone layouts include narrow portrait and short landscape; the
  // same classification drives initial collapse and drawer exclusivity.
  function isMobileViewport() {
    return typeof window.matchMedia === "function"
      && window.matchMedia("(max-width: 640px), (max-height: 560px) and (min-width: 641px)").matches;
  }

  // Module-scope (not a wire()-local closure) so languagechange and the map's
  // popupopen handler can both refresh/collapse the results disclosure.
  function syncResultsDisclosure() {
    const resultsPanel = $("resultsPanel");
    const resultsToggle = $("resultsToggle");
    if (!resultsPanel || !resultsToggle) return;
    const collapsed = resultsPanel.dataset.collapsed === "true";
    setControlLabel("resultsToggle", collapsed ? t("results.toggleExpand") : t("results.toggleCollapse"));
    resultsToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  // On mobile, opening one of {filter conditions, results drawer} collapses
  // the other first — they stay mutually exclusive so neither covers the
  // bottom search dock nor the map. Desktop leaves both independently
  // openable. Never steals focus here (that stays Escape-key-only); never
  // touches the map's own pan/zoom.
  function collapseMobileDrawersForPopup() {
    if (!isMobileViewport()) return;
    const filterMenu = $("filterMenu");
    if (filterMenu && filterMenu.open) filterMenu.open = false;
    const resultsPanel = $("resultsPanel");
    if (resultsPanel && resultsPanel.dataset.collapsed !== "true") {
      resultsPanel.dataset.collapsed = "true";
      syncResultsDisclosure();
    }
    scheduleMapViewportRefresh();
  }

  function wire() {
    if (state.wired) return;
    state.wired = true;
    let searchTimer;
    const filters = $("searchDock");
    if (filters) filters.addEventListener("submit", (event) => {
      event.preventDefault();
      applyFilters();
    });
    $("q").addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(applyFilters, 120);
    });
    ["sido", "theme", "age", "activity", "from", "to"].forEach((id) => $(id).addEventListener("input", applyFilters));
    ["applyable", "kids", "freeOnly"].forEach((id) => $(id).addEventListener("change", applyFilters));
    $("reset").addEventListener("click", () => {
      ["q", "sido", "theme", "age", "activity", "from", "to"].forEach((id) => { $(id).value = ""; });
      $("applyable").checked = false;
      $("kids").checked = true;
      $("freeOnly").checked = false;
      applyFilters();
    });
    $("aiBtn").addEventListener("click", showAI);
    const sortBind = [["sortRel", "relevance"], ["sortNew", "upcoming"], ["sortDist", "distance"]];
    for (const [id, mode] of sortBind) {
      const btn = $(id);
      if (btn) btn.addEventListener("click", () => { state.sortMode = mode; state.page = 0; applyFilters({ updateMap: false }); });
    }
    const locate = $("locateBtn");
    if (locate) locate.addEventListener("click", locateUser);
    const prev = $("pagePrev");
    if (prev) prev.addEventListener("click", () => gotoPage(-1));
    const next = $("pageNext");
    if (next) next.addEventListener("click", () => gotoPage(1));

    const filterMenu = $("filterMenu");
    const filterToggle = $("filterToggle");
    const restoreFilterFocus = () => {
      if (filterToggle && typeof filterToggle.focus === "function") filterToggle.focus();
    };
    if (filterMenu) {
      filterMenu.addEventListener("toggle", () => {
        if (filterMenu.open && isMobileViewport()) {
          const resultsPanel = $("resultsPanel");
          if (resultsPanel && resultsPanel.dataset.collapsed !== "true") {
            resultsPanel.dataset.collapsed = "true";
            syncResultsDisclosure();
          }
        }
        scheduleMapViewportRefresh();
        refreshPageSizeAndRender();
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || event.defaultPrevented || !filterMenu.open) return;
        if (document.querySelector && document.querySelector("dialog[open]")) return;
        event.preventDefault();
        filterMenu.open = false;
        restoreFilterFocus();
        scheduleMapViewportRefresh();
      });
      document.addEventListener("pointerdown", (event) => {
        if (!filterMenu.open || filterMenu.contains(event.target)) return;
        filterMenu.open = false;
        const mapNode = $("map");
        if (mapNode && typeof mapNode.contains === "function" && mapNode.contains(event.target)) {
          state.mapFitPending = false;
          state.lastAutoFitCenter = null;
          state.lastAutoFitZoom = null;
        }
        scheduleMapViewportRefresh();
      });
    }

    const resultsPanel = $("resultsPanel") || (document.querySelector && document.querySelector(".results"));
    const resultsToggle = $("resultsToggle");
    if (resultsPanel && resultsToggle) {
      // Mobile defaults to a collapsed results drawer (icon-opened), distinct
      // from desktop's default-open panel — a one-time init correction since
      // the drawer markup starts data-collapsed="false" for every viewport.
      if (isMobileViewport() && resultsPanel.dataset.collapsed !== "true") {
        resultsPanel.dataset.collapsed = "true";
      }
      // Label/aria-expanded text sync is deferred to localizeStaticChrome()
      // (called once the i18n catalog is ready) so this never shows a raw
      // translation key before the catalog loads — the static HTML's baked-in
      // Korean label is already correct for the ko default in the meantime.
      scheduleMapViewportRefresh();
      resultsToggle.addEventListener("click", () => {
        const collapsed = resultsPanel.dataset.collapsed !== "true";
        resultsPanel.dataset.collapsed = collapsed ? "true" : "false";
        if (!collapsed && isMobileViewport()) {
          // Expanding the results drawer closes the filter conditions popup
          // first — mobile keeps exactly one of the two open at a time.
          const filterMenu = $("filterMenu");
          if (filterMenu && filterMenu.open) filterMenu.open = false;
        }
        syncResultsDisclosure();
        scheduleMapViewportRefresh();
        refreshPageSizeAndRender();
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || event.defaultPrevented || !isMobileViewport()) return;
        if (document.querySelector && document.querySelector("dialog[open]")) return;
        if (resultsPanel.dataset.collapsed === "true") return;
        event.preventDefault();
        resultsPanel.dataset.collapsed = "true";
        syncResultsDisclosure();
        if (typeof resultsToggle.focus === "function") resultsToggle.focus();
        scheduleMapViewportRefresh();
        refreshPageSizeAndRender();
      });
    }

    window.addEventListener("popstate", () => { restoreFromURL(); applyFilters(); });
    let resizeFrame;
    pageSize();
    const raf = typeof window.requestAnimationFrame === "function" ? window.requestAnimationFrame : window.setTimeout;
    const cancelRaf = typeof window.cancelAnimationFrame === "function" ? window.cancelAnimationFrame : window.clearTimeout;
    window.addEventListener("resize", () => {
      cancelRaf(resizeFrame);
      resizeFrame = raf(() => {
        resizeFrame = 0;
        refreshMapViewport();
        refreshPageSizeAndRender();
      });
    });
  }

  function renderFreshness(updated) {
    const builtAt = updated && updated.generated_at;
    const builtMs = builtAt ? Date.parse(String(builtAt)) : NaN;
    const staleDays = Number.isFinite(builtMs) ? Math.floor((Date.now() - builtMs) / 86400000) : null;
    const staleSuffix = staleDays != null && staleDays >= 3 ? t("freshness.staleSuffix", { days: staleDays }) : "";
    $("freshness").textContent = builtAt
      ? `${t("freshness.builtLabel", { time: formatTimestamp(builtAt), count: state.events.length })}${staleSuffix}`
      : t("freshness.readyOnly", { count: state.events.length });
    $("freshness").title = builtAt ? t("freshness.buildTooltip", { time: builtAt }) : "";
    const sourceNode = $("sourceStatus");
    const modeKeys = { offline: "source.modeLabel.offline", live: "source.modeLabel.live", mixed: "source.modeLabel.mixed" };
    const mode = t(modeKeys[String(updated && updated.source_mode || "offline")] || "source.modeLabel.unknown");
    const parts = [t("source.collectionMode", { mode }), t("source.schedule")];
    const lastSuccess = formatTimestamp(updated && updated.last_success_at);
    parts.push(lastSuccess ? t("source.lastSuccess", { time: lastSuccess }) : t("source.lastSuccessNone"));
    const sources = updated && updated.sources && typeof updated.sources === "object" ? Object.entries(updated.sources) : [];
    const recordModeKey = (info) => (info && info.mode === "live" ? "source.recordMode.live" : info && info.mode === "error" ? "source.recordMode.error" : "source.recordMode.fixture");
    const sourceModes = sources.map(([name, info]) => `${name} ${t(recordModeKey(info))}`);
    if (sourceModes.length) parts.push(t("source.perSource", { list: sourceModes.join(" · ") }));
    const errorCount = Number(updated && updated.collection_error_count || 0);
    if (errorCount) parts.push(t("source.errorCount", { count: errorCount }));
    sourceNode.textContent = parts.join(" · ");
    const collectionErrors = updated && Array.isArray(updated.collection_errors) ? updated.collection_errors : [];
    sourceNode.title = collectionErrors.join(" · ") || sourceNode.textContent;
  }

  async function loadData() {
    setDataReady(false);
    setHidden("loadError", true);
    setHidden("loading", false);
    disposeState();
    try {
      const [events, facets, regions, updated] = await Promise.all([
        getJSON("events.json"), getJSON("facets.json"), getJSON("regions.json"), getJSON("updated.json"),
      ]);
      state.events = Array.isArray(events) ? events : [];
      state.facets = facets || {};
      state.regions = Array.isArray(regions) ? regions : [];
      state.lastUpdated = updated;
      state.fuse = window.Fuse ? new window.Fuse(state.events, { keys: ["name", "description", "sido", "sigungu", "themes", "experience_summary", "experience_categories", "family_evidence"], threshold: .4, ignoreLocation: true }) : null;
      buildFilters();
      restoreFromURL();
      setDataReady(true);
      applyFilters();
      renderFreshness(updated);
      setHidden("loading", true);
    } catch (error) {
      setDataReady(false);
      setHidden("loading", true);
      setHidden("loadError", false);
      if (isMobileViewport()) {
        const filterMenu = $("filterMenu");
        if (filterMenu) filterMenu.open = false;
        const resultsPanel = $("resultsPanel");
        if (resultsPanel) {
          resultsPanel.dataset.collapsed = "false";
          syncResultsDisclosure();
        }
      }
      $("freshness").textContent = t("freshness.failed");
      $("resultHint").textContent = error && error.message ? error.message : t("error.loadGeneric");
    }
  }
  function localizeDocumentTitle() {
    if (typeof document !== "undefined") document.title = t("brand.documentTitle");
  }

  function localizeStaticChrome() {
    // Apply catalog-backed static hooks first; stateful controls below then
    // restore their current expanded/collapsed or active labels.
    if (i18n()) i18n().apply(document);
    localizeDocumentTitle();
    localizeNativeMapControls();
    setControlLabel("locateBtn", t("map.locate"));
    syncResultsDisclosure();
    const filterToggle = $("filterToggle");
    if (filterToggle) {
      const openSpan = filterToggle.querySelector(".filter-label-open");
      const closeSpan = filterToggle.querySelector(".filter-label-close");
      const hintSpan = filterToggle.querySelector(".filter-toggle-hint");
      if (openSpan) openSpan.textContent = t("filter.openLabel");
      if (closeSpan) closeSpan.textContent = t("filter.closeLabel");
      if (hintSpan) hintSpan.textContent = t("filter.toggleHint");
    }
    updateSortUI();
  }

  // Fires on #languageToggle clicks (bound once, inside i18n.js) and on
  // popstate-restored lang= values. Re-renders text in place — filters,
  // page, map view/zoom, and any open popup/dialog are left untouched; the
  // map is relabeled, never rebuilt or refit, just to switch language.
  function onLanguageChange() {
    localizeStaticChrome();
    if (!state.dataReady) return;
    buildFilters();
    applyFilters({ updateMap: false, resetScroll: false, preservePage: true });
    relabelMarkers();
    if (state.lastUpdated) renderFreshness(state.lastUpdated);
  }

  function showLocaleBootstrapFailure() {
    setHidden("loading", true);
    setHidden("loadError", false);
    const loadErrorBody = $("loadError") && $("loadError").querySelector("p");
    if (loadErrorBody) loadErrorBody.textContent = "잠시 후 다시 시도해 주세요. Could not load language data. Please reload.";
    const filterMenu = $("filterMenu");
    if (isMobileViewport() && filterMenu) filterMenu.open = false;
    const resultsPanel = $("resultsPanel");
    if (resultsPanel) resultsPanel.dataset.collapsed = "false";
    const resultsToggle = $("resultsToggle");
    if (resultsToggle) resultsToggle.setAttribute("aria-expanded", "true");
    const freshness = $("freshness");
    if (freshness) freshness.textContent = "번역 설정을 불러오지 못했어요.";
    const resultHint = $("resultHint");
    if (resultHint) resultHint.textContent = "잠시 후 다시 시도해 주세요.";
  }

  async function main() {
    bindRetry();
    const engine = i18n();
    if (engine) {
      await engine.ready;
      if (typeof engine.isLoaded === "function" && !engine.isLoaded()) {
        showLocaleBootstrapFailure();
        return;
      }
    }
    localizeStaticChrome();
    window.addEventListener("notchima:languagechange", onLanguageChange);
    initMap();
    wire();
    await loadData();
  }

  document.addEventListener("DOMContentLoaded", main);
})();
