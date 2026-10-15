/* 놓치마 가족 행사 탐색 — 정적 JSON을 읽어 필터·지도·목록을 함께 갱신한다. */
(function () {
  "use strict";

  const DATA = "./data/";
  const state = {
    events: [], regions: [], facets: {}, fuse: null,
    map: null, markers: null, userMarker: null, mapReady: false,
    filteredRows: [], lastFocus: null, wired: false,
    page: 0, sortMode: "relevance", userPos: null, lastQuery: "",
    _search: null,
  };

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
    return new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp));
  }

  function linkProof(event, now = Date.now()) {
    const status = String(event && event.link_status || "unchecked").toLowerCase();
    const checkedAt = Date.parse(String(event && event.link_checked_at || ""));
    const rawHref = String(event && event.link_url || "");
    const href = safeVerifiedUrl(rawHref);
    const unsafeHref = Boolean(rawHref && !href);
    const fresh = status === "verified" && href && Number.isFinite(checkedAt) && checkedAt <= now && now - checkedAt <= LINK_MAX_AGE_MS;
    let label = "연결 미확인";
    if (status === "blocked" || unsafeHref) label = "연결 차단";
    else if (status === "dead") label = "연결 끊김";
    else if (status === "verified" && !fresh) label = "확인 만료";
    else if (fresh) label = "연결 확인";
    const reason = String(event && event.link_reason || "").trim();
    return { allowed: Boolean(fresh), href: fresh ? href : "", label, reason, checked: formatTimestamp(event && event.link_checked_at) };
  }

  function linkStatusNode(event) {
    const proof = linkProof(event);
    const visible = `${proof.label} · ${proof.checked ? `확인 ${proof.checked}` : "확인 시각 없음"}${proof.reason ? ` · ${proof.reason}` : ""}`;
    const node = make("span", `link-status${proof.allowed ? " verified" : ""}`, visible);
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

  function setHidden(id, hidden) {
    const node = $(id);
    if (node) node.hidden = hidden;
  }

  function disposeState() {
    // 동적 메모리 정리: 이전 인덱스·마커·검색 캐시·페이지 상태를 버린다.
    state.fuse = null;
    state._search = null;
    state.page = 0;
    if (state.markers) state.markers.clearLayers();
    if (state.userMarker && state.map) {
      state.map.removeLayer(state.userMarker);
      state.userMarker = null;
    }
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
    return km < 1 ? `약 ${Math.round(km * 1000)}m` : `약 ${km.toFixed(1)}km`;
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
    if (!response.ok) throw new Error(`${name} 로드 실패 (${response.status})`);
    return response.json();
  }

  function initMap() {
    if (!$("map")) return;
    if (!window.L) {
      setHidden("mapFallback", false);
      return;
    }
    try {
      state.map = window.L.map("map", { scrollWheelZoom: false }).setView([36.5, 127.8], 7);
      window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: "© OpenStreetMap",
      }).addTo(state.map);
      state.markers = window.L.layerGroup().addTo(state.map);
      state.mapReady = true;
    } catch (_) {
      state.map = null;
      setHidden("mapFallback", false);
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

  function buildFilters() {
    fillSelect($("sido"), state.regions.map((region) => ({
      value: region.sido, label: `${region.sido} (${region.count})`,
    })));
    const themes = Object.entries(state.facets.theme || {}).sort((a, b) => b[1] - a[1]);
    fillSelect($("theme"), themes.map(([theme, count]) => ({ value: theme, label: `${theme} (${count})` })));
    const ageValues = new Map(Object.keys(state.facets.age_band || {}).map((age) => [age, 0]));
    const activityValues = new Map();
    for (const event of state.events) {
      for (const age of new Set(event.age_bands || [])) ageValues.set(age, (ageValues.get(age) || 0) + 1);
      if (isExperience(event)) for (const category of experienceCategories(event)) activityValues.set(category, (activityValues.get(category) || 0) + 1);
    }
    fillSelect($("age"), Array.from(ageValues.entries()).map(([age, count]) => ({
      value: age, label: count ? `${age} (${count})` : age,
    })));
    const activities = Array.from(activityValues.entries()).sort((a, b) => (EXPERIENCE_ORDER.indexOf(a[0]) - EXPERIENCE_ORDER.indexOf(b[0])) || b[1] - a[1]);
    fillSelect($("activity"), activities.map(([category, count]) => ({ value: category, label: `${category} (${count})` })));
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
        const option = make("option", null, `${value} (현재 목록 없음)`);
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

  function isApplyable(event) {
    if (event.status !== "Open" || isEnded(event)) return false;
    const start = dpart(event.application_start);
    const end = dpart(event.application_end);
    const now = today();
    if (start && now < start) return false;
    if (end && now > end) return false;
    return true;
  }

  function statusInfo(event) {
    if (isEnded(event)) return { label: "종료", className: "ended" };
    if (isApplyable(event)) return { label: "신청가능", className: "open" };
    const applicationEnd = dpart(event.application_end);
    if (event.status === "Open" && applicationEnd && applicationEnd < today()) {
      return { label: "신청마감", className: "closed" };
    }
    const labels = { 신청전: "신청전", 오픈: "오픈", 종료: "종료", 마감임박: "마감임박", 마감: "마감" };
    const label = labels[event.display_status] || (event.status === "Open" ? "신청전" : "");
    const classNames = { 신청전: "before", 오픈: "open", 종료: "ended", 마감임박: "imminent", 마감: "closed" };
    return label ? { label, className: classNames[label] || "before" } : null;
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
    if (isFree(event.price)) wrap.appendChild(badge("무료", "free"));
    const status = statusInfo(event);
    if (status) wrap.appendChild(badge(status.label, status.className));
    if (event.event_type) wrap.appendChild(badge(event.event_type));
    if (isExperience(event)) wrap.appendChild(badge("체험", "experience"));
    if (isDemoEvent(event)) wrap.appendChild(badge("데모", "discovered"));
    if (event.verification === "web-discovered") {
      const confidence = event.confidence != null ? ` ${Math.round(event.confidence * 100)}%` : "";
      const found = badge(`웹 발견${confidence}`, "discovered");
      found.title = "웹검색으로 발견된 후보입니다. 출처 링크에서 내용을 확인하세요.";
      wrap.appendChild(found);
    }
    for (const category of experienceCategories(event)) wrap.appendChild(badge(category, "experience-category"));
    for (const theme of event.themes || []) wrap.appendChild(badge(theme));
    return wrap;
  }

  function dateLabel(event) {
    const start = dpart(event.start_date);
    const end = dpart(event.end_date);
    return end && end !== start ? `${start} ~ ${end}` : start || "일정 정보 없음";
  }

  function priceLabel(event) {
    if (isFree(event.price)) return "무료";
    if (event.price == null || event.price === "") return "가격 정보 없음";
    return typeof event.price === "number" ? `${event.price.toLocaleString("ko-KR")}원` : String(event.price);
  }

  function localDetailUrl(event) {
    return `events/${safeId(event.id)}.html`;
  }

  function card(event, query) {
    const li = make("li", "card");
    const article = make("article");
    const heading = make("h3", "card-title");
    const open = make("button", "card-open", "");
    open.type = "button";
    open.innerHTML = highlight(event.name || "이름 없는 행사", query);
    open.setAttribute("aria-label", `${event.name || "이름 없는 행사"} 상세 보기`);
    open.addEventListener("click", () => showDetail(event));
    heading.appendChild(open);
    article.appendChild(heading);

    const meta = make("div", "card-meta");
    meta.appendChild(make("span", "card-location", [event.sido, event.sigungu].filter(Boolean).join(" ") || "지역 정보 없음"));
    meta.appendChild(make("span", "card-date", dateLabel(event)));
    const dist = eventDist(event);
    if (dist != null) meta.appendChild(make("span", "card-distance", fmtDist(dist)));
    if (event.age) meta.appendChild(make("span", "card-age", `대상 ${event.age}`));
    meta.appendChild(make("span", "card-price", priceLabel(event)));
    meta.appendChild(renderBadges(event));
    article.appendChild(meta);
    if (isExperience(event) && event.experience_summary) {
      article.appendChild(make("p", "card-experience", `해볼 수 있는 일: ${event.experience_summary}`));
    }
    if (hasFamilySignal(event) && event.family_evidence) {
      article.appendChild(make("p", "card-evidence", `가족 근거: ${event.family_evidence}`));
    }

    const actions = make("div", "card-actions");
    const detailButton = make("button", "button button-quiet", "상세 보기");
    detailButton.type = "button";
    detailButton.addEventListener("click", () => showDetail(event));
    actions.appendChild(detailButton);
    const proof = linkProof(event);
    if (proof.allowed) {
      actions.appendChild(linkStatusNode(event));
      const link = make("a", "button button-primary", "연결 확인");
      link.href = proof.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `${isApplyable(event) ? "출처 신청/상세 페이지" : "출처 상세 페이지"} · 연결 확인`);
      actions.appendChild(link);
    } else {
      actions.appendChild(linkStatusNode(event));
    }
    article.appendChild(actions);
    li.appendChild(article);
    return li;
  }
  function popup(event) {
    const wrap = make("div", "map-popup");
    const open = make("button", "map-popup-title", "");
    open.type = "button";
    open.innerHTML = escHtml(event.name || "이름 없는 행사");
    open.addEventListener("click", () => showDetail(event));
    wrap.appendChild(open);
    wrap.appendChild(make("div", "map-popup-meta", `${event.sido || "지역 정보 없음"} · ${dateLabel(event)}`));
    const status = statusInfo(event);
    if (status) wrap.appendChild(badge(status.label, status.className));
    if (isFree(event.price)) wrap.appendChild(badge("무료", "free"));
    const dist = eventDist(event);
    if (dist != null) wrap.appendChild(make("div", "map-popup-dist", fmtDist(dist)));
    const proof = linkProof(event);
    const row = make("div", "map-popup-actions");
    const detail = make("button", "button button-quiet", "상세 보기");
    detail.type = "button";
    detail.addEventListener("click", () => showDetail(event));
    row.appendChild(detail);
    if (proof.allowed) {
      const link = make("a", "button button-primary", "연결 확인");
      link.href = proof.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.appendChild(link);
    } else {
      row.appendChild(linkStatusNode(event));
    }
    wrap.appendChild(row);
    return wrap;
  }

  function popupGroup(events) {
    if (events.length === 1) return popup(events[0]);
    const wrap = make("div", "map-popup-list");
    wrap.appendChild(make("strong", null, `${events.length}개 행사`));
    for (const event of events) {
      const row = make("div", "map-popup-event");
      const button = make("button", null, event.name || "이름 없는 행사");
      button.type = "button";
      button.addEventListener("click", () => {
        showDetail(event);
      });
      const proof = linkProof(event);
      if (proof.allowed) {
        row.appendChild(linkStatusNode(event));
        const link = make("a", "map-source-link", "연결 확인");
        link.href = proof.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.appendChild(link);
      } else {
        row.appendChild(linkStatusNode(event));
      }
      row.appendChild(button);
      row.appendChild(make("small", null, `${event.sido || "지역 정보 없음"} · ${dateLabel(event)}`));
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
    const baseIcon = (count) => window.L.divIcon({
      className: "event-marker",
      html: count > 1 ? `<span aria-hidden="true"></span><b class="marker-count">${count}</b>` : `<span aria-hidden="true"></span>`,
      iconSize: [44, 44],
      iconAnchor: [22, 22],
      popupAnchor: [0, -24],
    });
    for (const events of groups.values()) {
      const first = events[0];
      const icon = baseIcon(events.length);
      const marker = window.L.marker([first.lat, first.lng], {
        icon, title: events.length > 1 ? `${events.length}개 행사` : (first.name || "행사 위치"),
      });
      marker.bindPopup(popupGroup(events));
      marker.addTo(state.markers);
      points.push([first.lat, first.lng]);
    }
    if (points.length) state.map.fitBounds(points, { padding: [24, 24], maxZoom: 15 });
    else state.map.setView([36.5, 127.8], 7);
  }

  function renderRegionQuick(baseRows) {
    const root = $("regionQuick");
    clear(root);
    const counts = new Map();
    for (const event of baseRows) if (event.sido) counts.set(event.sido, (counts.get(event.sido) || 0) + 1);
    const regions = state.regions.filter((region) => counts.has(region.sido)).sort((a, b) => counts.get(b.sido) - counts.get(a.sido));
    if (!regions.length) {
      root.appendChild(make("span", "result-hint", "현재 조건에서 지역별 결과가 없습니다."));
      return;
    }
    for (const region of regions) {
      const item = make("div");
      item.setAttribute("role", "listitem");
      const button = make("button", "region-chip");
      button.type = "button";
      button.setAttribute("aria-pressed", $("sido").value === region.sido ? "true" : "false");
      button.appendChild(make("span", null, region.sido));
      button.appendChild(make("strong", null, `${counts.get(region.sido)}건`));
      button.addEventListener("click", () => {
        $("sido").value = $("sido").value === region.sido ? "" : region.sido;
        applyFilters();
      });
      item.appendChild(button);
      root.appendChild(item);
    }
  }
  function renderActivityQuick(baseRows) {
    const root = $("activityQuick");
    clear(root);
    const counts = new Map();
    for (const event of baseRows) {
      if (!isExperience(event)) continue;
      for (const category of experienceCategories(event)) counts.set(category, (counts.get(category) || 0) + 1);
    }
    const categories = Array.from(counts.keys()).sort((a, b) => (EXPERIENCE_ORDER.indexOf(a) - EXPERIENCE_ORDER.indexOf(b)) || a.localeCompare(b, "ko"));
    if (!categories.length) {
      root.appendChild(make("span", "result-hint", "현재 조건에서 해볼 수 있는 체험이 없습니다."));
      return;
    }
    for (const category of categories) {
      const item = make("div");
      item.setAttribute("role", "listitem");
      const button = make("button", "region-chip activity-chip");
      button.type = "button";
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
      copy.appendChild(make("span", null, category));
      copy.appendChild(make("strong", null, `${counts.get(category)}건`));
      button.appendChild(copy);
      button.addEventListener("click", () => {
        $("activity").value = $("activity").value === category ? "" : category;
        applyFilters();
      });
      item.appendChild(button);
      root.appendChild(item);
    }
  }

  function sortRows(rows, rankOf) {
    const rank = rankOf || new Map();
    let seq = 0;
    const order = new Map();
    for (const event of rows) if (!order.has(event)) order.set(event, seq++);
    return rows.slice().sort((a, b) => {
      const endedDelta = Number(isEnded(a)) - Number(isEnded(b));
      if (endedDelta) return endedDelta;
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
      return Number(isExperience(b)) - Number(isExperience(a));
    });
  }

  function pageSize() {
    // 화면 높이에 맞는 페이지 크기: 카드 1장 ≈ 140px 기준, 5~12장 범위.
    const h = (typeof window !== "undefined" && window.innerHeight) || 800;
    return Math.max(5, Math.min(12, Math.floor(h / 140)));
  }

  function render(rows) {
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
    setHidden("empty", rows.length > 0);
    renderPager(rows.length, start, shown.length, pages);
    const ended = rows.filter(isEnded).length;
    const unmapped = rows.filter((e) => typeof e.lat !== "number" || typeof e.lng !== "number").length;
    const note = ended ? `종료된 행사 ${ended}건은 날짜에 '종료'로 표시했습니다.` : "출처 페이지에서 일정과 신청 조건을 한 번 더 확인하세요.";
    const mapNote = unmapped ? ` 지도 미표시 ${unmapped}건(위치 정보 없음).` : "";
    const sortNote = state.sortMode === "distance" && state.userPos ? " 내 위치 가까운 순." : "";
    $("resultHint").textContent = `${rows.length}건. ${note}${mapNote}${sortNote}`;
    renderMap(rows);
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
      prev.setAttribute("aria-label", "이전 페이지");
    }
    if (next) {
      next.disabled = state.page >= pages - 1;
      next.setAttribute("aria-label", "다음 페이지");
    }
    if (info) {
      info.textContent = total === 0
        ? ""
        : `${state.page + 1} / ${pages} 페이지 (전체 ${total}건 중 ${start + 1}–${start + shownCount}번째)`;
    }
  }

  function gotoPage(delta) {
    state.page += delta;
    render(state.filteredRows);
    const list = $("list");
    if (list && typeof list.scrollIntoView === "function") {
      list.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }

  function renderSubFacets(rows, filters) {
    const root = $("subFacets");
    if (!root) return;
    clear(root);
    const groups = [];
    if (!filters.theme) {
      const counts = new Map();
      for (const event of rows) for (const theme of event.themes || []) counts.set(theme, (counts.get(theme) || 0) + 1);
      groups.push({ dim: "theme", label: "테마 좁히기", items: topN(counts, 5) });
    }
    if (!filters.sido) {
      const counts = new Map();
      for (const event of rows) if (event.sido) counts.set(event.sido, (counts.get(event.sido) || 0) + 1);
      groups.push({ dim: "sido", label: "지역 좁히기", items: topN(counts, 5) });
    }
    if (!filters.age) {
      const counts = new Map();
      for (const event of rows) for (const age of event.age_bands || []) counts.set(age, (counts.get(age) || 0) + 1);
      groups.push({ dim: "age", label: "나이대 좁히기", items: topN(counts, 5) });
    }
    if (!filters.activity) {
      const counts = new Map();
      for (const event of rows) {
        if (!isExperience(event)) continue;
        for (const category of experienceCategories(event)) counts.set(category, (counts.get(category) || 0) + 1);
      }
      groups.push({ dim: "activity", label: "활동 좁히기", items: topN(counts, 5) });
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
        chip.appendChild(make("span", null, value));
        chip.appendChild(make("strong", null, `${count}건`));
        chip.addEventListener("click", () => {
          const control = $(group.dim === "sido" ? "sido" : group.dim);
          if (control) {
            if (control.tagName === "SELECT"
              && !Array.from(control.options).some((option) => option.value === value)) {
              const option = make("option", null, `${value} (현재 목록 없음)`);
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
  }

  function topN(counts, n) {
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, n);
  }

  function applyFilters() {
    const filters = currentFilters();
    state._search = null;
    state.page = 0;
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
    render(rows);
    writeURL(filters);
  }

  function openDialog(dialog) {
    state.lastFocus = document.activeElement;
    dialog.showModal();
    const close = dialog.querySelector(".close");
    if (close) close.focus();
    dialog.onclose = () => {
      if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
    };
  }

  function addClose(dialog) {
    const close = make("button", "close", "닫기");
    close.type = "button";
    close.setAttribute("aria-label", "창 닫기");
    close.addEventListener("click", () => dialog.close());
    dialog.appendChild(close);
  }

  function showDetail(event) {
    const dialog = $("detail");
    clear(dialog);
    addClose(dialog);
    const title = make("h2", null, event.name || "행사 상세");
    title.id = "detailTitle";
    dialog.appendChild(title);
    if (event.description) dialog.appendChild(make("p", null, event.description));
    else dialog.appendChild(make("p", null, "상세 설명은 출처 페이지에서 확인할 수 있습니다."));
    if (isExperience(event)) {
      const experience = make("section", "experience-detail");
      experience.appendChild(make("strong", null, "아이와 해볼 수 있는 것"));
      if (experienceCategories(event).length) experience.appendChild(make("p", null, `분류: ${experienceCategories(event).join(" · ")}`));
      if (event.experience_summary) experience.appendChild(make("p", null, event.experience_summary));
      dialog.appendChild(experience);
    }
    if (hasFamilySignal(event) && event.family_evidence) {
      dialog.appendChild(make("p", "card-evidence", `가족 근거: ${event.family_evidence}`));
    }
    const meta = make("div", "detail-meta");
    meta.appendChild(make("span", null, `지역: ${[event.sido, event.sigungu].filter(Boolean).join(" ") || "정보 없음"}`));
    meta.appendChild(make("span", null, `일정: ${dateLabel(event)}`));
    meta.appendChild(make("span", null, `비용: ${priceLabel(event)}`));
    const status = statusInfo(event);
    if (status) {
      const statusLine = make("span");
      statusLine.appendChild(make("span", null, "상태: "));
      statusLine.appendChild(badge(status.label, status.className));
      meta.appendChild(statusLine);
    }
    if (event.application_end) meta.appendChild(make("span", null, `신청 마감: ${dpart(event.application_end)}`));
    dialog.appendChild(meta);
    dialog.appendChild(make("p", null, `출처: ${event.source || "정보 없음"}${isDemoEvent(event) ? " · 데모 기록" : ""}`));
    const actions = make("div", "dialog-actions");
    const proof = linkProof(event);
    if (proof.allowed) {
      actions.appendChild(linkStatusNode(event));
      const link = make("a", "button button-primary", "연결 확인");
      link.href = proof.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `${isApplyable(event) ? "출처 신청/상세 페이지" : "출처 상세 페이지"} · 연결 확인`);
      actions.appendChild(link);
    } else {
      actions.appendChild(linkStatusNode(event));
    }
    const local = make("a", "button button-quiet", "공유용 상세");
    local.href = localDetailUrl(event);
    local.rel = "noopener";
    actions.appendChild(local);
    dialog.appendChild(actions);
    openDialog(dialog);
  }

  function recommend(rows) {
    return rows.filter((event) => !isEnded(event)).map((event) => {
      let score = 0;
      const reasons = [];
      if (isExperience(event)) { score += 2; reasons.push("해볼 수 있는 체험"); }
      if (hasFamilySignal(event)) { score += 3; reasons.push("가족 근거 확인"); }
      if (isFree(event.price)) { score += 1; reasons.push("무료"); }
      if (isApplyable(event)) { score += 2; reasons.push("신청가능"); }
      if ((event.themes || []).length) { score += 1; reasons.push(`테마 ${event.themes[0]}`); }
      return { event, score, reasons };
    }).sort((a, b) => b.score - a.score).slice(0, 5);
  }

  function showAI() {
    const dialog = $("aiDialog");
    clear(dialog);
    addClose(dialog);
    const title = make("h2", null, "조건 맞춤 추천");
    title.id = "aiTitle";
    dialog.appendChild(title);
    dialog.appendChild(make("p", null, "현재 필터 결과 안에서 가족 근거, 무료 여부, 신청 가능 여부를 기준으로 정리했습니다."));
    const recs = recommend(state.filteredRows);
    if (!recs.length) {
      dialog.appendChild(make("p", null, "추천할 진행 중 행사가 없습니다. 날짜나 지역을 넓혀 보세요."));
    } else {
      const list = make("ol", "recommendations");
      for (const item of recs) {
        const li = make("li");
        const open = make("button", "rec-open", item.event.name || "이름 없는 행사");
        open.type = "button";
        open.addEventListener("click", () => { dialog.close(); showDetail(item.event); });
        li.appendChild(open);
        li.appendChild(make("span", null, ` · ${item.event.sido || "지역 정보 없음"}`));
        li.appendChild(make("br"));
        li.appendChild(make("small", null, item.reasons.join(" · ") || "현재 조건 일치"));
        list.appendChild(li);
      }
      dialog.appendChild(list);
    }
    openDialog(dialog);
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
        ? "내 위치 기준 거리순 정렬이 켜졌습니다. 위치는 저장되지 않습니다."
        : "내 위치를 사용하면 가까운 순으로 정렬합니다. 위치는 저장되지 않습니다.";
    }
  }

  function locateUser() {
    const btn = $("locateBtn");
    const hint = $("locateHint");
    if (!state.mapReady || !("geolocation" in navigator)) {
      if (hint) hint.textContent = "이 브라우저에서는 위치 찾기를 지원하지 않습니다.";
      return;
    }
    if (btn) btn.disabled = true;
    if (hint) hint.textContent = "위치를 확인하는 중입니다…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userPos = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        if (state.userMarker && state.map) state.map.removeLayer(state.userMarker);
        state.userMarker = window.L.circleMarker([state.userPos.lat, state.userPos.lng], {
          radius: 9, color: "#fff", weight: 3, fillColor: "#24755c", fillOpacity: 1,
        }).addTo(state.map).bindPopup("내 위치");
        state.map.setView([state.userPos.lat, state.userPos.lng], 14);
        state.sortMode = "distance";
        if (btn) btn.disabled = false;
        applyFilters();
      },
      () => {
        if (btn) btn.disabled = false;
        if (hint) hint.textContent = "위치를 가져오지 못했습니다. 브라우저 권한을 확인해 주세요.";
      },
      { timeout: 8000, maximumAge: 600000 },
    );
  }

  function wire() {
    if (state.wired) return;
    state.wired = true;
    let searchTimer;
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
    $("retry").addEventListener("click", loadData);
    const sortBind = [["sortRel", "relevance"], ["sortNew", "upcoming"], ["sortDist", "distance"]];
    for (const [id, mode] of sortBind) {
      const btn = $(id);
      if (btn) btn.addEventListener("click", () => { state.sortMode = mode; state.page = 0; updateSortUI(); render(state.filteredRows); });
    }
    const locate = $("locateBtn");
    if (locate) locate.addEventListener("click", locateUser);
    const prev = $("pagePrev");
    if (prev) prev.addEventListener("click", () => gotoPage(-1));
    const next = $("pageNext");
    if (next) next.addEventListener("click", () => gotoPage(1));
    window.addEventListener("popstate", () => { restoreFromURL(); applyFilters(); });
    let resizeFrame;
    let lastPageSize = pageSize();
    window.addEventListener("resize", () => {
      window.cancelAnimationFrame(resizeFrame);
      resizeFrame = window.requestAnimationFrame(() => {
        if (state.mapReady) state.map.invalidateSize({ pan: false });
        const size = pageSize();
        if (size !== lastPageSize) {
          lastPageSize = size;
          if (state.filteredRows.length) render(state.filteredRows);
        }
      });
    });
  }

  function renderFreshness(updated) {
    const builtAt = updated && updated.generated_at;
    const builtMs = builtAt ? Date.parse(String(builtAt)) : NaN;
    const staleDays = Number.isFinite(builtMs) ? Math.floor((Date.now() - builtMs) / 86400000) : null;
    const staleSuffix = staleDays != null && staleDays >= 3 ? ` · 정보 ${staleDays}일 경과(출처 확인 권장)` : "";
    $("freshness").textContent = builtAt ? `빌드 ${formatTimestamp(builtAt)} · ${state.events.length}건${staleSuffix}` : `${state.events.length}건 준비됨`;
    $("freshness").title = builtAt ? `정적 빌드 시각 ${builtAt}` : "";
    const sourceNode = $("sourceStatus");
    const modeLabels = { offline: "오프라인(offline)", live: "실시간(live)", mixed: "혼합(mixed)" };
    const mode = modeLabels[String(updated && updated.source_mode || "offline")] || "확인 필요";
    const parts = [`수집 모드: ${mode}`, "예약 설정: 매일 03:00 KST"];
    const lastSuccess = formatTimestamp(updated && updated.last_success_at);
    parts.push(lastSuccess ? `최근 실시간 성공: ${lastSuccess}` : "최근 실시간 성공 기록 없음");
    const sources = updated && updated.sources && typeof updated.sources === "object" ? Object.entries(updated.sources) : [];
    const sourceModes = sources.map(([name, info]) => `${name} ${info && info.mode === "live" ? "실시간(live)" : info && info.mode === "error" ? "수집 실패(error)" : "기존 기록(fixture)"}`);
    if (sourceModes.length) parts.push(`출처별: ${sourceModes.join(" · ")}`);
    const errorCount = Number(updated && updated.collection_error_count || 0);
    if (errorCount) parts.push(`수집 오류 ${errorCount}건`);
    sourceNode.textContent = parts.join(" · ");
    const collectionErrors = updated && Array.isArray(updated.collection_errors) ? updated.collection_errors : [];
    sourceNode.title = collectionErrors.join(" · ") || sourceNode.textContent;
  }

  async function loadData() {
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
      state.fuse = window.Fuse ? new window.Fuse(state.events, { keys: ["name", "description", "sido", "sigungu", "themes", "experience_summary", "experience_categories", "family_evidence"], threshold: .4, ignoreLocation: true }) : null;
      buildFilters();
      wire();
      restoreFromURL();
      applyFilters();
      renderFreshness(updated);
      setHidden("loading", true);
    } catch (error) {
      setHidden("loading", true);
      setHidden("loadError", false);
      $("freshness").textContent = "데이터를 불러오지 못했습니다";
      $("resultHint").textContent = error && error.message ? error.message : "데이터 로드 실패";
    }
  }

  function main() {
    initMap();
    loadData();
  }

  document.addEventListener("DOMContentLoaded", main);
})();
