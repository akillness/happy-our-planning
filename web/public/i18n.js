/* 놓치마 i18n 어댑터 — 의존성 없는 KO/EN 런타임 전환.
 * 단일 번역 홈은 ./locales.json (평평한 ko/en 키). 런타임 번역 모델/유료 호출 없음.
 * API: window.NotchimaI18n = { ready, t, getLanguage, setLanguage, apply, label, formatDate, formatMoney }
 *
 * lang 복원 순서: URL ?lang= > localStorage > html[lang] > 기본 "ko".
 * URL 쓰기 규칙: 초기 로드에서 기존 빈 URL에 기본값(ko)을 강제로 추가하지 않는다(레거시 빈 URL
 * 의미 보존). URL에 이미 lang이 있었거나 사용자가 명시적으로 전환한 뒤부터만 history 쓰기에
 * lang을 동행시킨다. localStorage 접근이 막혀 있어도(프라이빗 모드 등) URL만으로 언어를 잃지
 * 않도록 전환 시 항상 URL에 기록한다.
 */
(function () {
  "use strict";

  const SUPPORTED = ["ko", "en"];
  const DEFAULT_LANG = "ko";
  const STORAGE_KEY = "notchima:lang";
  // Resolve beside this script so static detail pages under /events can load the
  // shared catalog from ../locales.json as well as the map at ./locales.json.
  const scriptSource = document.currentScript && document.currentScript.src;
  const CATALOG_URL = scriptSource ? new URL("locales.json", scriptSource).href : "./locales.json";
  const EVENT_NAME = "notchima:languagechange";

  const LANGUAGE_NAMES = { ko: "한국어", en: "English" };

  const state = {
    catalog: { ko: {}, en: {} },
    lang: DEFAULT_LANG,
    explicit: false, // true once the URL carried ?lang= on load, or the user toggled this session
    bound: false,
    loaded: false,
  };

  function safeStorageGet(key) {
    try {
      return window.localStorage ? window.localStorage.getItem(key) : null;
    } catch (_) {
      return null;
    }
  }

  function safeStorageSet(key, value) {
    try {
      if (window.localStorage) window.localStorage.setItem(key, value);
    } catch (_) {
      // Private mode / storage disabled — URL remains the fallback channel.
    }
  }

  function normalizeLang(value) {
    const lower = String(value || "").toLowerCase();
    return SUPPORTED.includes(lower) ? lower : null;
  }

  function readURLLang() {
    try {
      return normalizeLang(new URLSearchParams(window.location.search).get("lang"));
    } catch (_) {
      return null;
    }
  }

  function writeURLLang(lang, { push = false } = {}) {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("lang", lang);
      const target = `${url.pathname}?${url.searchParams.toString()}${url.hash}`;
      if (push) window.history.pushState(window.history.state, "", target);
      else window.history.replaceState(window.history.state, "", target);
    } catch (_) {
      // Non-browser/test environment without a usable History API — ignore.
    }
  }

  // Patches history.pushState/replaceState so any later URL rewrite performed
  // by app.js (filters/page/reset) keeps carrying ?lang= once this session
  // has made the language explicit — without app.js needing to know about
  // language at all. No-ops entirely until state.explicit flips true, so the
  // "don't append default lang on a bare initial URL" rule still holds.
  let reconciling = false;
  function patchHistory() {
    if (typeof window.history !== "object" || window.history.__notchimaLangPatched) return;
    window.history.__notchimaLangPatched = true;
    ["pushState", "replaceState"].forEach((method) => {
      const original = window.history[method];
      if (typeof original !== "function") return;
      window.history[method] = function (...args) {
        const result = original.apply(this, args);
        if (!reconciling && state.explicit) {
          reconciling = true;
          try {
            const url = new URL(window.location.href);
            if (url.searchParams.get("lang") !== state.lang) {
              url.searchParams.set("lang", state.lang);
              original.call(window.history, window.history.state, "",
                `${url.pathname}?${url.searchParams.toString()}${url.hash}`);
            }
          } catch (_) {
            // ignore
          } finally {
            reconciling = false;
          }
        }
        return result;
      };
    });
  }

  function interpolate(template, vars) {
    if (!vars) return template;
    return template.replace(/\{(\w+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
    ));
  }

  function t(key, vars) {
    const table = state.catalog[state.lang] || {};
    const fallbackTable = state.catalog[DEFAULT_LANG] || {};
    const value = Object.prototype.hasOwnProperty.call(table, key) ? table[key]
      : Object.prototype.hasOwnProperty.call(fallbackTable, key) ? fallbackTable[key] : null;
    if (value == null) {
      if (typeof console !== "undefined" && console.warn) console.warn(`NotchimaI18n: missing key "${key}"`);
      return key;
    }
    return interpolate(value, vars);
  }

  // Controlled-vocabulary lookup for canonical Korean filter VALUES (sido,
  // theme, age band, activity category, schema.org event_type). The value
  // itself never changes — only its *display* label does. Unknown values are
  // returned unchanged rather than guessed, never hallucinated.
  function label(value, kind) {
    if (value == null || value === "") return value;
    const key = `vocab.${kind}.${value}`;
    const table = state.catalog[state.lang] || {};
    if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
    return value;
  }

  function pad2(n) { return String(n).padStart(2, "0"); }

  // Display-only date formatting. Never touches the canonical YYYY-MM-DD
  // strings used for filtering/URL/comparison elsewhere in the app.
  function formatDate(value) {
    if (!value) return t("date.noSchedule");
    const iso = String(value).slice(0, 10);
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    if (!m) return iso;
    const [, y, mo, d] = m;
    if (state.lang === "en") {
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const idx = Number.parseInt(mo, 10) - 1;
      const monthName = months[idx] || mo;
      return `${monthName} ${Number.parseInt(d, 10)}, ${y}`;
    }
    return t("date.ko.full", { y, m: Number.parseInt(mo, 10), d: Number.parseInt(d, 10) });
  }

  function formatMoney(price) {
    if (price === "free" || price === 0 || price === "0") return t("price.free");
    if (price == null || price === "") return t("price.unknown");
    if (typeof price === "number") {
      return t("price.won", { amount: price.toLocaleString(state.lang === "en" ? "en-US" : "ko-KR") });
    }
    return String(price);
  }

  function setTextPreservingChildren(node, text) {
    if (!node) return;
    const children = Array.from(node.childNodes || []);
    const textNodes = children.filter((child) => child.nodeType === 3);
    if (!textNodes.length) {
      node.appendChild(document.createTextNode(String(text)));
      return;
    }
    textNodes[0].nodeValue = String(text);
    for (const extra of textNodes.slice(1)) extra.nodeValue = "";
  }

  function translatedOrNull(key, vars) {
    const value = t(key, vars);
    if (value == null || value === "" || value === key) return null;
    return value;
  }

  function applyNode(node) {
    const templateKey = node.getAttribute("data-i18n-template");
    if (templateKey) {
      const value = node.getAttribute("data-i18n-value") || node.getAttribute("value") || "";
      const translated = translatedOrNull(templateKey, { value });
      if (translated !== null) setTextPreservingChildren(node, translated);
    }
    const statusKey = node.getAttribute("data-status-key");
    if (statusKey) {
      const translated = translatedOrNull(statusKey);
      if (translated !== null) setTextPreservingChildren(node, translated);
    }
    const key = node.getAttribute("data-i18n");
    if (key) {
      const label = translatedOrNull(key);
      if (label !== null) {
        const controlLabel = node.querySelector && node.querySelector(".control-label");
        if (controlLabel) controlLabel.textContent = label;
        else setTextPreservingChildren(node, label);
      }
    }
    for (const attr of ["placeholder", "aria-label", "title", "alt"]) {
      const attrKey = node.getAttribute(`data-i18n-${attr}`);
      if (attrKey) {
        const translated = translatedOrNull(attrKey);
        if (translated !== null) node.setAttribute(attr, translated);
      }
    }
  }


  // Walks data-i18n, template/value, status, and data-i18n-* attribute hooks.
  // Text replacement preserves child controls/icons; only direct text nodes are
  // changed when a translated label wraps a select or SVG control.
  function apply(root) {
    if (!state.loaded) return;
    const scope = root || document;
    if (scope.hasAttribute && /data-(?:i18n|status-key)/.test(scope.outerHTML || "")) applyNode(scope);
    const nodes = scope.querySelectorAll
      ? scope.querySelectorAll("[data-i18n], [data-i18n-template], [data-status-key], [data-i18n-placeholder], [data-i18n-aria-label], [data-i18n-title], [data-i18n-alt]")
      : [];
    nodes.forEach(applyNode);
    if (document.documentElement) document.documentElement.lang = state.lang;
  }

  function dispatchChange(previous) {
    try {
      window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { language: state.lang, previous } }));
    } catch (_) {
      // Environments without CustomEvent (none expected in target browsers) — ignore.
    }
  }

  // Core setter. `explicit` marks a deliberate choice (URL already had ?lang=
  // on load, or a user-driven toggle) — only then do we start writing ?lang=
  // into the URL on every subsequent history write. `silent` skips the
  // languagechange event (used during initial resolution, where there is no
  // real "previous" language yet to react to).
  function applyLanguage(lang, { explicit = false, persist = false, silent = false } = {}) {
    const next = normalizeLang(lang) || DEFAULT_LANG;
    const previous = state.lang;
    state.lang = next;
    if (explicit) state.explicit = true;
    if (document.documentElement) document.documentElement.lang = next;
    if (persist) safeStorageSet(STORAGE_KEY, next);
    if (state.explicit) writeURLLang(next);
    if (state.loaded) apply(document);
    if (!silent && previous !== next) dispatchChange(previous);
    return next;
  }

  function setLanguage(lang) {
    return applyLanguage(lang, { explicit: true, persist: true, silent: false });
  }

  function getLanguage() {
    return state.lang;
  }

  function resolveInitialLanguage() {
    const urlLang = readURLLang();
    if (urlLang) return { lang: urlLang, explicit: true };
    const storedLang = normalizeLang(safeStorageGet(STORAGE_KEY));
    if (storedLang) return { lang: storedLang, explicit: false };
    const htmlLang = document.documentElement ? normalizeLang(document.documentElement.lang) : null;
    return { lang: htmlLang || DEFAULT_LANG, explicit: false };
  }

  function bindToggle() {
    if (state.bound) return;
    const button = document.getElementById("languageToggle");
    if (!button) return;
    state.bound = true;
    const sync = () => {
      if (!state.loaded) return;
      const target = state.lang === "ko" ? "en" : "ko";
      const text = translatedOrNull(target === "en" ? "tool.languageToggle.switchToEn" : "tool.languageToggle.switchToKo");
      if (text === null) return;
      button.setAttribute("aria-label", text);
      button.title = text;
      const span = button.querySelector(".control-label");
      if (span) span.textContent = text;
      else {
        const textNode = Array.from(button.childNodes || []).find((child) => child.nodeType === 3);
        if (textNode) textNode.nodeValue = text;
        else button.appendChild(document.createTextNode(text));
      }
    };
    button.addEventListener("click", () => {
      setLanguage(state.lang === "ko" ? "en" : "ko");
    });
    window.addEventListener(EVENT_NAME, sync);
    sync();
  }

  function init() {
    patchHistory();
    const resolved = resolveInitialLanguage();
    state.lang = resolved.lang;
    state.explicit = resolved.explicit;
    if (document.documentElement) document.documentElement.lang = state.lang;

    const ready = fetch(CATALOG_URL, { cache: "no-cache" })
      .then((response) => {
        if (!response.ok) throw new Error(`locales.json load failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        state.catalog = {
          ko: (data && data.ko) || {},
          en: (data && data.en) || {},
        };
        state.loaded = true;
      })
      .catch((error) => {
        if (typeof console !== "undefined" && console.error) console.error("NotchimaI18n: catalog load failed", error);
        state.catalog = { ko: {}, en: {} };
      })
      .then(() => {
        if (state.loaded) apply(document);
        bindToggle();
      });

    window.addEventListener("popstate", () => {
      const urlLang = readURLLang();
      if (urlLang) applyLanguage(urlLang, { explicit: true });
    });

    window.NotchimaI18n = {
      ready,
      t,
      getLanguage,
      setLanguage,
      apply,
      label,
      formatDate,
      formatMoney,
      isLoaded: () => state.loaded,
      LANGUAGE_NAMES,
    };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
