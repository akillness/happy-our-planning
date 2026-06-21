/* Happy Our Planning — 정적 프런트엔드.
   data/*.json 을 읽어 지도·필터·검색·AI 추천을 모두 클라이언트에서 처리. */
(function () {
  "use strict";

  const DATA = "./data/";
  const state = { events: [], regions: [], facets: {}, fuse: null, markers: null, map: null };

  const $ = (id) => document.getElementById(id);
  const dpart = (s) => (s || "").slice(0, 10);
  const isFree = (p) => p === "free" || p === 0;

  async function getJSON(name) {
    const r = await fetch(DATA + name);
    if (!r.ok) throw new Error(name + " 로드 실패");
    return r.json();
  }

  function initMap() {
    state.map = L.map("map").setView([36.5, 127.8], 7);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© OpenStreetMap",
    }).addTo(state.map);
    state.markers = L.layerGroup().addTo(state.map);
  }

  function fillSelect(sel, items) {
    for (const it of items) {
      const o = document.createElement("option");
      o.value = it.value; o.textContent = it.label;
      sel.appendChild(o);
    }
  }

  function buildFilters() {
    fillSelect($("sido"), state.regions.map((r) => ({ value: r.sido, label: `${r.sido} (${r.count})` })));
    const themes = Object.entries(state.facets.theme || {}).sort((a, b) => b[1] - a[1]);
    fillSelect($("theme"), themes.map(([t, c]) => ({ value: t, label: `${t} (${c})` })));
    const ages = Object.entries(state.facets.age_band || {});
    fillSelect($("age"), ages.map(([a, c]) => ({ value: a, label: `${a} (${c})` })));
  }

  function currentFilters() {
    return {
      q: $("q").value.trim(),
      sido: $("sido").value,
      theme: $("theme").value,
      age: $("age").value,
      from: $("from").value,
      to: $("to").value,
      applyable: $("applyable").checked,
    };
  }

  // 필터 상태 ↔ URL 동기화(공유·새로고침 시 복원). 빈 값은 생략한다.
  const URL_KEYS = ["q", "sido", "theme", "age", "from", "to"];

  function writeURL(f) {
    const p = new URLSearchParams();
    for (const k of URL_KEYS) if (f[k]) p.set(k, f[k]);
    if (f.applyable) p.set("applyable", "1");
    const qs = p.toString();
    const url = qs ? "?" + qs : location.pathname;
    history.replaceState(null, "", url);
  }

  function restoreFromURL() {
    const p = new URLSearchParams(location.search);
    for (const k of URL_KEYS) if (p.has(k)) $(k).value = p.get(k);
    $("applyable").checked = p.get("applyable") === "1";
  }

  function isApplyable(e) {
    if (e.status !== "Open") return false;
    const today = new Date().toISOString().slice(0, 10);
    const s = dpart(e.application_start), en = dpart(e.application_end);
    if (s && today < s) return false;
    if (en && today > en) return false;
    return true;
  }

  function applyFilters() {
    const f = currentFilters();
    let rows = state.events;
    if (f.q && state.fuse) rows = state.fuse.search(f.q).map((r) => r.item);
    rows = rows.filter((e) => {
      if (f.sido && e.sido !== f.sido) return false;
      if (f.theme && !(e.themes || []).includes(f.theme)) return false;
      if (f.age && !(e.age_bands || []).includes(f.age)) return false;
      if (f.from && dpart(e.end_date || e.start_date) < f.from) return false;
      if (f.to && dpart(e.start_date) > f.to) return false;
      if (f.applyable && !isApplyable(e)) return false;
      return true;
    });
    render(rows);
    writeURL(f);
  }

  function badge(e) {
    const b = [];
    if (isFree(e.price)) b.push('<span class="badge free">무료</span>');
    if (e.status === "Open") b.push('<span class="badge open">신청가능</span>');
    if (e.event_type) b.push(`<span class="badge">${e.event_type}</span>`);
    if (e.verification === "web-discovered") {
      const c = e.confidence != null ? ` ${Math.round(e.confidence * 100)}%` : "";
      b.push(`<span class="badge discovered" title="웹검색으로 발견된 미검증 후보">🔎 발견${c}</span>`);
    }
    return b.join(" ");
  }

  function render(rows) {
    $("count").textContent = rows.length;
    const list = $("list");
    list.innerHTML = "";
    state.markers.clearLayers();
    for (const e of rows) {
      const li = document.createElement("li");
      li.className = "card";
      li.innerHTML = `<h3>${e.name}</h3>
        <div class="meta">
          <span>📍 ${e.sido || "-"}${e.sigungu ? " " + e.sigungu : ""}</span>
          <span>🗓 ${dpart(e.start_date)}${e.end_date ? " ~ " + dpart(e.end_date) : ""}</span>
          ${e.age ? `<span>👥 ${e.age}</span>` : ""}
          ${(e.themes || []).map((t) => `<span class="badge">${t}</span>`).join(" ")}
          ${badge(e)}
        </div>`;
      li.addEventListener("click", () => showDetail(e));
      list.appendChild(li);
      if (e.lat && e.lng) {
        L.marker([e.lat, e.lng]).addTo(state.markers).bindPopup(`<b>${e.name}</b><br>${e.sido || ""}`);
      }
    }
  }

  function showDetail(e) {
    const d = $("detail");
    d.innerHTML = `<button class="close" aria-label="닫기">×</button>
      <h2>${e.name}</h2>
      <p>${e.description || ""}</p>
      <div class="meta">📍 ${e.sido || ""} ${e.sigungu || ""} · 🗓 ${dpart(e.start_date)}${e.end_date ? " ~ " + dpart(e.end_date) : ""}</div>
      ${e.application_end ? `<p>신청마감: <b>${dpart(e.application_end)}</b></p>` : ""}
      <p>출처: ${e.source} · ${isFree(e.price) ? "무료" : (e.price ?? "정보없음")}</p>
      <a href="${e.url}" target="_blank" rel="noopener">신청/상세 페이지</a>
      <button class="act" data-act="macro">신청 매크로 등록</button>
      <button class="act" data-act="alarm">🔔 알람 켜기</button>`;
    d.querySelector(".close").onclick = () => d.close();
    d.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = () => alert(
        btn.dataset.act === "macro"
          ? "신청 매크로 잡으로 등록됩니다(데모). 신청기간 시작 시 Playwright 러너가 실행됩니다."
          : "이 조건의 신규/마감 알람을 구독합니다(데모)."
      );
    });
    d.showModal();
  }

  /* 클라이언트 규칙 기반 추천 (scripts/recommend/rank.py 와 동일 로직 축약) */
  function recommend(profile) {
    const scored = [];
    for (const e of state.events) {
      let s = 0; const why = [];
      if (profile.regions.includes(e.sido)) { s += 3; why.push("지역일치"); }
      const tm = (e.themes || []).filter((t) => profile.themes.includes(t));
      if (tm.length) { s += 2 * tm.length; why.push("테마:" + tm.join(",")); }
      if (profile.age && (e.age_bands || []).includes(profile.age)) { s += 2; why.push("나이대일치"); }
      if (isFree(e.price)) { s += 1; why.push("무료"); }
      else if (profile.freeOnly) { continue; }
      if (e.status === "Open") { s += 2; why.push("신청가능"); }
      if (s > 0) scored.push({ e, s, why });
    }
    scored.sort((a, b) => b.s - a.s);
    return scored.slice(0, 5);
  }

  function showAI() {
    const profile = {
      regions: $("sido").value ? [$("sido").value] : state.regions.map((r) => r.sido),
      themes: $("theme").value ? [$("theme").value] : Object.keys(state.facets.theme || {}),
      age: $("age").value || null,
      freeOnly: false,
    };
    const recs = recommend(profile);
    const d = $("aiDialog");
    d.innerHTML = `<button class="close" aria-label="닫기">×</button>
      <h2>🧠 AI 추천 플랜 <small>(규칙 기반)</small></h2>
      <p>프로필: 지역 ${profile.regions.length}개 · 테마 ${profile.themes.join("/") || "전체"} · 나이대 ${profile.age || "전체"}</p>
      ${recs.length ? "<ol>" + recs.map((r) =>
        `<li><b>${r.e.name}</b> — ${r.e.sido || ""} <span class="badge">점수 ${r.s}</span><br>
         <small>${r.why.join(" · ")}</small></li>`).join("") + "</ol>"
        : "<p>조건에 맞는 추천이 없습니다.</p>"}
      <p><small>키 설정 시 ai-proxy(Gemini/Groq)가 동선·이유를 보강합니다.</small></p>`;
    d.querySelector(".close").onclick = () => d.close();
    d.showModal();
  }

  function wire() {
    ["q", "sido", "theme", "age", "from", "to"].forEach((id) =>
      $(id).addEventListener("input", applyFilters));
    $("applyable").addEventListener("change", applyFilters);
    $("reset").addEventListener("click", () => {
      ["q", "sido", "theme", "age", "from", "to"].forEach((id) => ($(id).value = ""));
      $("applyable").checked = false; applyFilters();
    });
    $("aiBtn").addEventListener("click", showAI);
    window.addEventListener("popstate", () => { restoreFromURL(); applyFilters(); });
  }

  async function main() {
    initMap();
    try {
      const [events, facets, regions, updated] = await Promise.all([
        getJSON("events.json"), getJSON("facets.json"),
        getJSON("regions.json"), getJSON("updated.json"),
      ]);
      state.events = events; state.facets = facets; state.regions = regions;
      state.fuse = new Fuse(events, { keys: ["name", "description", "themes"], threshold: 0.4 });
      $("freshness").textContent = `갱신 ${updated.generated_at} · 활성 ${updated.total_active}건`;
      buildFilters(); wire(); restoreFromURL(); applyFilters();
    } catch (err) {
      $("freshness").textContent = "데이터 로드 실패: " + err.message;
    }
  }

  document.addEventListener("DOMContentLoaded", main);
})();
