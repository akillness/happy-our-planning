/* 방문 계획과 확인한 방문 기록은 이 브라우저에만 저장한다. 위치 기록이나 계정 연동은 사용하지 않는다. */
(function () {
  "use strict";

  const STORAGE_KEY = "notchima:timeline:v2";
  const LEGACY_STORAGE_KEY = "notchima:timeline:v1";
  const SOURCES = new Set(["manual", "google-export"]);
  const FILTER_VIEWS = new Set(["all", "planned", "visited"]);
  const $ = (id) => document.getElementById(id);
  const t = (key, values) => window.NotchimaI18n.t(key, values);
  let entries = [];
  let catalog = new Map();
  let callbacks;
  let noticeKey = "";
  let returnTarget = null;
  let editsPending = false;
  let pointerActive = false;
  let storageBlocked = false;
  let storageCorrupt = false;
  let filterDate = "";
  let filterView = "all";

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }

  function textFor(key, fallback, values) {
    try {
      const translated = t(key, values);
      return translated && translated !== key ? translated : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function validDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }

  function eventDate(value) {
    const date = typeof value === "string" ? value.slice(0, 10) : "";
    return validDate(date) ? date : "";
  }

  function validTime(value) {
    return value === "" || (typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value));
  }

  function withinDates(event, date) {
    if (!date) return true;
    const start = eventDate(event && event.start_date);
    const end = eventDate(event && (event.end_date || event.start_date));
    return (!start || date >= start) && (!end || date <= end);
  }

  function kstParts() {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return values;
  }

  function todayKST() {
    const parts = kstParts();
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  function isPastOrPresent(date, time) {
    if (!validDate(date)) return false;
    const today = todayKST();
    if (date < today) return true;
    if (date > today) return false;
    if (!time) return true;
    if (!validTime(time)) return false;
    const parts = kstParts();
    return time <= `${parts.hour}:${parts.minute}`;
  }

  function newEntryId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    } catch (_) {
      // A local fallback keeps old browsers and the test VM usable.
    }
    const template = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx";
    return template.replace(/[xy]/g, (token) => {
      const random = Math.floor(Math.random() * 16);
      const value = token === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    });
  }

  function normalizeEntry(entry, fallbackVisited, fallbackSource) {
    if (!entry || typeof entry !== "object" || typeof entry.event_id !== "string" || !entry.event_id) return null;
    const date = typeof entry.date === "string" ? entry.date : "";
    const time = typeof entry.time === "string" ? entry.time : "";
    if ((date && !validDate(date)) || !validTime(time)) return null;
    const visited = typeof entry.visited === "boolean" ? entry.visited : Boolean(fallbackVisited);
    const source = SOURCES.has(entry.source) ? entry.source : fallbackSource;
    if (!SOURCES.has(source)) return null;
    if (typeof entry.entry_id !== "string" || !entry.entry_id) return null;
    return { entry_id: entry.entry_id, event_id: entry.event_id, date, time, visited, source };
  }

  function normalizeV2(payload) {
    if (!payload || payload.version !== 2 || !Array.isArray(payload.entries)) return null;
    const seen = new Set();
    const result = [];
    for (const raw of payload.entries) {
      const entry = normalizeEntry(raw, false, "manual");
      if (!entry || seen.has(entry.entry_id)) return null;
      seen.add(entry.entry_id);
      result.push(entry);
    }
    return result;
  }

  function normalizeLegacy(payload) {
    if (!payload || payload.version !== 1 || !Array.isArray(payload.entries)) return null;
    const result = [];
    const seen = new Set();
    for (const raw of payload.entries) {
      if (!raw || typeof raw !== "object" || typeof raw.event_id !== "string" || !raw.event_id) return null;
      const requestedId = typeof raw.entry_id === "string" && raw.entry_id ? raw.entry_id : newEntryId();
      if (seen.has(requestedId)) return null;
      const entry = normalizeEntry({
        entry_id: requestedId,
        event_id: raw.event_id,
        date: typeof raw.date === "string" ? raw.date : "",
        time: typeof raw.time === "string" ? raw.time : "",
        visited: raw.visited === true,
        source: SOURCES.has(raw.source) ? raw.source : "manual",
      }, false, "manual");
      if (!entry) return null;
      seen.add(entry.entry_id);
      result.push(entry);
    }
    return result;
  }

  function announce(key) {
    noticeKey = key || "";
    const notice = $("timelineNotice");
    if (!notice) return;
    notice.hidden = !noticeKey;
    notice.textContent = noticeKey ? textFor(noticeKey, noticeKey) : "";
  }

  function storageGet(key) {
    try {
      if (!window.localStorage) return { ok: false, blocked: true, value: null };
      return { ok: true, blocked: false, value: window.localStorage.getItem(key) };
    } catch (_) {
      return { ok: false, blocked: true, value: null };
    }
  }

  function storageSave(payload) {
    if (storageBlocked) return false;
    try {
      if (!window.localStorage) throw new Error("Storage unavailable");
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 2, entries: payload }));
    } catch (_) {
      storageBlocked = true;
      announce("timeline.saveFailed");
      return false;
    }
    // Remove v1 only after the v2 write has completed. A cleanup failure does
    // not invalidate the committed v2 payload; the stale v1 key is ignored.
    try {
      if (window.localStorage.getItem(LEGACY_STORAGE_KEY) != null) window.localStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch (_) {}
    storageBlocked = false;
    storageCorrupt = false;
    announce("");
    return true;
  }

  function restore() {
    entries = [];
    storageBlocked = false;
    storageCorrupt = false;
    const current = storageGet(STORAGE_KEY);
    if (current.blocked) {
      storageBlocked = true;
      announce("timeline.storageBlocked");
      return;
    }
    if (current.value) {
      let saved = null;
      try { saved = JSON.parse(current.value); } catch (_) { saved = null; }
      const restored = normalizeV2(saved);
      if (restored) {
        entries = restored;
        announce("");
        return;
      }
      // Never replace a usable legacy record with an empty v2 payload.
      const legacy = storageGet(LEGACY_STORAGE_KEY);
      const migrated = legacy.value ? parseLegacyRaw(legacy.value) : null;
      if (migrated) entries = migrated;
      storageBlocked = true;
      storageCorrupt = true;
      announce("timeline.corrupt");
      return;
    }

    const legacy = storageGet(LEGACY_STORAGE_KEY);
    if (legacy.blocked) {
      storageBlocked = true;
      announce("timeline.storageBlocked");
      return;
    }
    if (!legacy.value) {
      announce("");
      return;
    }
    const migrated = parseLegacyRaw(legacy.value);
    if (!migrated) {
      storageBlocked = true;
      storageCorrupt = true;
      announce("timeline.corrupt");
      return;
    }
    entries = migrated;
    if (!storageSave(entries)) announce("timeline.saveFailed");
  }

  function parseLegacyRaw(raw) {
    try { return normalizeLegacy(JSON.parse(raw)); } catch (_) { return null; }
  }

  function persistedEntries() {
    return entries.map(({ entry_id, event_id, date, time, visited, source }) => ({
      entry_id, event_id, date, time, visited, source,
    }));
  }

  function save() {
    return storageSave(persistedEntries());
  }

  function plannedEntry(eventId) {
    return entries.find((entry) => entry.event_id === eventId && !entry.visited) || null;
  }

  function sameVisit(a, b) {
    return a.event_id === b.event_id && a.date === b.date && a.time === b.time;
  }
  function visitMetaText(entry) {
    const state = textFor(entry.visited ? "history.visited" : "history.planned", entry.visited ? "방문 완료 · 직접 확인" : "방문 계획");
    const source = textFor(entry.source === "google-export" ? "history.sourceGoogle" : "history.sourceManual", entry.source === "google-export" ? "Google 파일에서 확인한 방문" : "직접 남긴 방문");
    return `${state} · ${source}`;
  }

  function syncButtons() {
    document.querySelectorAll("[data-timeline-event]").forEach((button) => {
      const planned = Boolean(plannedEntry(button.dataset.timelineEvent));
      button.textContent = textFor(planned ? "timeline.remove" : "timeline.add", planned ? "계획에서 제거" : "타임라인에 담기");
      button.setAttribute("aria-pressed", String(planned));
    });
  }

  function removePlan(eventId) {
    const plan = plannedEntry(eventId);
    if (!plan) return;
    entries = entries.filter((entry) => entry.entry_id !== plan.entry_id);
    save();
    render();
  }

  function removeEntry(entryId) {
    const target = entries.find((entry) => entry.entry_id === entryId);
    if (!target) return;
    entries = entries.filter((entry) => entry.entry_id !== entryId);
    save();
    render();
  }

  function canMarkVisited(entry, event) {
    if (!event || !validDate(entry.date) || !withinDates(event, entry.date)) return false;
    return isPastOrPresent(entry.date, entry.time);
  }

  function toggleVisited(entryId) {
    const entry = entries.find((candidate) => candidate.entry_id === entryId);
    if (!entry) return;
    const focused = document.activeElement;
    const redraw = () => render({ reuseRows: true, restoreFocusTarget: focused });
    if (!entry.visited) {
      const event = catalog.get(entry.event_id);
      if (!canMarkVisited(entry, event)) {
        announce("history.futureVisit");
        redraw();
        return;
      }
      if (entries.some((candidate) => candidate.entry_id !== entry.entry_id && candidate.visited && sameVisit(candidate, entry))) {
        announce("history.duplicateVisit");
        redraw();
        return;
      }
      entry.visited = true;
    } else {
      if (entries.some((candidate) => candidate.entry_id !== entry.entry_id && candidate.event_id === entry.event_id && !candidate.visited)) {
        announce("history.duplicatePlan");
        redraw();
        return;
      }
      entry.visited = false;
    }
    save();
    redraw();
  }

  function addAction(container, event) {
    if (!container || !event || typeof event.id !== "string") return;
    const button = node("button", "button button-quiet timeline-save");
    button.type = "button";
    button.dataset.timelineEvent = event.id;
    const sync = () => {
      const planned = Boolean(plannedEntry(event.id));
      button.textContent = textFor(planned ? "timeline.remove" : "timeline.add", planned ? "계획에서 제거" : "타임라인에 담기");
      button.setAttribute("aria-pressed", String(planned));
    };
    sync();
    button.addEventListener("click", () => {
      if (plannedEntry(event.id)) {
        removePlan(event.id);
        return;
      }
      const start = eventDate(event.start_date);
      const end = eventDate(event.end_date || event.start_date);
      const now = todayKST();
      const date = start && start < now && (!end || end >= now) ? now : start;
      entries = entries.concat({ entry_id: newEntryId(), event_id: event.id, date, time: "", visited: false, source: "manual" });
      save();
      render();
    });
    container.appendChild(button);
  }

  function showMap({ focus = false } = {}) {
    const panel = $("timelinePanel");
    const wasOpen = panel && !panel.hidden;
    if (panel) panel.hidden = true;
    if ($("resultsPanel")) $("resultsPanel").hidden = false;
    if ($("mapTab")) $("mapTab").setAttribute("aria-pressed", "true");
    if ($("timelineTab")) $("timelineTab").setAttribute("aria-pressed", "false");
    if (callbacks && typeof callbacks.onLayout === "function") callbacks.onLayout();
    if (focus && wasOpen) (returnTarget && returnTarget.isConnected ? returnTarget : $("timelineTab")).focus();
  }

  function showTimeline() {
    returnTarget = document.activeElement;
    if ($("filterMenu")) $("filterMenu").open = false;
    if ($("toolsMenu")) $("toolsMenu").open = false;
    if ($("resultsPanel")) $("resultsPanel").hidden = true;
    if ($("timelinePanel")) $("timelinePanel").hidden = false;
    if ($("mapTab")) $("mapTab").setAttribute("aria-pressed", "false");
    if ($("timelineTab")) $("timelineTab").setAttribute("aria-pressed", "true");
    render();
    if (callbacks && typeof callbacks.onLayout === "function") callbacks.onLayout();
    if ($("timelineClose")) $("timelineClose").focus();
  }

  function editField(item, entry, event, field) {
    const label = node("label", null, textFor(field === "date" ? "timeline.visitDate" : "timeline.visitTime", field === "date" ? "방문 날짜" : "방문 시간"));
    const input = node("input");
    input.type = field;
    input.value = entry[field];
    input.dataset.field = field;
    input.setAttribute("aria-label", `${textFor(field === "date" ? "timeline.visitDate" : "timeline.visitTime", field === "date" ? "방문 날짜" : "방문 시간")} · ${event.name || entry.event_id}`);
    if (field === "date") {
      input.min = eventDate(event.start_date);
      input.max = eventDate(event.end_date || event.start_date);
    }
    input.addEventListener("change", () => {
      const value = input.value;
      const date = field === "date" ? value : entry.date;
      const time = field === "time" ? value : entry.time;
      const valid = field === "date" ? validDate(value) && withinDates(event, value) : validTime(value);
      const futureVisited = entry.visited && !isPastOrPresent(date, time);
      const duplicateVisited = entry.visited && entries.some((candidate) => candidate.entry_id !== entry.entry_id && candidate.visited && sameVisit(candidate, { ...entry, date, time }));
      if (!valid || futureVisited || duplicateVisited) {
        input.value = entry[field];
        announce(duplicateVisited ? "history.duplicateVisit" : futureVisited ? "history.futureVisit" : "timeline.invalidDateTime");
        return;
      }
      entry[field] = value;
      save();
      editsPending = true;
    });
    input.addEventListener("blur", () => { window.setTimeout(commitEdits, 0); });
    label.appendChild(input);
    item.appendChild(label);
  }

  function commitEdits() {
    const list = $("timelineList");
    if (!editsPending || pointerActive || (list && list.querySelector(".timeline-fields input:focus"))) return;
    editsPending = false;
    render({ reuseRows: true });
  }

  function readFilters() {
    const input = $("timelineDate");
    const select = $("timelineView");
    filterDate = input && validDate(input.value) ? input.value : "";
    filterView = select && FILTER_VIEWS.has(select.value) ? select.value : "all";
  }

  function filteredEntries() {
    return entries.filter((entry) => {
      if (filterDate && entry.date !== filterDate) return false;
      if (filterView === "planned" && entry.visited) return false;
      if (filterView === "visited" && !entry.visited) return false;
      return true;
    });
  }

  function updateEmptyState(count) {
    const empty = $("timelineEmpty");
    if (!empty) return;
    empty.hidden = count !== 0;
    const title = empty.querySelector("strong");
    const body = empty.querySelector("p");
    if (entries.length && count === 0) {
      if (title) title.textContent = textFor("history.emptyFiltered", "선택한 날짜나 보기에 맞는 방문이 없어요.");
      if (body) body.textContent = textFor("history.emptyFilteredBody", "전체 날짜나 다른 보기를 선택해 주세요.");
    } else {
      if (title) title.textContent = textFor("timeline.emptyTitle", "아직 담은 일정이 없어요.");
      if (body) body.textContent = textFor("timeline.emptyBody", "지도에서 마음에 드는 행사를 찾아 타임라인에 담아보세요.");
    }
  }

  function sortEntries(list) {
    return list.map((entry, index) => ({ entry, index })).sort((left, right) => {
      const dateA = left.entry.date || "9999-99-99";
      const dateB = right.entry.date || "9999-99-99";
      return dateA.localeCompare(dateB)
        || (left.entry.time || "99:99").localeCompare(right.entry.time || "99:99")
        || left.index - right.index;
    }).map(({ entry }) => entry);
  }

  function buildRow(item, entry, event, list) {
    const heading = node("h4", "timeline-event-title");
    if (event) {
      const open = node("button", "rec-open", event.name || entry.event_id);
      open.type = "button";
      open.addEventListener("click", () => callbacks.onDetail(event));
      heading.appendChild(open);
    } else heading.textContent = textFor("timeline.unavailable", "행사 정보를 찾을 수 없어요.");
    item.appendChild(heading);
    item.appendChild(node("p", "timeline-place timeline-history-meta", visitMetaText(entry)));
    if (event) {
      item.appendChild(node("p", "timeline-place", [event.venue, event.address].filter(Boolean).join(" · ") || textFor("directions.unavailable", "장소 정보 없음")));
      const fields = node("div", "timeline-fields");
      editField(fields, entry, event, "date");
      editField(fields, entry, event, "time");
      item.appendChild(fields);
      item.appendChild(node("p", "timeline-note timeline-schedule-warning"));
      item.appendChild(node("div", "timeline-leg"));
    } else item.appendChild(node("p", "timeline-note", textFor("timeline.unavailableBody", "현재 공개 행사 목록에서 이 기록을 찾지 못했어요.", { id: entry.event_id })));
    const actions = node("div", "timeline-actions");
    const visitedControl = node("div", "timeline-visited");
    const visitedButton = node("button", "button button-quiet timeline-visited");
    visitedButton.type = "button";
    visitedButton.addEventListener("click", () => toggleVisited(entry.entry_id));
    visitedControl.appendChild(visitedButton);
    actions.appendChild(visitedControl);
    const removeButton = node("button", "button button-quiet timeline-remove", textFor("timeline.remove", "삭제"));
    removeButton.type = "button";
    removeButton.addEventListener("click", () => {
      const index = Array.from(list.children).indexOf(item);
      removeEntry(entry.entry_id);
      const next = list.children[Math.min(index, list.children.length - 1)];
      (next ? next.querySelector(".timeline-remove") : $("timelineClose"))?.focus();
    });
    actions.appendChild(removeButton);
    item.appendChild(actions);
  }

  function updateRow(item, entry, event, previous, restoreFocus) {
    const visitedButton = item.querySelector(".timeline-visited button") || item.querySelector(".timeline-visited");
    if (visitedButton) {
      const value = String(entry.visited);
      visitedButton.textContent = textFor(entry.visited ? "history.backToPlan" : "history.markVisited", entry.visited ? "계획으로 되돌리기" : "방문 완료로 표시");
      visitedButton.setAttribute("aria-pressed", value);
    }
    const meta = item.querySelector(".timeline-history-meta");
    if (meta) meta.textContent = visitMetaText(entry);
    if (!event) return;
    const warning = item.querySelector(".timeline-schedule-warning");
    if (warning) {
      warning.hidden = withinDates(event, entry.date);
      warning.textContent = warning.hidden ? "" : textFor("timeline.scheduleChanged", "행사 일정과 다른 날짜예요.");
    }
    const leg = item.querySelector(".timeline-leg");
    if (!leg || !window.NotchimaDirections || typeof window.NotchimaDirections.append !== "function") return;
    const focused = leg.contains(restoreFocus) ? restoreFocus : null;
    const details = leg.querySelector("details");
    const wasOpen = details && details.open;
    leg.replaceChildren(node("p", "timeline-note", previous
      ? textFor("timeline.fromPrevious", `이전 장소: ${previous.name || textFor("timeline.unavailable", "정보 없음")}`, { name: previous.name || textFor("timeline.unavailable", "정보 없음") })
      : textFor("timeline.fromCurrent", "현재 위치에서 출발")));
    window.NotchimaDirections.append(leg, event, { originEvent: previous });
    const nextDetails = leg.querySelector("details");
    if (wasOpen && nextDetails) nextDetails.open = true;
    if (focused) {
      restoreFocus = Array.from(leg.querySelectorAll("a, summary")).find((element) => element.tagName === focused.tagName && element.className === focused.className);
    }
    return restoreFocus;
  }
  function render({ reuseRows = false, restoreFocusTarget = null } = {}) {
    if (!callbacks) return;
    catalog = new Map((callbacks.getEvents ? callbacks.getEvents() : []).map((event) => [event.id, event]));
    readFilters();
    syncButtons();
    const list = $("timelineList");
    if (!list) return;
    const existing = reuseRows ? new Map(Array.from(list.children).map((item) => [item.dataset.entryId, item])) : new Map();
    const shown = sortEntries(filteredEntries());
    const used = new Set();
    let restoreFocus = restoreFocusTarget || document.activeElement;
    if (!reuseRows) list.replaceChildren();
    updateEmptyState(shown.length);
    if (noticeKey) announce(noticeKey);
    const previousByEntry = new Map();
    let routePrevious = null;
    let routeDate = null;
    for (const routeEntry of sortEntries(entries)) {
      if (routeEntry.date !== routeDate) routePrevious = null;
      previousByEntry.set(routeEntry.entry_id, routePrevious);
      routePrevious = catalog.get(routeEntry.event_id) || { name: textFor("timeline.unavailable", "정보 없음") };
      routeDate = routeEntry.date;
    }
    let previousDate = null;
    for (const [position, entry] of shown.entries()) {
      const event = catalog.get(entry.event_id);
      const item = existing.get(entry.entry_id) || node("li", "timeline-item");
      const reused = existing.has(entry.entry_id);
      used.add(entry.entry_id);
      item.dataset.eventId = entry.event_id;
      item.dataset.entryId = entry.entry_id;
      const day = item.querySelector(".timeline-day");
      if (day) day.remove();
      if (entry.date !== previousDate) {
        item.prepend(node("h3", "timeline-day", entry.date ? window.NotchimaI18n.formatDate(entry.date) : textFor("timeline.undated", "날짜 미정")));
      }
      let time = item.querySelector(".timeline-time");
      if (!time) { time = node("p", "timeline-time"); item.appendChild(time); }
      time.textContent = entry.time || textFor("timeline.timeUnset", "시간 미정");
      if (!reused) buildRow(item, entry, event, list);
      restoreFocus = updateRow(item, entry, event, previousByEntry.get(entry.entry_id) || null, restoreFocus) || restoreFocus;
      if (list.children[position] !== item) list.insertBefore(item, list.children[position] || null);
      previousDate = entry.date;
    }
    if (reuseRows) Array.from(list.children).forEach((item) => {
      if (!used.has(item.dataset.entryId)) list.removeChild(item);
    });
    const count = $("timelineCount");
    if (count) count.textContent = String(shown.length);
    if (restoreFocus && restoreFocus.isConnected && document.activeElement !== restoreFocus) restoreFocus.focus({ preventScroll: true });
    else if (restoreFocus && !restoreFocus.isConnected) {
      const fallback = list.querySelector(".timeline-visited button") || $("timelineView");
      if (fallback) fallback.focus({ preventScroll: true });
    }
  }

  function importEntries(candidates) {
    const list = Array.isArray(candidates) ? candidates : [];
    const seen = new Set();
    let added = 0;
    let duplicates = 0;
    const nowCatalog = new Map((callbacks && callbacks.getEvents ? callbacks.getEvents() : []).map((event) => [event.id, event]));
    const additions = [];
    for (const candidate of list) {
      if (!candidate || candidate.source !== "google-export" || typeof candidate.event_id !== "string") continue;
      const date = candidate.date;
      const time = candidate.time;
      const event = nowCatalog.get(candidate.event_id);
      if (!event || !validDate(date) || !validTime(time) || !time || !withinDates(event, date) || !isPastOrPresent(date, time)) continue;
      const key = `${candidate.event_id}\u0000${date}\u0000${time}`;
      if (seen.has(key) || entries.some((entry) => entry.visited && sameVisit(entry, { event_id: candidate.event_id, date, time }))) {
        duplicates += 1;
        continue;
      }
      seen.add(key);
      additions.push({ entry_id: newEntryId(), event_id: candidate.event_id, date, time, visited: true, source: "google-export" });
    }
    if (additions.length) {
      const previousEntries = entries;
      entries = entries.concat(additions);
      if (!save()) {
        entries = previousEntries;
        render();
        throw new Error("timeline import save failed");
      }
      added = additions.length;
      render();
    }
    return { added, duplicates };
  }

  function bindFilters() {
    const date = $("timelineDate");
    if (date) date.addEventListener("change", () => { filterDate = validDate(date.value) ? date.value : ""; render(); });
    const view = $("timelineView");
    if (view) view.addEventListener("change", () => { filterView = FILTER_VIEWS.has(view.value) ? view.value : "all"; render(); });
    const clear = $("timelineDateClear");
    if (clear) clear.addEventListener("click", () => {
      if (date) { date.value = ""; date.focus(); }
      filterDate = "";
      render();
    });
  }

  function init(options) {
    if (callbacks) return;
    callbacks = options || {};
    catalog = new Map((callbacks.getEvents ? callbacks.getEvents() : []).map((event) => [event.id, event]));
    document.addEventListener("pointerdown", () => { pointerActive = true; }, true);
    document.addEventListener("pointerup", () => {
      pointerActive = false;
      window.setTimeout(commitEdits, 0);
    }, true);
    document.addEventListener("pointercancel", () => { pointerActive = false; commitEdits(); }, true);
    restore();
    bindFilters();
    if ($("mapTab")) $("mapTab").addEventListener("click", () => showMap());
    if ($("timelineTab")) $("timelineTab").addEventListener("click", showTimeline);
    if ($("timelineClose")) $("timelineClose").addEventListener("click", () => showMap({ focus: true }));
    if ($("timelineBrowse")) $("timelineBrowse").addEventListener("click", () => {
      showMap();
      if ($("resultsPanel")) $("resultsPanel").dataset.collapsed = "false";
      if (callbacks.onLayout) callbacks.onLayout();
      if ($("resultsToggle")) $("resultsToggle").focus();
    });
    const tools = $("toolsMenu");
    if (tools) tools.addEventListener("toggle", () => {
      if ($("toolsToggle")) $("toolsToggle").setAttribute("aria-expanded", String(tools.open));
      if (tools.open) {
        showMap();
        if ($("filterMenu")) $("filterMenu").open = false;
        if (callbacks.isMobile && callbacks.isMobile()) $("resultsPanel").dataset.collapsed = "true";
        if (callbacks.onLayout) callbacks.onLayout();
      }
    });
    if ($("filterMenu")) $("filterMenu").addEventListener("toggle", () => {
      if ($("filterMenu").open) {
        showMap();
        if (tools) tools.open = false;
      }
    });
    if ($("resultsToggle")) $("resultsToggle").addEventListener("click", () => { if (tools) tools.open = false; });
    document.addEventListener("pointerdown", (event) => {
      if (tools && tools.open && !tools.contains(event.target)) tools.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector("dialog[open]")) return;
      if (tools && tools.open) {
        tools.open = false;
        if ($("toolsToggle")) $("toolsToggle").focus();
        event.preventDefault();
      } else if ($("timelinePanel") && !$('timelinePanel').hidden) {
        showMap({ focus: true });
        event.preventDefault();
      }
    });
    window.addEventListener("notchima:languagechange", render);
    window.addEventListener("storage", (event) => {
      if (event.key !== STORAGE_KEY && event.key !== LEGACY_STORAGE_KEY) return;
      if (event.key === STORAGE_KEY && event.newValue) {
        let saved = null;
        try { saved = JSON.parse(event.newValue); } catch (_) { saved = null; }
        const recovered = normalizeV2(saved);
        if (recovered) {
          entries = recovered;
          storageBlocked = false;
          storageCorrupt = false;
          announce("");
          render();
          return;
        }
        storageBlocked = true;
        storageCorrupt = true;
        announce("timeline.corrupt");
        render();
        return;
      }
      restore();
      render();
    });
    render();
  }

  window.NotchimaTimeline = { init, addAction, refresh: render, showMap, importEntries };
})();
