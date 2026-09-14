/* GimmeThatVid UI. Talks to Python through window.pywebview.api; Python pushes
   download events back through window.GTV.onEvent(). */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const reducedMotion = () =>
    document.documentElement.classList.contains("no-motion") ||
    matchMedia("(prefers-reduced-motion: reduce)").matches;

  const el = {
    viewHome: $("viewHome"), viewProgress: $("viewProgress"), viewDone: $("viewDone"),
    field: $("field"), linkInput: $("linkInput"), pasteBtn: $("pasteBtn"), clearBtn: $("clearBtn"),
    fieldHint: $("fieldHint"), reveal: $("reveal"),
    notice: $("notice"), noticeTitle: $("noticeTitle"), noticeDetail: $("noticeDetail"), noticeRetry: $("noticeRetry"),
    previewCard: $("previewCard"), thumbImg: $("thumbImg"), durationBadge: $("durationBadge"),
    previewTitle: $("previewTitle"), previewSub: $("previewSub"),
    qualityBtn: $("qualityBtn"), qualityLabel: $("qualityLabel"), qualityMenu: $("qualityMenu"),
    downloadBtn: $("downloadBtn"), sizeLabel: $("sizeLabel"),
    recents: $("recents"), recentList: $("recentList"), folderBtn: $("folderBtn"), folderLabel: $("folderLabel"),
    ambient: $("ambient"), ring: $("ring"), ringThumb: $("ringThumb"), ringBar: $("ringBar"), ringPct: $("ringPct"),
    progressStage: $("progressStage"), progressDetail: $("progressDetail"), progressTitle: $("progressTitle"),
    cancelBtn: $("cancelBtn"),
    doneBadge: $("doneBadge"), doneTitle: $("doneTitle"), player: $("player"), playerArt: $("playerArt"),
    fileName: $("fileName"), fileSub: $("fileSub"), fileExt: $("fileExt"), playBtn: $("playBtn"), revealBtn: $("revealBtn"), againBtn: $("againBtn"),
    toast: $("toast"),
  };

  const YOUTUBE = /^(?:https?:\/\/)?(?:(?:www|m|music)\.)?(?:youtube\.com\/(?:watch\?|shorts\/|live\/|embed\/)|youtu\.be\/)\S+$/i;
  const isYouTube = (text) => YOUTUBE.test((text || "").trim());

  const state = {
    view: "home",
    info: null,            // details of the video in the preview card
    infoUrl: "",           // the link those details belong to
    loading: false,
    seq: 0,                // guards against a slow, stale preview request winning
    quality: null,
    thumbUrl: "",          // whichever thumbnail actually loaded
    autoFilled: "",        // link we filled in from the clipboard ourselves
    dismissed: new Set(),  // clipboard links the user cleared or already downloaded
    downloading: false,
    file: null,
  };

  let api = null;

  /* ------------------------------------------------------------ formatting */
  function fmtBytes(bytes) {
    if (!bytes || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let n = bytes, i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
  }

  function fmtClock(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const pad = (v) => String(v).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
  }

  function fmtEta(seconds) {
    if (seconds == null || !isFinite(seconds)) return "";
    const s = Math.round(seconds);
    if (s < 4) return "almost done";
    if (s < 60) return `${s} sec left`;
    if (s < 3600) return `${Math.round(s / 60)} min left`;
    return `${Math.floor(s / 3600)} hr ${Math.round((s % 3600) / 60)} min left`;
  }

  function fmtWhen(unixSeconds) {
    if (!unixSeconds) return "";
    const date = new Date(unixSeconds * 1000);
    const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((dayStart(new Date()) - dayStart(date)) / 86400000);
    if (days <= 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days < 7) return date.toLocaleDateString("en-US", { weekday: "long" });
    return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  /* ----------------------------------------------------------------- views */
  const views = { home: el.viewHome, progress: el.viewProgress, done: el.viewDone };

  function showView(name) {
    if (state.view === name) return;
    state.view = name;
    document.body.dataset.view = name;
    for (const [key, node] of Object.entries(views)) {
      const active = key === name;
      node.classList.toggle("is-active", active);
      node.inert = !active;
    }
  }

  function setText(node, text, animate = true) {
    if (node.textContent === text) return;
    node.textContent = text;
    if (animate && !reducedMotion()) {
      node.animate(
        [{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }],
        { duration: 300, easing: "cubic-bezier(.32,.72,0,1)" }
      );
    }
  }

  let toastTimer = 0;
  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.toast.classList.remove("is-visible"), 2800);
  }

  /* ------------------------------------------------------------------ field */
  function setHint(text, kind = "") {
    el.fieldHint.textContent = text;
    el.fieldHint.classList.toggle("is-error", kind === "error");
    el.field.classList.toggle("is-invalid", kind === "error");
  }

  function syncFieldButtons() {
    const hasText = el.linkInput.value.length > 0;
    el.clearBtn.hidden = !hasText;
    el.pasteBtn.hidden = hasText;
  }

  function fillLink(url, source) {
    el.linkInput.value = url;
    syncFieldButtons();
    state.autoFilled = source === "clipboard" ? url : "";
    setHint(source === "clipboard" ? "Pasted from your clipboard" : "");
    return loadPreview(url);
  }

  function clearLink() {
    if (state.autoFilled) state.dismissed.add(state.autoFilled);
    el.linkInput.value = "";
    state.autoFilled = "";
    syncFieldButtons();
    setHint("");
    resetPreview();
    el.linkInput.focus();
  }

  async function pasteFromClipboard() {
    const url = await api.clipboard_url();
    if (url) {
      state.dismissed.delete(url);
      fillLink(url, "paste");
    } else {
      setHint("There's no YouTube link on your clipboard.", "error");
      el.field.animate(
        [{ transform: "translateX(0)" }, { transform: "translateX(-6px)" }, { transform: "translateX(5px)" },
         { transform: "translateX(-3px)" }, { transform: "translateX(0)" }],
        { duration: 380, easing: "ease-out" }
      );
    }
  }

  async function checkClipboard() {
    if (!api || state.view !== "home" || state.downloading) return;
    const current = el.linkInput.value.trim();
    if (current && current !== state.autoFilled) return;     // never overwrite what the user typed
    let url = "";
    try { url = await api.clipboard_url(); } catch { return; }
    if (!url || url === current || state.dismissed.has(url)) return;
    fillLink(url, "clipboard");
  }

  let typingTimer = 0;
  function onInput() {
    syncFieldButtons();
    state.autoFilled = "";
    clearTimeout(typingTimer);
    const value = el.linkInput.value.trim();

    if (!value) { setHint(""); resetPreview(); return; }
    if (isYouTube(value)) {
      setHint("");
      typingTimer = setTimeout(() => loadPreview(value), 220);
      return;
    }
    if (state.infoUrl && value !== state.infoUrl) resetPreview();
    typingTimer = setTimeout(() => {
      if (el.linkInput.value.trim() === value) setHint("That doesn't look like a YouTube link.", "error");
    }, 650);
  }

  /* ---------------------------------------------------------------- preview */
  function setHomeExpanded(expanded) {
    el.viewHome.classList.toggle("has-preview", expanded);
    el.reveal.classList.toggle("is-open", expanded);
  }

  function resetPreview() {
    closeQualityMenu(false);
    state.seq++;
    state.info = null;
    state.infoUrl = "";
    state.loading = false;
    hideNotice();
    setHomeExpanded(false);
  }

  function setThumb(img, primary, fallback, onReady) {
    img.classList.remove("is-loaded");
    let usedFallback = !primary;
    img.onload = () => {
      // YouTube answers a missing maxres thumbnail with a tiny grey placeholder.
      if (!usedFallback && img.naturalWidth <= 120 && fallback) {
        usedFallback = true;
        img.src = fallback;
        return;
      }
      img.classList.add("is-loaded");
      if (onReady) onReady(img.currentSrc || img.src);
    };
    img.onerror = () => {
      if (!usedFallback && fallback) { usedFallback = true; img.src = fallback; }
    };
    const src = primary || fallback;
    if (src) img.src = src; else img.removeAttribute("src");
  }

  function renderSkeleton() {
    el.previewCard.hidden = false;
    el.previewCard.classList.add("is-loading");
    el.thumbImg.classList.remove("is-loaded");
    el.thumbImg.removeAttribute("src");
    el.durationBadge.textContent = "";
    el.previewTitle.textContent = "";
    el.previewSub.textContent = "";
    el.sizeLabel.textContent = "";
    closeQualityMenu(false);
    el.qualityLabel.textContent = "";
    el.qualityBtn.disabled = true;
    el.downloadBtn.disabled = true;
    state.thumbUrl = "";
  }

  function renderInfo(info) {
    el.previewCard.classList.remove("is-loading");
    setThumb(el.thumbImg, info.thumb, info.thumb_fallback, (url) => { state.thumbUrl = url; });
    el.durationBadge.textContent = info.duration ? fmtClock(info.duration) : "";
    el.previewTitle.textContent = info.title;
    el.previewTitle.title = info.title;

    el.previewSub.textContent = info.channel;

    renderQualities(info.qualities, info.default_quality);
    el.downloadBtn.disabled = info.qualities.length === 0;
    if (!info.qualities.length) showNotice("No downloadable video formats were found for this link.");
  }

  async function loadPreview(url) {
    url = url.trim();
    if (url === state.infoUrl && (state.info || state.loading)) {
      setHomeExpanded(true);
      return;
    }
    const seq = ++state.seq;
    state.info = null;
    state.infoUrl = url;
    state.loading = true;
    hideNotice();
    renderSkeleton();
    setHomeExpanded(true);

    let result;
    try { result = await api.fetch_info(url); }
    catch (err) { result = { ok: false, error: "Couldn't read that video.", detail: String(err) }; }
    if (seq !== state.seq) return;                          // the link changed meanwhile
    state.loading = false;

    if (!result || !result.ok) {
      state.infoUrl = "";
      el.previewCard.hidden = true;
      showNotice(result?.error, result?.detail, () => loadPreview(url));
      return;
    }
    state.info = result.info;
    renderInfo(result.info);
  }

  /* ----------------------------------------------------------- quality menu */
  const CHECK_ICON =
    '<svg class="menu-check" viewBox="0 0 24 24" aria-hidden="true"><path d="M5.5 12.5l4 4L18.5 7.5"/></svg>';
  let menuOpen = false;

  function currentQuality() {
    return state.info?.qualities.find((q) => q.value === state.quality) || null;
  }

  function renderQualities(list, selected) {
    closeQualityMenu(false);
    const values = list.map((q) => q.value);
    state.quality = values.includes(selected) ? selected : values[0] ?? null;   // list is best first
    el.qualityBtn.disabled = list.length < 2;
    el.qualityBtn.title = list.length < 2 ? "The only quality this video has" : "Quality";
    updateQualityUi(false);
  }

  function updateQualityUi(animate) {
    const q = currentQuality();
    setText(el.qualityLabel, q ? q.short || q.label : "", animate);
    setText(el.sizeLabel, q?.size ? fmtBytes(q.size) : "", animate);
  }

  function selectQuality(value) {
    if (value !== state.quality) {
      state.quality = value;
      updateQualityUi(true);
    }
    closeQualityMenu(true);
  }

  function buildMenu() {
    const nodes = [];
    const items = [];
    (state.info?.qualities || []).forEach((q, index) => {
      if (q.kind === "audio" && items.length) {
        const separator = document.createElement("div");
        separator.className = "menu-separator";
        separator.setAttribute("role", "separator");
        nodes.push(separator);
      }

      const item = document.createElement("button");
      item.type = "button";
      item.className = "menu-item";
      item.dataset.value = q.value;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(q.value === state.quality));
      item.innerHTML = CHECK_ICON;

      const label = document.createElement("span");
      label.className = "menu-label";
      label.textContent = q.label;
      item.append(label);

      const tagText = q.kind === "audio" ? "MP3" : index === 0 ? "Best" : "";
      if (tagText) {
        const tag = document.createElement("span");
        tag.className = q.kind === "audio" ? "menu-tag is-neutral" : "menu-tag";
        tag.textContent = tagText;
        item.append(tag);
      }

      const size = document.createElement("span");
      size.className = "menu-size";
      size.textContent = fmtBytes(q.size);
      item.append(size);

      item.addEventListener("click", () => selectQuality(q.value));
      nodes.push(item);
      items.push(item);
    });
    el.qualityMenu.replaceChildren(...nodes);
    return items;
  }

  function positionQualityMenu() {
    const menu = el.qualityMenu;
    const anchor = el.qualityBtn.getBoundingClientRect();
    menu.style.minWidth = `${Math.max(210, anchor.width)}px`;

    // Invisible but laid out, so it can be measured before it's placed.
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    const margin = 10, gap = 6;
    const spaceBelow = window.innerHeight - anchor.bottom - margin;
    const openUp = spaceBelow < height + gap && anchor.top - margin > spaceBelow;
    const top = openUp
      ? anchor.top - gap - height
      : Math.min(anchor.bottom + gap, window.innerHeight - margin - height);
    menu.style.top = `${Math.max(margin, top)}px`;
    menu.style.left = `${Math.min(Math.max(margin, anchor.left), window.innerWidth - margin - width)}px`;
    menu.classList.toggle("opens-up", openUp);
  }

  function openQualityMenu() {
    if (menuOpen || el.qualityBtn.disabled || !state.info) return;
    const items = buildMenu();
    const menu = el.qualityMenu;
    positionQualityMenu();
    menu.classList.add("is-open");
    el.qualityBtn.setAttribute("aria-expanded", "true");
    menuOpen = true;
    const selected = items.find((i) => i.getAttribute("aria-selected") === "true") || items[0];
    selected?.focus({ preventScroll: true });
  }

  function closeQualityMenu(returnFocus) {
    if (!menuOpen) return;
    menuOpen = false;
    el.qualityMenu.classList.remove("is-open");
    el.qualityBtn.setAttribute("aria-expanded", "false");
    if (returnFocus) el.qualityBtn.focus({ preventScroll: true });
  }

  function onMenuKeydown(e) {
    const items = [...el.qualityMenu.querySelectorAll(".menu-item")];
    const index = items.indexOf(document.activeElement);
    const focusAt = (i) => items[(i + items.length) % items.length]?.focus();
    switch (e.key) {
      case "ArrowDown": focusAt(index + 1); break;
      case "ArrowUp": focusAt(index - 1); break;
      case "Home": focusAt(0); break;
      case "End": focusAt(items.length - 1); break;
      case "Escape": closeQualityMenu(true); break;
      case "Enter":
      case " ": if (index >= 0) selectQuality(items[index].dataset.value); break;
      case "Tab": closeQualityMenu(false); return;
      default: return;
    }
    e.preventDefault();
    e.stopPropagation();
  }

  /* ----------------------------------------------------------------- notice */
  let noticeAction = null;

  function showNotice(title, detail, action) {
    el.noticeTitle.textContent = title || "Something went wrong.";
    el.noticeDetail.textContent = detail || "";
    el.noticeDetail.hidden = !detail;
    noticeAction = action || null;
    el.noticeRetry.hidden = !action;
    el.notice.hidden = false;
    setHomeExpanded(true);
  }

  function hideNotice() {
    el.notice.hidden = true;
    noticeAction = null;
  }

  /* ---------------------------------------------------------------- recents */
  const FOLDER_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2h7.4a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>';

  function renderRecents(items) {
    const rows = (items || []).map((item) => {
      const li = document.createElement("li");
      li.className = "recent";

      const main = document.createElement("button");
      main.type = "button";
      main.className = "recent-main";
      main.title = item.name;

      const img = document.createElement("img");
      img.className = "recent-thumb";
      img.alt = "";
      img.draggable = false;
      setThumb(img, item.thumb_small, item.thumb_fallback);

      const text = document.createElement("span");
      text.className = "recent-text";
      const title = document.createElement("span");
      title.className = "recent-title";
      title.textContent = item.title || item.name;
      const sub = document.createElement("span");
      sub.className = "recent-sub";
      sub.textContent = [fmtWhen(item.saved_at), fmtBytes(item.size), item.kind === "audio" ? "MP3" : ""]
        .filter(Boolean).join(" · ");
      text.append(title, sub);
      main.append(img, text);
      main.addEventListener("click", () => openRecent(item.id));

      const reveal = document.createElement("button");
      reveal.type = "button";
      reveal.className = "recent-reveal";
      reveal.title = "Show in Folder";
      reveal.setAttribute("aria-label", "Show in Folder");
      reveal.innerHTML = FOLDER_ICON;
      reveal.addEventListener("click", () => api.reveal(item.id));

      li.append(main, reveal);
      return li;
    });
    el.recentList.replaceChildren(...rows);
    el.recents.hidden = rows.length === 0;
  }

  async function refreshRecents() {
    try { renderRecents(await api.recent()); } catch { /* keep what's shown */ }
  }

  async function openRecent(id) {
    const file = await api.history_item(id);
    if (file) {
      openFile(file, false);
    } else {
      toast("That video was moved or deleted");
      refreshRecents();
    }
  }

  /* ------------------------------------------------------------------- ring */
  const CIRCUMFERENCE = 2 * Math.PI * 96;
  const ring = { target: 0, shown: 0, frame: 0 };
  el.ringBar.style.strokeDasharray = String(CIRCUMFERENCE);

  function drawRing() {
    el.ringBar.style.strokeDashoffset = String(CIRCUMFERENCE * (1 - ring.shown / 100));
    el.ringPct.textContent = String(Math.floor(ring.shown));
  }

  function startRing() {
    cancelAnimationFrame(ring.frame);
    const step = () => {
      const gap = ring.target - ring.shown;
      ring.shown = Math.abs(gap) < 0.05 ? ring.target : ring.shown + gap * 0.11;
      drawRing();
      ring.frame = requestAnimationFrame(step);
    };
    ring.frame = requestAnimationFrame(step);
  }

  function stopRing() {
    cancelAnimationFrame(ring.frame);
    ring.frame = 0;
  }

  function waitForRing(value, timeout) {
    const started = performance.now();
    return new Promise((resolve) => {
      const check = () => {
        if (ring.shown >= value || performance.now() - started > timeout) resolve();
        else requestAnimationFrame(check);
      };
      check();
    });
  }

  /* --------------------------------------------------------------- download */
  const STAGE_TEXT = {
    preparing: "Getting ready…",
    video: "Downloading video…",
    audio: "Downloading audio…",
    file: "Downloading…",
    finishing: "Finishing up…",
    converting: "Converting to MP3…",
    retrying: "Reconnecting…",
  };

  function prepareProgress(info) {
    ring.target = 0;
    ring.shown = 0;
    drawRing();
    el.ring.classList.remove("is-complete", "is-finishing");
    el.ring.classList.add("is-busy");
    setText(el.progressStage, STAGE_TEXT.preparing, false);
    el.progressDetail.textContent = "\u00a0";
    el.progressTitle.textContent = info.title;
    el.cancelBtn.disabled = false;

    const thumb = state.thumbUrl;
    for (const node of [el.ambient, el.ringThumb]) {
      node.style.backgroundImage = thumb ? `url("${thumb}")` : "";
      node.classList.toggle("has-image", Boolean(thumb));
    }
    startRing();
  }

  async function startDownload() {
    if (!state.info || state.downloading) return;
    closeQualityMenu(false);
    hideNotice();
    state.downloading = true;
    prepareProgress(state.info);
    showView("progress");

    let result;
    try { result = await api.start_download(state.infoUrl, state.quality); }
    catch (err) { result = { ok: false, error: String(err) }; }
    if (!result?.ok) {
      state.downloading = false;
      stopRing();
      showView("home");
      showNotice(result?.error || "Couldn't start the download.");
    }
  }

  function onStage(stage) {
    if (state.view !== "progress") return;
    if (stage === "finishing" || stage === "converting") {
      ring.target = 100;
      el.ring.classList.remove("is-busy");
      el.ring.classList.add("is-finishing");
      setText(el.progressStage, STAGE_TEXT[stage]);
      el.progressDetail.textContent = stage === "converting" ? "Adding the title and cover art" : "Saving your MP4";
      el.cancelBtn.disabled = true;
    } else if (stage === "preparing") {
      setText(el.progressStage, STAGE_TEXT.preparing);
    } else if (stage === "retrying") {
      // The watchdog restarted a stalled download with fresh links.
      ring.target = 0;
      ring.shown = 0;
      el.ring.classList.add("is-busy");
      setText(el.progressStage, STAGE_TEXT.retrying);
      el.progressDetail.textContent = "YouTube stalled, trying again";
    }
  }

  function onProgress(event) {
    if (state.view !== "progress" || el.ring.classList.contains("is-finishing")) return;
    el.ring.classList.remove("is-busy");
    // Never let the ring slide backwards when a second stream grows the total.
    ring.target = Math.max(ring.target, Math.min(99, event.percent || 0));
    setText(el.progressStage, STAGE_TEXT[event.stage] || STAGE_TEXT.file);

    const parts = [];
    if (event.total) parts.push(`${fmtBytes(event.downloaded)} of ${fmtBytes(event.total)}`);
    if (event.speed) parts.push(`${fmtBytes(event.speed)}/s`);
    const eta = fmtEta(event.eta);
    if (eta) parts.push(eta);
    el.progressDetail.textContent = parts.join(" · ") || "\u00a0";
  }

  async function onDone(file) {
    state.downloading = false;
    state.dismissed.add(state.infoUrl);                    // don't re-offer it from the clipboard
    el.cancelBtn.disabled = true;
    ring.target = 100;
    el.ring.classList.remove("is-busy", "is-finishing");
    setText(el.progressStage, "Saved");
    el.progressDetail.textContent = file.name;

    await waitForRing(99.5, 1200);
    el.ring.classList.add("is-complete");
    await sleep(reducedMotion() ? 50 : 1150);
    stopRing();
    openFile(file, true);
    refreshRecents();
  }

  function onDownloadError(event) {
    state.downloading = false;
    stopRing();
    showView("home");
    el.previewCard.hidden = !state.info;
    showNotice(event.error, event.detail, state.info ? startDownload : null);
  }

  function onCancelled() {
    state.downloading = false;
    stopRing();
    showView("home");
    toast("Download cancelled");
  }

  function onEvent(event) {
    switch (event?.type) {
      case "stage": onStage(event.stage); break;
      case "progress": onProgress(event); break;
      case "done": onDone(event.file); break;
      case "error": onDownloadError(event); break;
      case "cancelled": onCancelled(); break;
    }
  }

  /* ------------------------------------------------------------------- done */
  function openFile(file, fresh) {
    state.file = file;
    el.doneBadge.hidden = !fresh;
    el.doneTitle.textContent = fresh ? "Saved" : `Saved ${fmtWhen(file.saved_at)}`;
    el.fileName.textContent = file.name;
    el.fileName.title = file.name;
    el.fileSub.textContent = [fmtBytes(file.size), file.folder_label].filter(Boolean).join(" · ");

    const audio = file.kind === "audio";
    el.viewDone.classList.toggle("is-audio", audio);
    el.fileExt.textContent = file.ext || (audio ? "MP3" : "MP4");
    el.playerArt.hidden = !audio;
    if (audio) setThumb(el.playerArt, file.thumb, file.thumb_fallback);

    el.player.style.aspectRatio = audio ? "" : "16 / 9";
    // For video, "#t=0.1" makes the player show a real first frame instead of a black box.
    if (file.media_url) el.player.src = audio ? file.media_url : `${file.media_url}#t=0.1`;
    else el.player.removeAttribute("src");
    showView("done");
  }

  function releasePlayer() {
    el.player.pause();
    el.player.removeAttribute("src");
    el.player.load();                                      // lets go of the file on disk
  }

  function backHome() {
    releasePlayer();
    el.linkInput.value = "";
    state.autoFilled = "";
    syncFieldButtons();
    setHint("");
    resetPreview();
    showView("home");
    setTimeout(() => el.linkInput.focus({ preventScroll: true }), 300);
  }

  el.player.addEventListener("loadedmetadata", () => {
    const { videoWidth: w, videoHeight: h } = el.player;
    if (w && h) el.player.style.aspectRatio = `${w} / ${h}`;
  });

  /* ----------------------------------------------------------------- events */
  function wireEvents() {
    el.linkInput.addEventListener("input", onInput);
    el.pasteBtn.addEventListener("click", pasteFromClipboard);
    el.clearBtn.addEventListener("click", clearLink);
    el.downloadBtn.addEventListener("click", startDownload);

    el.qualityBtn.addEventListener("click", () => (menuOpen ? closeQualityMenu(true) : openQualityMenu()));
    el.qualityBtn.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); openQualityMenu(); }
    });
    el.qualityMenu.addEventListener("keydown", onMenuKeydown);
    document.addEventListener("pointerdown", (e) => {
      if (menuOpen && !el.qualityMenu.contains(e.target) && !el.qualityBtn.contains(e.target)) {
        closeQualityMenu(false);
      }
    }, true);
    // Follow the button if the page scrolls, rather than snapping the menu shut.
    el.viewHome.querySelector(".home-scroll").addEventListener("scroll", () => {
      if (menuOpen) positionQualityMenu();
    });
    el.noticeRetry.addEventListener("click", () => { const action = noticeAction; hideNotice(); action?.(); });
    el.cancelBtn.addEventListener("click", () => {
      el.cancelBtn.disabled = true;
      setText(el.progressStage, "Cancelling…");
      api.cancel_download();
    });

    el.playBtn.addEventListener("click", () => {
      if (!state.file) return;
      el.player.pause();
      api.play(state.file.id);
    });
    el.revealBtn.addEventListener("click", () => state.file && api.reveal(state.file.id));
    el.againBtn.addEventListener("click", backHome);

    el.folderBtn.addEventListener("click", async () => {
      const result = await api.choose_folder();
      if (result?.ok) {
        el.folderLabel.textContent = result.output_label;
        toast(`Videos will be saved to ${result.output_label}`);
      }
    });

    document.addEventListener("keydown", (e) => {
      if (menuOpen) return;                                // the menu handles its own keys
      if (e.key === "Enter" && state.view === "home") {
        const value = el.linkInput.value.trim();
        if (state.info && value === state.infoUrl) { e.preventDefault(); startDownload(); }
        else if (isYouTube(value)) { e.preventDefault(); clearTimeout(typingTimer); loadPreview(value); }
      } else if (e.key === "Escape") {
        if (state.view === "done") backHome();
        else if (state.view === "home" && el.linkInput.value) clearLink();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v"
                 && state.view === "home" && document.activeElement !== el.linkInput) {
        e.preventDefault();
        pasteFromClipboard();
      }
    });

    // Drop a link from the browser's address bar onto the window.
    let dragDepth = 0;
    window.addEventListener("dragenter", (e) => {
      e.preventDefault();
      if (state.view === "home" && ++dragDepth === 1) el.field.classList.add("is-drop");
    });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("dragleave", () => {
      if (--dragDepth <= 0) { dragDepth = 0; el.field.classList.remove("is-drop"); }
    });
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      dragDepth = 0;
      el.field.classList.remove("is-drop");
      if (state.view !== "home") return;
      const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "";
      const url = text.split(/\r?\n/).map((line) => line.trim()).find(isYouTube);
      if (url) fillLink(url, "drop");
      else setHint("Drop a YouTube link here.", "error");
    });

    window.addEventListener("focus", checkClipboard);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkClipboard(); });
    window.addEventListener("resize", () => { if (menuOpen) positionQualityMenu(); });
    window.addEventListener("blur", () => closeQualityMenu(false));
  }

  /* ------------------------------------------------------------------- boot */
  async function boot(bridge) {
    api = bridge;
    let initial = null;
    try { initial = await api.bootstrap(); } catch { /* fall through with defaults */ }
    if (initial) {
      el.folderLabel.textContent = initial.output_label;
      renderRecents(initial.history);
      if (!initial.ffmpeg) setTimeout(() => toast("ffmpeg wasn't found, so only low quality is available"), 1200);
    }
    el.linkInput.focus({ preventScroll: true });
    await checkClipboard();
  }

  window.GTV = { onEvent };
  document.documentElement.classList.add("is-launching");
  setTimeout(() => document.documentElement.classList.remove("is-launching"), 1400);
  wireEvents();
  syncFieldButtons();

  const demo = new URLSearchParams(location.search).get("demo");
  if (demo) {
    runDemo(demo);
  } else if (window.pywebview?.api?.bootstrap) {
    boot(window.pywebview.api);
  } else {
    window.addEventListener("pywebviewready", () => boot(window.pywebview.api), { once: true });
  }

  /* ------------------------------------------------------------------- demo */
  // Open index.html?demo=home|preview|loading|error|progress|finishing|done[&static]
  // in a browser to look at a screen without Python. Not used by the app itself.
  function runDemo(screen) {
    const params = new URLSearchParams(location.search);
    if (params.has("static")) document.documentElement.classList.add("no-motion");
    const vid = "ytoGJiYH8ms";
    const thumbs = {
      thumb: `https://i.ytimg.com/vi/${vid}/maxresdefault.jpg`,
      thumb_fallback: `https://i.ytimg.com/vi/${vid}/hqdefault.jpg`,
      thumb_small: `https://i.ytimg.com/vi/${vid}/mqdefault.jpg`,
    };
    const info = {
      id: vid, url: `https://www.youtube.com/watch?v=${vid}`, ...thumbs,
      title: "Minecraft Iron Farming Guide - How Golem Spawning Works",
      channel: "GentleGiant", duration: 176, default_quality: "2160",
      qualities: [
        { value: "2160", label: "4K", short: "4K", kind: "video", size: 212e6 },
        { value: "1440", label: "1440p", short: "1440p", kind: "video", size: 118e6 },
        { value: "1080", label: "1080p", short: "1080p", kind: "video", size: 63.5e6 },
        { value: "720", label: "720p", short: "720p", kind: "video", size: 36.8e6 },
        { value: "audio", label: "Audio only", short: "MP3", kind: "audio", size: 4.4e6 },
      ],
    };
    const now = Date.now() / 1000;
    const file = {
      id: "demo1", ...thumbs, media_url: "", size: 63.5e6, saved_at: now, duration: 176,
      name: "20260914-Minecraft Iron Farming Guide - How Golem Spawning Works.mp4",
      title: info.title, channel: "GentleGiant", folder_label: "Downloads › GimmeThatVid",
    };
    const history = [
      file,
      { ...file, id: "demo2", title: "Redstone Sorter Tutorial - Overly Explained", saved_at: now - 86400, size: 118e6 },
      { ...file, id: "demo3", title: "The Redstone Rulebook - Inputs and Important Blocks", saved_at: now - 5 * 86400, size: 42e6 },
    ];
    const fake = {
      bootstrap: async () => ({ output_label: "Downloads › GimmeThatVid", history: screen === "home" ? history : [], ffmpeg: true }),
      recent: async () => history,
      clipboard_url: async () => (screen === "home" ? "" : info.url),
      fetch_info: async () => {
        if (screen === "loading") await sleep(1e9);
        if (screen === "error") return { ok: false, error: "This video is private." };
        return { ok: true, info };
      },
      start_download: async () => ({ ok: true }),
      cancel_download: async () => true,
      history_item: async () => file,
      play: async () => true,
      reveal: async () => true,
      choose_folder: async () => ({ ok: false }),
    };

    boot(fake).then(async () => {
      await sleep(60);
      if (screen === "progress" || screen === "finishing") {
        state.thumbUrl = thumbs.thumb_fallback;
        await startDownload();
        if (screen === "progress") {
          onEvent({ type: "progress", stage: "video", percent: 64, downloaded: 40.6e6, total: 63.5e6, speed: 7.1e6, eta: 4 });
        } else {
          onEvent({ type: "stage", stage: "finishing" });
        }
        ring.shown = ring.target;
        drawRing();
      } else if (screen === "done") {
        openFile(file, true);
      } else if (screen === "menu") {
        await sleep(200);
        openQualityMenu();
      }
    });
  }
})();
