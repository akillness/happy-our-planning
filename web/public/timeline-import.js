/* Google export formats are file formats, not a public Timeline API.
 * User-controlled exports: https://support.google.com/maps/answer/6258979
 * Only explicit visits are read; raw location traces never leave this browser.
 */
(function () {
  "use strict";
  const MAX_BYTES = 20 * 1024 * 1024;
  const MAX_RECORDS = 100000;
  const MAX_CANDIDATES = 1000;
  const $ = (id) => document.getElementById(id);
  const t = (key, values) => window.NotchimaI18n.t(key, values);
  const kst = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });

  function failure(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function point(lat, lng) {
    return typeof lat === "number" && typeof lng === "number" && Number.isFinite(lat) && Number.isFinite(lng)
      && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 && (lat !== 0 || lng !== 0) ? { lat, lng } : null;
  }

  function locationPoint(location) {
    const text = typeof location === "string" ? location : location && location.latLng;
    if (typeof text !== "string") return null;
    const match = text.trim().match(/^(?:geo:)?\s*(-?\d+(?:\.\d+)?)\s*°?\s*,\s*(-?\d+(?:\.\d+)?)\s*°?\s*$/i);
    return match ? point(Number(match[1]), Number(match[2])) : null;
  }

  function instant(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return NaN;
    const day = new Date(`${value.slice(0, 10)}T00:00:00Z`);
    if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== value.slice(0, 10)) return NaN;
    return Date.parse(value);
  }

  function validVisit(visit) {
    if (!visit || !point(visit.lat, visit.lng)) return false;
    const start = instant(visit.start);
    const end = instant(visit.end);
    return Number.isFinite(start) && Number.isFinite(end) && end >= start && end <= Date.now();
  }

  function milliseconds(value) {
    if (typeof value !== "string" || !/^\d+$/.test(value)) return undefined;
    const date = new Date(Number(value));
    return Number.isFinite(date.getTime()) ? date.toISOString() : undefined;
  }

  function parse(text) {
    if (typeof text !== "string") throw failure("invalid", "Invalid JSON file");
    if (text.length > MAX_BYTES || new TextEncoder().encode(text).byteLength > MAX_BYTES) throw failure("tooLarge", "File exceeds 20 MiB size limit");
    let data;
    try { data = JSON.parse(text.replace(/^\uFEFF/, "")); }
    catch (_) { throw failure("invalid", "Invalid JSON file"); }
    const format = Array.isArray(data) ? "ios" : data && Array.isArray(data.timelineObjects) ? "legacy"
      : data && Array.isArray(data.semanticSegments) ? "android" : null;
    if (!format) throw failure("unsupported", "Unsupported Timeline export format");
    const records = format === "ios" ? data : format === "legacy" ? data.timelineObjects : data.semanticSegments;
    if (records.length > MAX_RECORDS) throw failure("tooMany", "File exceeds 100000 record limit");
    const visits = [];
    let skipped = 0;
    for (const record of records) {
      const visit = record && (format === "legacy" ? record.placeVisit : record.visit);
      let coordinates = null;
      let start;
      let end;
      if (visit && format === "legacy") {
        const location = visit.location;
        if (location && typeof location.latitudeE7 === "number" && typeof location.longitudeE7 === "number") {
          coordinates = point(location.latitudeE7 / 1e7, location.longitudeE7 / 1e7);
        }
        const duration = visit.duration || {};
        start = duration.startTimestamp;
        end = duration.endTimestamp;
        if (start == null) start = milliseconds(duration.startTimestampMs);
        if (end == null) end = milliseconds(duration.endTimestampMs);
      } else if (visit) {
        coordinates = locationPoint(visit.topCandidate && visit.topCandidate.placeLocation);
        start = record.startTime ?? (record.duration || {}).startTimestamp;
        end = record.endTime ?? (record.duration || {}).endTimestamp;
      }
      const normalized = coordinates && { ...coordinates, start, end };
      if (validVisit(normalized)) visits.push(normalized);
      else skipped += 1;
    }
    return { format, visits, skipped };
  }

  function dateTime(value) {
    const parts = Object.fromEntries(kst.formatToParts(new Date(value)).map((part) => [part.type, part.value]));
    return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
  }

  function distance(a, b) {
    const radians = Math.PI / 180;
    const dLat = (a.lat - b.lat) * radians;
    const dLng = (a.lng - b.lng) * radians;
    const value = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * radians) * Math.cos(b.lat * radians) * Math.sin(dLng / 2) ** 2;
    return 6371000 * 2 * Math.asin(Math.min(1, Math.sqrt(value)));
  }

  function match(visits, events) {
    if (!Array.isArray(visits) || !Array.isArray(events)) throw failure("invalid", "Invalid visits or catalog");
    if (visits.length > MAX_RECORDS) throw failure("tooMany", "100000 record limit");
    const places = events.map((event) => ({ event, coordinates: window.NotchimaDirections.coordinates(event) }))
      .filter(({ event, coordinates }) => coordinates && event && typeof event.id === "string");
    const results = [];
    const seen = new Set();
    for (const visit of visits) {
      if (!validVisit(visit)) continue;
      const { date, time } = dateTime(visit.start);
      for (const { event, coordinates } of places) {
        const start = typeof event.start_date === "string" ? event.start_date.slice(0, 10) : "";
        const end = typeof (event.end_date || event.start_date) === "string" ? (event.end_date || event.start_date).slice(0, 10) : "";
        if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || date < start || date > end) continue;
        if (Math.abs(coordinates.lat - visit.lat) > .00136 || Math.abs(coordinates.lng - visit.lng) * Math.cos(visit.lat * Math.PI / 180) > .00136) continue;
        if (distance(visit, coordinates) > 150) continue;
        const key = JSON.stringify([event.id, date, time]);
        if (seen.has(key)) continue;
        seen.add(key);
        results.push({ event_id: event.id, date, time, visited: true, source: "google-export" });
        if (results.length > MAX_CANDIDATES) throw failure("tooMany", "Preview exceeds 1000 candidate limit; select a smaller export");
      }
    }
    return results;
  }

  function init({ getEvents, onImport }) {
    const input = $("historyFile");
    if (!input) return;
    let request = 0;
    let candidates = [];
    let selected = new Set();
    let summary = null;
    let statusKey = "";
    let statusValues = {};
    const setStatus = (key, values = {}) => {
      statusKey = key;
      statusValues = values;
      $("historyImportStatus").textContent = key ? t(key, values) : "";
    };
    const clear = () => {
      request += 1;
      input.value = "";
      candidates = [];
      selected.clear();
      summary = null;
      $("historyCandidates").replaceChildren();
      $("historyCandidates").hidden = true;
      $("historyConfirm").disabled = true;
    };
    const render = () => {
      const root = $("historyCandidates");
      root.replaceChildren();
      root.hidden = candidates.length === 0;
      const catalog = new Map(getEvents().map((event) => [event.id, event]));
      candidates.forEach((candidate, index) => {
        const label = document.createElement("label");
        label.className = "history-candidate";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = String(index);
        checkbox.checked = selected.has(index);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.add(index);
          else selected.delete(index);
          $("historyConfirm").disabled = selected.size === 0;
        });
        const copy = document.createElement("span");
        const event = catalog.get(candidate.event_id);
        copy.textContent = `${event ? event.name || event.id : candidate.event_id} · ${candidate.date} ${candidate.time}`;
        label.append(checkbox, copy);
        root.appendChild(label);
      });
      $("historyConfirm").disabled = selected.size === 0;
      if (statusKey) setStatus(statusKey, statusValues);
    };
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      clear();
      if (!file) { setStatus(""); return; }
      const token = request;
      if (file.size > MAX_BYTES) { setStatus("history.error.tooLarge"); return; }
      setStatus("history.reading");
      try {
        const text = await file.text();
        if (token !== request) return;
        const parsed = parse(text);
        candidates = match(parsed.visits, getEvents());
        summary = { visits: parsed.visits.length, candidates: candidates.length, skipped: parsed.skipped };
        setStatus(candidates.length ? "history.previewSummary" : "history.noMatches", summary);
        render();
      } catch (error) {
        if (token !== request) return;
        const code = ["invalid", "unsupported", "tooLarge", "tooMany"].includes(error && error.code) ? error.code : "read";
        setStatus(`history.error.${code}`);
      }
    });
    $("historyCancel").addEventListener("click", () => { clear(); setStatus("history.cancelled"); });
    $("historyConfirm").addEventListener("click", () => {
      if (!selected.size) return;
      const chosen = candidates.filter((_, index) => selected.has(index));
      $("historyConfirm").disabled = true;
      try {
        const result = onImport(chosen);
        clear();
        setStatus("history.saved", result);
      } catch (_) { setStatus("history.error.save"); $("historyConfirm").disabled = false; }
    });
    window.addEventListener("notchima:languagechange", render);
  }

  window.NotchimaTimelineImport = { parse, match, init };
})();
