/* MyApps — utilitaires partagés (portail, administration, assistant) */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));

function readToken() { try { return localStorage.getItem("token"); } catch { return null; } }
function saveToken(t) { try { t ? localStorage.setItem("token", t) : localStorage.removeItem("token"); } catch {} }

const state = { token: readToken(), me: null, dash: null, apps: [], sites: [], editMode: false, branding: {} };

// ---------- API ----------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401 && state.token) { sessionLost(); throw new Error("Session expirée"); }
  if (!res.ok) {
    let msg = "Erreur " + res.status;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : (j.detail[0]?.msg || JSON.stringify(j.detail));
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2800);
}

async function uploadImage(file) {
  const fd = new FormData(); fd.append("file", file);
  const r = await api("/api/upload", { method: "POST", body: fd });
  return r.url;
}

function slugify(s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }
function initials(s) { return (s || "?").trim().split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase(); }

// ---------- Fond (fondu enchaîné entre 2 couches, image préchargée) ----------
let _bgFront = 0, _bgUrl = null;
function setBg(url) {
  url = url || "";
  if (url === _bgUrl) return;
  _bgUrl = url;
  const layers = [$("#bgLayer"), $("#bgLayer2")];
  const cur = layers[_bgFront], nxt = layers[1 - _bgFront];
  const swap = () => {
    nxt.style.backgroundImage = url ? `url("${url}")` : "";
    nxt.style.opacity = "1";
    cur.style.opacity = "0";
    _bgFront = 1 - _bgFront;
  };
  if (url) { const im = new Image(); im.onload = swap; im.onerror = swap; im.src = url; }
  else swap();
}

// ---------- Apparence ----------
function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return [59, 130, 246];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function onAccent(hex) {
  // Texte noir ou blanc selon la luminance relative (WCAG) de la couleur d'accent.
  const [r, g, b] = hexToRgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.36 ? "#0b1020" : "#ffffff";
}

function lockupHtml(b) {
  b = b || state.branding;
  const name = b.portal_name || "MyApps";
  const mark = b.logo_url
    ? `<img class="mark${b.logo_plate ? " plate" : ""}" src="${esc(b.logo_url)}" alt="">`
    : `<span class="mono">${esc(initials(name))}</span>`;
  const word = (b.show_name !== false || !b.logo_url) ? `<span class="word">${esc(name)}</span>` : "";
  return `<span class="lockup">${mark}${word}</span>`;
}

