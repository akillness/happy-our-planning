/* 놓치마 외부 길찾기 링크 어댑터 — 키/SDK/추적 없이 URL만 만든다. */
(function () {
  "use strict";

  const LANGUAGE_CHANGE = "notchima:languagechange";
  const EVENT_SELECTOR = "[data-directions-event]";
  const PLACEHOLDER = /^(?:정보\s*없음|unknown|n\/?a|none|null|undefined)$/i;

  function text(value) {
    if (typeof value !== "string") return "";
    const trimmed = value.trim();
    return trimmed && !PLACEHOLDER.test(trimmed) ? trimmed : "";
  }

  function locationOf(event) {
    return event && event.location && typeof event.location === "object" && !Array.isArray(event.location)
      ? event.location
      : {};
  }

  function validCoordinate(value, min, max) {
    return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
  }

  function coordinateValue(event, key, min, max) {
    const location = locationOf(event);
    const values = [event && event[key], location[key]];
    for (const value of values) {
      if (validCoordinate(value, min, max)) return value;
    }
    return null;
  }
  function isCoarsePrecision(event) {
    const location = locationOf(event);
    return [event && event.geo_precision, location.geo_precision].some((value) => {
      const precision = text(value).toLowerCase();
      return precision === "sido-centroid" || precision.endsWith("-centroid");
    });
  }
  function coordinatePair(event) {
    if (isCoarsePrecision(event)) return null;
    const lat = coordinateValue(event, "lat", -90, 90);
    const lng = coordinateValue(event, "lng", -180, 180);
    return lat !== null && lng !== null && !(lat === 0 && lng === 0) ? { lat, lng } : null;
  }
  function coordinates(event) {
    const pair = coordinatePair(event);
    return pair ? { lat: pair.lat, lng: pair.lng } : null;
  }

  function firstText(...values) {
    for (const value of values) {
      const result = text(value);
      if (result) return result;
    }
    return "";
  }

  /**
   * Return a real place for an event, never an event title by itself.
   * Venue-level coordinates win when both are valid; coarse centroid values
   * fall back to address, then venue plus a region. A coordinate pair of 0,0
   * is deliberately rejected.
   */
  function destination(event) {
    if (!event || typeof event !== "object") return null;
    const coordinates = coordinatePair(event);
    if (coordinates) return `${coordinates.lat},${coordinates.lng}`;

    const location = locationOf(event);
    const address = firstText(event.address, location.address);
    if (address) return address;

    const venue = firstText(event.venue, location.venue, location.name);
    const region = firstText(
      [event.sido, event.sigungu].filter((value) => text(value)).join(" "),
      [location.sido, location.sigungu].filter((value) => text(value)).join(" "),
      event.region,
      location.region,
    );
    if (venue && region) return `${venue}, ${region}`;
    return null;
  }

  function encode(value) {
    return encodeURIComponent(String(value));
  }

  function googleUrl(destinationValue, originValue) {
    const params = ["api=1", "travelmode=transit"];
    if (originValue) params.push(`origin=${encode(originValue)}`);
    params.push(`destination=${encode(destinationValue)}`);
    return `https://www.google.com/maps/dir/?${params.join("&")}`;
  }

  function naverUrl(destinationValue) {
    return `https://map.naver.com/p/search/${encode(destinationValue)}`;
  }
  function destinationName(event, destinationValue) {
    const location = locationOf(event);
    return firstText(event && event.venue, location.venue, location.name, event && event.name,
      event && event.address, location.address) || destinationValue;
  }

  function naverApp(event) {
    const coordinates = coordinatePair(event);
    const target = destination(event);
    if (!coordinates || !target) return null;
    const name = destinationName(event, target);
    return `nmap://route/car?dlat=${coordinates.lat}&dlng=${coordinates.lng}&dname=${encode(name)}&appname=happy-our-planning.vercel.app`;
  }

  function kakaoUrl(event, destinationValue) {
    const coordinates = coordinatePair(event);
    if (coordinates) {
      const name = destinationName(event, destinationValue);
      return `https://map.kakao.com/link/to/${encode(name)},${coordinates.lat},${coordinates.lng}`;
    }
    return `https://map.kakao.com/?q=${encode(destinationValue)}`;
  }

  /**
   * Build keyless HTTPS URLs. The enumerable result intentionally stays the
   * three-key contract consumed by app.js/timeline.js. A supplied but invalid
   * origin disables Google only; Naver/Kakao remain destination-only searches.
   */
  function links(event, originEvent) {
    const target = destination(event);
    const result = { google: null, naver: null, kakao: null };
    if (!target) return result;

    const hasOrigin = originEvent !== null && originEvent !== undefined;
    const origin = hasOrigin ? destination(originEvent) : null;
    result.google = !hasOrigin || origin ? googleUrl(target, origin) : null;
    result.naver = naverUrl(target);
    result.kakao = kakaoUrl(event, target);
    return result;
  }

  function i18nText(key, fallback) {
    try {
      if (window.NotchimaI18n && typeof window.NotchimaI18n.t === "function") {
        const translated = window.NotchimaI18n.t(key);
        if (translated && translated !== key) return translated;
      }
    } catch (_) {
      // Fallback text keeps static pages usable if the catalog is unavailable.
    }
    return fallback;
  }

  function anchor(className, href, key, fallback) {
    if (!href) return null;
    const node = document.createElement("a");
    node.className = className;
    node.href = href;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    node.setAttribute("data-i18n", key);
    node.textContent = i18nText(key, fallback);
    return node;
  }

  function message(className, key, fallback) {
    const node = document.createElement("p");
    node.className = className;
    node.setAttribute("data-i18n", key);
    node.textContent = i18nText(key, fallback);
    return node;
  }
  function smallHint(className, key, fallback) {
    const node = document.createElement("small");
    node.className = className;
    node.setAttribute("data-i18n", key);
    node.textContent = i18nText(key, fallback);
    return node;
  }

  function applyI18n(root) {
    try {
      if (window.NotchimaI18n && typeof window.NotchimaI18n.apply === "function") {
        window.NotchimaI18n.apply(root);
      }
    } catch (_) {
      // The helper remains functional with fallback labels.
    }
  }

  /**
   * Append a directions control group to container. compact=true emits only
   * the Google action; full mode groups Naver/Kakao destination links
   * under details.directions-options and exposes a CSS-gated Naver app handoff.
   * The caller owns container lifecycle.
   */
  function append(container, event, options) {
    if (!container || typeof container.appendChild !== "function") return null;
    const config = options && typeof options === "object" ? options : {};
    const compact = config.compact === true;
    const originEvent = config.originEvent === undefined ? null : config.originEvent;
    const result = links(event, originEvent);
    const target = destination(event);
    const originUnavailable = originEvent !== null && originEvent !== undefined && !destination(originEvent);
    const actions = document.createElement("div");
    actions.className = "directions-actions";

    if (originUnavailable) {
      actions.appendChild(message(
        "directions-origin-note",
        "directions.originUnavailable",
        "이전 방문 장소를 확인할 수 없어 출발지를 직접 입력해야 합니다.",
      ));
    }

    const google = anchor(
      "directions-google",
      result.google,
      "directions.google",
      "Google 지도에서 대중교통 길찾기",
    );
    if (google) actions.appendChild(google);
    else if (!target) {
      actions.appendChild(message(
        "directions-unavailable",
        "directions.unavailable",
        "목적지 정보가 없어 길찾기 링크를 만들 수 없습니다.",
      ));
    }

    if (!compact && (result.naver || result.kakao)) {
      const optionsNode = document.createElement("details");
      optionsNode.className = "directions-options";
      const summary = document.createElement("summary");
      summary.setAttribute("data-i18n", "directions.extra");
      summary.textContent = i18nText("directions.extra", "다른 지도에서 목적지 확인");
      optionsNode.appendChild(summary);
      const list = document.createElement("div");
      list.className = "directions-options-list";
      const naver = anchor(
        "directions-naver",
        result.naver,
        "directions.naver",
        "네이버 지도에서 찾기",
      );
      const naverAppLink = anchor(
        "directions-naver-app",
        naverApp(event),
        "directions.naverApp",
        "네이버 내비 앱",
      );
      const naverAppHint = naverAppLink
        ? smallHint("directions-naver-app-hint", "directions.appHint", "네이버 앱이 설치되어 있으면 앱으로 열립니다.")
        : null;
      const kakao = anchor(
        "directions-kakao",
        result.kakao,
        "directions.kakao",
        "현재 위치에서 출발 · 카카오맵에서 목적지 확인",
      );
      if (naver) list.appendChild(naver);
      if (naverAppLink) list.appendChild(naverAppLink);
      if (naverAppHint) list.appendChild(naverAppHint);
      if (kakao) list.appendChild(kakao);
      optionsNode.appendChild(list);
      actions.appendChild(optionsNode);
    }

    container.dataset.directionsOriginUnavailable = originUnavailable ? "true" : "false";
    container.dataset.directionsDestinationAvailable = target ? "true" : "false";
    container.appendChild(actions);
    applyI18n(container);
    return container;
  }

  function readStaticEvent(host) {
    try {
      return JSON.parse(host.getAttribute("data-directions-event") || "null");
    } catch (_) {
      return null;
    }
  }

  function renderStaticHost(host) {
    const event = readStaticEvent(host);
    while (host.firstChild) host.removeChild(host.firstChild);
    append(host, event, { compact: false });
  }

  function initStatic() {
    const hosts = Array.from(document.querySelectorAll(EVENT_SELECTOR));
    if (!hosts.length) return;
    const render = () => hosts.forEach(renderStaticHost);
    render();
    window.addEventListener(LANGUAGE_CHANGE, render);
    try {
      if (window.NotchimaI18n && window.NotchimaI18n.ready && typeof window.NotchimaI18n.ready.then === "function") {
        window.NotchimaI18n.ready.then(() => hosts.forEach((host) => applyI18n(host)));
      }
    } catch (_) {
      // Fallback labels are already in the generated DOM.
    }
  }

  window.NotchimaDirections = { destination, links, append, naverApp, coordinates };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initStatic);
  else initStatic();
})();
