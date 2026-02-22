/* /static/app.js
   =============================================================================
   PartyBox UI controller (Admin + User pages)
   - Live refresh: /api/state, /api/queue, /api/catalog (if present)
   - AJAX-post admin actions (forms/buttons) then refresh UI immediately
   - Highlight active mode: Mute / PartyBox / Spotify
   =============================================================================
*/

(function () {
  "use strict";

  // -----------------------------
  // Config
  // -----------------------------
  const POLL_STATE_MS = 1000;
  const POLL_QUEUE_MS = 1200;
  const POLL_CATALOG_MS = 2500;

  // If your API paths differ, change these 3 only.
  const API_STATE = "/api/state";
  const API_QUEUE = "/api/queue";
  const API_CATALOG = "/api/catalog"; // optional; if 404 we just stop asking

  // -----------------------------
  // Helpers
  // -----------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function fetchJson(url, opts = {}) {
    const res = await fetch(url, { cache: "no-store", ...opts });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      const err = new Error(`HTTP ${res.status} for ${url}`);
      err.status = res.status;
      err.body = text;
      throw err;
    }
    return res.json();
  }

  async function postFormAjax(form) {
    const action = form.getAttribute("action") || window.location.href;
    const method = (form.getAttribute("method") || "POST").toUpperCase();

    const fd = new FormData(form);

    // If action has query string already, keep it.
    const res = await fetch(action, {
      method,
      body: fd,
      credentials: "same-origin",
      headers: {
        // Helps Flask treat it like a normal form post in many setups
        "X-Requested-With": "XMLHttpRequest",
      },
    });

    if (!res.ok) {
      const t = await res.text().catch(() => "");
      const err = new Error(`POST failed ${res.status}`);
      err.status = res.status;
      err.body = t;
      throw err;
    }

    // Some endpoints may return JSON; others may redirect or return HTML.
    // We don’t care — success is success.
    return true;
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  // -----------------------------
  // UI: Toast (optional)
  // -----------------------------
  let toastTimer = null;
  function toast(msg, kind = "ok", ms = 1600) {
    let el = $("#pb_toast");
    if (!el) return;

    el.classList.remove("hidden");
    el.textContent = msg;

    el.classList.remove("ok", "warn", "danger");
    el.classList.add(kind);

    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
  }

  // -----------------------------
  // State + Queue + Catalog (polling)
  // -----------------------------
  let lastState = null;
  let lastQueue = null;
  let lastCatalog = null;

  let catalogSupported = true; // turns false if API_CATALOG 404

  async function refreshState() {
    try {
      const st = await fetchJson(API_STATE);
      lastState = st;
      applyStateToUi(st);
    } catch (e) {
      // If state fails, show offline pill if present
      applyOfflineUi();
    }
  }

  async function refreshQueue() {
    try {
      const q = await fetchJson(API_QUEUE);
      lastQueue = q;
      applyQueueToUi(q);
    } catch (e) {
      // ignore; state might still be okay
    }
  }

  async function refreshCatalog() {
    if (!catalogSupported) return;
    try {
      const c = await fetchJson(API_CATALOG);
      lastCatalog = c;
      applyCatalogToUi(c);
    } catch (e) {
      if (e && e.status === 404) {
        catalogSupported = false;
        // Don’t spam the server if /api/catalog doesn’t exist in this build.
        return;
      }
    }
  }

  // -----------------------------
  // Apply: State -> UI
  // -----------------------------
  function applyOfflineUi() {
    // These IDs are optional; we only update if they exist
    const onlineDot = $("#sys_dot");
    const onlineText = $("#sys_text");
    if (onlineDot) {
      onlineDot.classList.remove("ok");
      onlineDot.classList.add("bad");
    }
    if (onlineText) onlineText.textContent = "System Offline";
  }

  function normalizeMode(st) {
    // We try a few likely shapes without assuming your backend
    // Examples:
    //   st.av_mode = "partybox" | "spotify"
    //   st.mode = "partybox" | "spotify"
    //   st.audio_mode = ...
    //   st.muted = true
    const muted = !!(st.muted ?? st.audio_muted ?? st.audio?.muted);
    let mode =
      (st.av_mode ?? st.mode ?? st.audio_mode ?? st.audio?.mode ?? "").toString().toLowerCase();

    // If backend returns "PartyBox" / "Spotify"
    if (mode.includes("party")) mode = "partybox";
    if (mode.includes("spot")) mode = "spotify";

    return { muted, mode };
  }

  function applyStateToUi(st) {
    // Online
    const onlineDot = $("#sys_dot");
    const onlineText = $("#sys_text");
    if (onlineDot) {
      onlineDot.classList.remove("bad");
      onlineDot.classList.add("ok");
    }
    if (onlineText) onlineText.textContent = "System Online";

    // Now playing
    const nowTitle =
      st.now_playing_title ??
      st.now_playing?.title ??
      st.now?.title ??
      st.now_playing ??
      "";
    const nowLocal =
      st.now_playing_local ??
      st.now_playing?.local ??
      st.now?.local ??
      st.now_playing_path ??
      "";

    setText("np_title", nowTitle || "—");
    setText("np_local", nowLocal || "");

    // Playback status
    const playback =
      (st.playback ?? st.playback_state ?? st.player_state ?? "").toString().toLowerCase();
    const isPlaying =
      st.is_playing ?? st.playing ?? (playback === "playing") ?? false;
    setText("np_playback", isPlaying ? "Playing" : (playback ? playback : "—"));

    // Requests lock
    const locked = !!(st.requests_locked ?? st.locked ?? st.requests?.locked);
    setText("np_requests", locked ? "Locked" : "Open");

    // Mode + mute (highlight buttons)
    const { muted, mode } = normalizeMode(st);

    // Update top pills if present
    setText("pill_mode", muted ? "Mute" : (mode ? (mode === "spotify" ? "Spotify" : "PartyBox") : "—"));
    setText("pill_audio", muted ? "Audio Off" : "Audio On");

    // Button highlight logic (optional IDs)
    setModeButtons(muted, mode);

    // Requests open/locked pill
    const reqText = $("#pill_requests");
    if (reqText) reqText.textContent = locked ? "Requests Locked" : "Requests Open";

    // Playback pill
    const playText = $("#pill_playback");
    if (playText) playText.textContent = isPlaying ? "Playing" : "Paused";
  }

  function setModeButtons(muted, mode) {
    // Buttons can be identified by IDs OR data-mode attributes.
    // Works with either:
    //   id="btn_mute" / id="btn_partybox" / id="btn_spotify"
    // OR
    //   data-mode="mute|partybox|spotify" on buttons

    const btnMute = $("#btn_mute") || $('[data-mode="mute"]');
    const btnPB = $("#btn_partybox") || $('[data-mode="partybox"]');
    const btnSp = $("#btn_spotify") || $('[data-mode="spotify"]');

    const all = [btnMute, btnPB, btnSp].filter(Boolean);

    // Remove active class
    all.forEach((b) => {
      b.classList.remove("isActive");
      b.setAttribute("aria-pressed", "false");
    });

    // Determine which is active
    let active = null;
    if (muted) active = btnMute;
    else if (mode === "spotify") active = btnSp;
    else if (mode === "partybox" || mode === "") active = btnPB;

    if (active) {
      active.classList.add("isActive");
      active.setAttribute("aria-pressed", "true");
    }
  }

  // -----------------------------
  // Apply: Queue -> UI
  // -----------------------------
  function applyQueueToUi(q) {
    // expects {items:[...]} or [...]
    const items = Array.isArray(q) ? q : (q.items || q.queue || []);
    const host = $("#queue_list");
    const empty = $("#queue_empty");

    if (!host) return;

    if (!items || items.length === 0) {
      host.innerHTML = "";
      if (empty) empty.style.display = "";
      setText("queue_count", "0 item(s)");
      return;
    }

    if (empty) empty.style.display = "none";
    setText("queue_count", `${items.length} item(s)`);

    host.innerHTML = items
      .map((it, idx) => {
        const title = (it.title ?? it.name ?? it.display ?? "Untitled").toString();
        const who = (it.requested_by ?? it.user ?? it.by ?? "").toString();
        const src = (it.youtube_id ?? it.url ?? it.local ?? it.source ?? "").toString();

        return `
          <div class="pb-queueRow">
            <div class="pb-queueIdx">${idx + 1}</div>
            <div style="min-width:0;flex:1;">
              <div class="pb-queueTitle ellip">${escapeHtml(title)}</div>
              <div class="pb-queueMeta ellip">
                ${who ? `by <b>${escapeHtml(who)}</b> • ` : ""}
                ${src ? escapeHtml(src) : ""}
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  // -----------------------------
  // Apply: Catalog -> UI (optional)
  // -----------------------------
  function applyCatalogToUi(c) {
    const host = $("#catalog_list");
    const count = $("#catalog_count");
    if (!host) return;

    // expects {items:[...]} or [...]
    const items = Array.isArray(c) ? c : (c.items || c.catalog || []);
    if (count) count.textContent = `${items.length} item(s)`;

    host.innerHTML = items
      .map((it) => {
        const id = it.id;
        const title = (it.title ?? it.name ?? "Untitled").toString();
        const y = (it.youtube_id ?? it.url ?? it.local ?? "").toString();
        const enabled = !!(it.enabled ?? it.is_enabled ?? it.active);

        // We DO NOT assume endpoints here.
        // The admin template should already have working forms/buttons for enable/disable.
        // If your template uses data attributes, we still render a readable row.
        return `
          <div class="listrow ${enabled ? "" : "pb-disabled"}" data-catalog-row="1" data-id="${escapeHtml(id)}">
            <div class="grow" style="min-width:0;">
              <div class="title ellip">${escapeHtml(title)}</div>
              <div class="sub ellip">${escapeHtml(y)}</div>
            </div>
            <div class="badge">${enabled ? "Enabled" : "Disabled"}</div>
          </div>
        `;
      })
      .join("");
  }

  // -----------------------------
  // AJAX actions: make UI update immediately
  // -----------------------------
  function wireAjaxForms() {
    // Intercept ONLY forms that look like action buttons:
    // - forms with data-ajax="1"
    // - OR forms inside #catalog_actions, #catalog, #admin_actions, etc.
    document.addEventListener("submit", async (ev) => {
      const form = ev.target;
      if (!(form instanceof HTMLFormElement)) return;

      const ajaxMarked = form.dataset && (form.dataset.ajax === "1" || form.classList.contains("js-ajax"));
      const insideCatalog = !!form.closest("#catalog") || !!form.closest("#catalog_list") || !!form.closest("#catalog_panel");
      const insideAdminActions = !!form.closest("#admin_actions") || !!form.closest("#actions_panel") || !!form.closest("#pb_actions");

      // If it’s not clearly an action form, don’t touch it.
      if (!ajaxMarked && !insideCatalog && !insideAdminActions) return;

      ev.preventDefault();

      // Optimistic UI for enable/disable buttons:
      // if button has text Enable/Disable, flip immediately while we post.
      let clickedBtn = form.querySelector("button[type='submit'], input[type='submit']");
      const oldLabel = clickedBtn ? (clickedBtn.textContent || clickedBtn.value || "") : "";

      if (clickedBtn) {
        clickedBtn.disabled = true;
        if (clickedBtn.textContent) clickedBtn.textContent = "Working…";
      }

      try {
        await postFormAjax(form);

        // Refresh everything immediately so you don't need browser refresh
        await Promise.all([refreshState(), refreshQueue(), refreshCatalog()]);
        toast("Updated", "ok", 900);
      } catch (e) {
        console.error(e);
        toast("Action failed", "danger", 1800);
      } finally {
        if (clickedBtn) {
          clickedBtn.disabled = false;
          if (clickedBtn.textContent) clickedBtn.textContent = oldLabel || clickedBtn.textContent;
        }
      }
    });
  }

  function wireAjaxButtons() {
    // Optional: buttons with data-post="/some/url"
    // This lets you use simple <button data-post="/api/..."> without a form.
    document.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button[data-post]");
      if (!btn) return;

      ev.preventDefault();

      const url = btn.getAttribute("data-post");
      if (!url) return;

      const old = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Working…";

      try {
        await fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        await Promise.all([refreshState(), refreshQueue(), refreshCatalog()]);
        toast("Updated", "ok", 900);
      } catch (e) {
        console.error(e);
        toast("Action failed", "danger", 1800);
      } finally {
        btn.disabled = false;
        btn.textContent = old;
      }
    });
  }

  // -----------------------------
  // Add a tiny CSS hook for active mode buttons
  // (No need to modify style.css; we inject it safely)
  // -----------------------------
  function injectActiveCss() {
    const css = `
      button.isActive, .btn.isActive {
        box-shadow: 0 0 0 2px rgba(0,212,255,0.22), 0 0 0 6px rgba(255,45,175,0.10) !important;
        border-color: rgba(0,212,255,0.35) !important;
        transform: translateY(-1px);
      }
    `;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  // -----------------------------
  // Startup
  // -----------------------------
  async function main() {
    injectActiveCss();
    wireAjaxForms();
    wireAjaxButtons();

    // Initial refresh
    await Promise.allSettled([refreshState(), refreshQueue(), refreshCatalog()]);

    // Polling loops
    (async function loopState() {
      while (true) {
        await refreshState();
        await sleep(POLL_STATE_MS);
      }
    })();

    (async function loopQueue() {
      while (true) {
        await refreshQueue();
        await sleep(POLL_QUEUE_MS);
      }
    })();

    (async function loopCatalog() {
      while (true) {
        await refreshCatalog();
        await sleep(POLL_CATALOG_MS);
      }
    })();
  }

  // DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();