function faviconUrl(b) {
  if (b.logo_url) return b.logo_url;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="${b.accent_color || "#3b82f6"}"/>` +
    `<text x="32" y="42" font-family="Arial,sans-serif" font-size="28" font-weight="700" text-anchor="middle" fill="${onAccent(b.accent_color)}">${esc(initials(b.portal_name || "MyApps"))}</text></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(svg);
}

function applyBranding(b) {
  state.branding = b = { ...state.branding, ...(b || {}) };
  const accent = b.accent_color || "#3b82f6", base = b.base_color || "#0b1020";
  const root = document.documentElement.style;
  root.setProperty("--accent", accent);
  root.setProperty("--accent-rgb", hexToRgb(accent).join(", "));
  root.setProperty("--on-accent", onAccent(accent));
  root.setProperty("--base", base);
  root.setProperty("--base-rgb", hexToRgb(base).join(", "));
  root.setProperty("--bg", "rgb(" + hexToRgb(base).map((v) => Math.round(v * 0.7)).join(", ") + ")");
  applyFonts(b.font_title, b.font_body);
  document.title = b.portal_name || "MyApps";
  $("#favicon").href = faviconUrl(b);
  $$(".brand-slot").forEach((el) => (el.innerHTML = lockupHtml(b)));
  const si = $("#searchInput");
  if (si) si.placeholder = b.search_engine_name ? `Rechercher une application ou sur ${b.search_engine_name}…` : "Rechercher une application…";
}

// Polices : « system » n'appelle rien ; sinon feuille Google Fonts chargée par le navigateur.
const SYSTEM_STACK = '"Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif';
const FONTS = ["system", "Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins", "Nunito",
  "Work Sans", "Source Sans 3", "DM Sans", "Manrope", "IBM Plex Sans", "Space Grotesk", "Albert Sans"];
function applyFonts(title, body) {
  const root = document.documentElement.style;
  const fam = (f) => (f && f !== "system" ? `"${f}", ` : '"Inter", ') + SYSTEM_STACK;
  root.setProperty("--font-title", fam(title));
  root.setProperty("--font-body", fam(body));
  const wanted = [...new Set([title, body].filter((f) => f && f !== "system" && FONTS.includes(f)))];
  let link = $("#brandFonts");
  if (!wanted.length) { link?.remove(); return; }
  const href = "https://fonts.googleapis.com/css2?" + wanted.map((f) => "family=" + f.replace(/ /g, "+") + ":wght@300;400;500;600;700").join("&") + "&display=swap";
  if (!link) { link = document.createElement("link"); link.id = "brandFonts"; link.rel = "stylesheet"; document.head.appendChild(link); }
  if (link.href !== href) link.href = href;
}
function fontSelectHtml(id, value) {
  return `<select id="${id}">${FONTS.map((f) => `<option value="${f}" ${f === (value || "system") ? "selected" : ""}>${f === "system" ? "Police du système (aucun appel externe)" : esc(f)}</option>`).join("")}</select>`;
}

// ---------- Fonds proposés ----------
const BACKGROUNDS = [
  { url: "/backgrounds/aurore.svg", name: "Aurore" },
  { url: "/backgrounds/ocean.svg", name: "Océan" },
  { url: "/backgrounds/lagune.svg", name: "Lagune" },
  { url: "/backgrounds/foret.svg", name: "Forêt" },
  { url: "/backgrounds/crepuscule.svg", name: "Crépuscule" },
  { url: "/backgrounds/graphite.svg", name: "Graphite" },
];
const BASES = ["#0b1020", "#000038", "#0f172a", "#111111", "#1a1033", "#062019", "#1f1414"];
const ACCENTS = ["#3b82f6", "#6366f1", "#8b5cf6", "#ec4899", "#ef4444", "#f97316", "#eab308", "#22c55e", "#10b981", "#06b6d4"];

// ---------- Widget : choix d'image (URL, envoi de fichier, aperçu) ----------
function imagePickerHtml(id, value, opts = {}) {
  return `<div class="img-picker">
    <span class="prev${opts.wide ? " wide" : ""}${opts.plate ? " plate" : ""}" id="${id}Prev"></span>
    <input id="${id}" placeholder="${esc(opts.placeholder || "https://… ou envoyer un fichier")}" value="${esc(value || "")}">
    <label class="btn-ghost sm upload-lbl">📁 Envoyer<input type="file" id="${id}File" accept="image/*" hidden></label>
    ${opts.clear !== false ? `<button type="button" class="icon-btn" id="${id}Clear" title="Retirer">✕</button>` : ""}
  </div>`;
}
function bindImagePicker(id, onChange) {
  const inp = $("#" + id), prev = $("#" + id + "Prev");
  const refresh = () => {
    const v = inp.value.trim();
    prev.style.backgroundImage = v ? `url("${v.replace(/"/g, "")}")` : "";
    if (onChange) onChange(v);
  };
  inp.oninput = refresh;
  $("#" + id + "File").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { inp.value = await uploadImage(f); refresh(); toast("Image envoyée"); }
    catch (err) { toast(err.message); }
    e.target.value = "";
  };
  const clr = $("#" + id + "Clear");
  if (clr) clr.onclick = () => { inp.value = ""; refresh(); };
  refresh();
}

// ---------- Widget : grille de fonds proposés ----------
function bgGridHtml(id, value) {
  return `<div class="bg-grid" id="${id}">
    <button type="button" class="bg-opt none${!value ? " on" : ""}" data-url="">Dégradé simple</button>
    ${BACKGROUNDS.map((b) => `<button type="button" class="bg-opt${value === b.url ? " on" : ""}" data-url="${b.url}" style="background-image:url('${b.url}')"><span>${esc(b.name)}</span></button>`).join("")}
  </div>`;
}
function bindBgGrid(id, onPick) {
  $$("#" + id + " .bg-opt").forEach((btn) => (btn.onclick = () => {
    $$("#" + id + " .bg-opt").forEach((b) => b.classList.toggle("on", b === btn));
    onPick(btn.dataset.url);
  }));
}
function markBgGrid(id, value) {
  $$("#" + id + " .bg-opt").forEach((b) => b.classList.toggle("on", b.dataset.url === (value || "")));
}

// ---------- Widget : couleur d'accent ----------
function swatchesHtml(id, value, palette = ACCENTS) {
  const v = (value || "").toLowerCase();
  return `<div class="swatches" id="${id}">
    ${palette.map((c) => `<button type="button" class="swatch${v === c ? " on" : ""}" data-c="${c}" style="background:${c}" title="${c}"></button>`).join("")}
    <input type="color" id="${id}Custom" value="${esc(value || palette[0])}" title="Couleur personnalisée">
  </div>`;
}
function bindSwatches(id, onPick) {
  const mark = (c) => $$("#" + id + " .swatch").forEach((s) => s.classList.toggle("on", s.dataset.c === c.toLowerCase()));
  $$("#" + id + " .swatch").forEach((s) => (s.onclick = () => { $("#" + id + "Custom").value = s.dataset.c; mark(s.dataset.c); onPick(s.dataset.c); }));
  $("#" + id + "Custom").oninput = (e) => { mark(e.target.value); onPick(e.target.value); };
}

// ---------- Widget : recherche de ville (Open-Meteo) ----------
function cityPickerHtml(id) {
  return `<div class="row-inline"><input id="${id}Q" placeholder="Ville (ex. Lyon, Bordeaux…)"><button type="button" class="btn-ghost shrink" id="${id}Go">Rechercher</button></div>
    <div class="city-results" id="${id}Res"></div>`;
}
function bindCityPicker(id, onPick) {
  const run = async () => {
    const q = $("#" + id + "Q").value.trim(); const res = $("#" + id + "Res");
    if (q.length < 2) return;
    res.innerHTML = `<span class="muted">Recherche…</span>`;
    try {
      const rows = await api("/api/geocode?q=" + encodeURIComponent(q));
      if (!rows.length) { res.innerHTML = `<span class="muted">Aucune ville trouvée.</span>`; return; }
      res.innerHTML = rows.map((r, i) => `<button type="button" data-i="${i}">${esc(r.name)} <span class="muted">${esc([r.admin, r.country].filter(Boolean).join(", "))}</span></button>`).join("");
      $$("button", res).forEach((b) => (b.onclick = () => {
        $$("button", res).forEach((x) => x.classList.toggle("on", x === b));
        onPick(rows[Number(b.dataset.i)]);
      }));
    } catch (e) { res.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  };
  $("#" + id + "Go").onclick = run;
  $("#" + id + "Q").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } };
}

// ---------- Applications proposées ----------
// url vide = application hébergée chez vous : seule l'adresse est à compléter.
const fav = (d) => `https://www.google.com/s2/favicons?domain=${d}&sz=128`;
const APP_CATALOG = [
  { name: "Microsoft 365", url: "https://www.office.com", tip: "Word, Excel, PowerPoint en ligne", logo: fav("office.com") },
  { name: "Outlook", url: "https://outlook.office.com", tip: "Messagerie et agenda", logo: fav("outlook.office.com") },
  { name: "Teams", url: "https://teams.microsoft.com", tip: "Messagerie d'équipe et visio", logo: fav("teams.microsoft.com") },
  { name: "SharePoint", url: "https://www.office.com/launch/sharepoint", tip: "Documents partagés", logo: fav("sharepoint.com") },
  { name: "Gmail", url: "https://mail.google.com", tip: "Messagerie Google", logo: fav("mail.google.com") },
  { name: "Google Drive", url: "https://drive.google.com", tip: "Fichiers partagés", logo: fav("drive.google.com") },
  { name: "Google Agenda", url: "https://calendar.google.com", tip: "Agenda partagé", logo: fav("calendar.google.com") },
  { name: "Slack", url: "https://app.slack.com", tip: "Messagerie d'équipe", logo: fav("slack.com") },
  { name: "Zoom", url: "https://zoom.us/signin", tip: "Visioconférence", logo: fav("zoom.us") },
  { name: "Notion", url: "https://www.notion.so", tip: "Wiki et notes", logo: fav("notion.so") },
  { name: "Jira", url: "https://id.atlassian.com", tip: "Suivi de projets", logo: fav("atlassian.com") },
  { name: "GitHub", url: "https://github.com", tip: "Dépôts de code", logo: fav("github.com") },
  { name: "GitLab", url: "", tip: "Dépôts de code (hébergé)", logo: fav("gitlab.com") },
  { name: "Nextcloud", url: "", tip: "Fichiers et agenda (hébergé)", logo: fav("nextcloud.com") },
  { name: "GLPI", url: "", tip: "Support et parc informatique", logo: fav("glpi-project.org") },
  { name: "Grafana", url: "", tip: "Tableaux de bord de supervision", logo: fav("grafana.com") },
  { name: "Zabbix", url: "", tip: "Supervision", logo: fav("zabbix.com") },
  { name: "Proxmox", url: "", tip: "Virtualisation", logo: fav("proxmox.com") },
];
