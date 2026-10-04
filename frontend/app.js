/* MyApps — portail et administration (SPA vanilla JS, sans étape de build) */
"use strict";

// ---------- Vues ----------
function show(view) {
  $("#splash").classList.add("hidden");
  $("#view-setup").classList.add("hidden");
  ["login", "app", "admin"].forEach((v) => $("#view-" + v).classList.toggle("hidden", v !== view));
  if (view === "login") setBg(state.branding.login_background || state.branding.dashboard_background || "");
}

// ---------- Démarrage ----------
async function boot() {
  // Retour SSO : jeton dans le fragment de l'URL (jamais envoyé au serveur)
  const hash = new URLSearchParams(location.hash.slice(1));
  if (hash.get("sso_token")) { state.token = hash.get("sso_token"); saveToken(state.token); }
  const params = new URLSearchParams(location.search);
  if (params.get("sso_error")) toast("Échec de la connexion SSO (" + params.get("sso_error") + ")");
  if (location.hash || location.search) history.replaceState({}, "", location.pathname);

  try { applyBranding(await fetch("/api/auth/branding").then((r) => r.json())); } catch { applyBranding({}); }

  let setup = { required: false, admin_exists: true };
  try { setup = await fetch("/api/setup/status").then((r) => r.json()); } catch {}
  state.setupRequired = setup.required;
  if (setup.required && !setup.admin_exists) { saveToken(null); state.token = null; return startWizard({ adminExists: false }); }

  try {
    const sso = await fetch("/api/auth/sso/config").then((r) => r.json());
    if (sso.enabled) { $("#ssoBtn").classList.remove("hidden"); $("#loginSep").classList.remove("hidden"); $("#ssoName").textContent = sso.provider; }
  } catch {}

  if (state.token) {
    const r = await fetch("/api/auth/me", { headers: { Authorization: "Bearer " + state.token } }).catch(() => null);
    if (r && r.ok) { state.me = await r.json(); return afterLogin(false); }
    saveToken(null); state.token = null;
  }
  show("login");
}

// Après connexion : l'assistant reprend pour un admin s'il n'a pas été terminé.
function afterLogin(withWelcome) {
  if (state.setupRequired && state.me.is_admin) return startWizard({ adminExists: true });
  if (withWelcome) { showWelcome(); enterApp(); setTimeout(hideWelcome, 1500); }
  else enterApp();
}

// ---------- Écran « Bienvenue » ----------
function showWelcome() {
  const me = state.me || {};
  const first = (me.full_name || me.username || "").trim().split(/\s+/)[0];
  $("#welcomeName").textContent = "Bienvenue " + first;
  $("#welcome").classList.remove("hidden", "leaving");
}
function hideWelcome() {
  const w = $("#welcome");
  w.classList.add("leaving");
  setTimeout(() => { w.classList.add("hidden"); w.classList.remove("leaving"); }, 300);
}

// ---------- Connexion ----------
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#loginForm button[type='submit']");
  btn.classList.add("loading");
  $("#loginErr").classList.add("hidden");
  const body = new URLSearchParams({ username: $("#loginUser").value, password: $("#loginPass").value, remember: $("#loginRemember").checked ? "true" : "false" });
  try {
    const r = await fetch("/api/auth/login", { method: "POST", body });
    if (!r.ok) throw new Error("bad");
    const j = await r.json();
    state.token = j.access_token; saveToken(state.token);
    state.me = j.user;
    $("#loginPass").value = "";
    afterLogin(true);
  } catch {
    const el = $("#loginErr"); el.textContent = "Identifiants invalides."; el.classList.remove("hidden");
  } finally {
    btn.classList.remove("loading");
  }
});
$("#ssoBtn").addEventListener("click", () => { location.href = "/api/auth/sso/login"; });

function logout() { endSession(); }

// Jeton refusé (expiré, compte désactivé…) : retour à la page de connexion.
let _lost = false;
function sessionLost() {
  if (_lost) return;
  _lost = true;
  endSession();
  toast("Session expirée, reconnectez-vous.");
  setTimeout(() => (_lost = false), 1000);
}

function endSession() {
  saveToken(null); state.token = null; state.me = null;
  $("#loginUser").value = ""; $("#loginPass").value = "";
  closeModal();
  show("login");
}

// ---------- Portail ----------
async function enterApp() {
  show("app");
  const me = state.me;
  $("#uname").textContent = me.full_name || me.username;
  $("#avatar").textContent = initials(me.full_name || me.username);
  $("#adminLink").classList.toggle("hidden", !me.is_admin);
  $("#editToggle").classList.toggle("hidden", !me.is_admin);
  state.editMode = false;
  applyEditMode();

  // Météo : seulement si au moins un site est défini
  try { state.sites = await api("/api/sites"); } catch { state.sites = []; }
  const wSite = $("#wSite");
  $("#weather").classList.toggle("hidden", !state.sites.length);
  if (state.sites.length) {
    wSite.innerHTML = state.sites.map((s) => `<option value="${esc(s.slug)}">${esc(s.name)}</option>`).join("");
    wSite.style.display = state.sites.length > 1 ? "" : "none";
    $(".weather .w-sep").style.display = state.sites.length > 1 ? "" : "none";
    loadWeather(me.site ? me.site.slug : null);
    wSite.onchange = () => loadWeather(wSite.value);
  }

  refreshUpdateHint();
  try { state.apps = await api("/api/me/apps"); } catch { state.apps = []; }

  let tabs = [];
  try { tabs = await api("/api/me/dashboards"); } catch {}
  renderTabs(tabs);
  const def = tabs.length ? await api("/api/me/default-dashboard") : { slug: null };
  if (def.slug) return openDashboard(def.slug);

  setBg(state.branding.dashboard_background);
  $("#dashTitle").textContent = "";
  $("#dashHello").textContent = "";
  $("#sections").innerHTML = me.is_admin
    ? `<div class="empty glass">Aucun dashboard pour l'instant.<br><button class="btn-accent" id="emptyAdd">Créer un dashboard</button></div>`
    : `<div class="empty glass">Aucun dashboard ne vous est attribué pour l'instant.<br><span class="muted">Contactez l'administrateur du portail.</span></div>`;
  if (me.is_admin) $("#emptyAdd").onclick = () => enterAdmin("dashboards");
}

function renderTabs(tabs) {
  const el = $("#tabs");
  el.innerHTML = `<span class="tab-ind no-anim"></span>` + tabs.map((t) => `<a data-slug="${esc(t.slug)}">${esc(t.name)}</a>`).join("");
  el.style.display = tabs.length > 1 ? "flex" : "none";
  $$("a", el).forEach((a) => (a.onclick = () => openDashboard(a.dataset.slug, true)));
}

// Pastille d'onglet actif qui glisse vers l'onglet choisi.
function moveTabIndicator() {
  const ind = $("#tabs .tab-ind"), a = $("#tabs a.active");
  if (!ind) return;
  if (!a) { ind.style.width = "0"; return; }
  ind.style.transform = `translate(${a.offsetLeft}px, ${a.offsetTop}px)`;
  ind.style.width = a.offsetWidth + "px";
  ind.style.height = a.offsetHeight + "px";
  if (ind.classList.contains("no-anim")) requestAnimationFrame(() => requestAnimationFrame(() => ind.classList.remove("no-anim")));
}
window.addEventListener("resize", () => {
  const ind = $("#tabs .tab-ind"); if (!ind) return;
  ind.classList.add("no-anim"); moveTabIndicator();
});

async function openDashboard(slug, animate = false) {
  let d;
  try { d = await api("/api/me/dashboard/" + encodeURIComponent(slug)); } catch (e) { toast(e.message); return; }
  $$("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.slug === slug));
  moveTabIndicator();
  // Changement d'onglet : l'ancien contenu s'efface (≈ 110 ms), le nouveau arrive en cascade.
  if (animate && !reducedMotion()) {
    $("#sections").classList.add("leaving");
    clearTimeout(openDashboard._t);
    openDashboard._t = setTimeout(() => renderDashboard(d, true), 110);
  } else renderDashboard(d, false);
}

function renderDashboard(d, swap) {
  state.dash = d;
  $("#sections").classList.remove("leaving");
  const head = $(".portal-head");
  head.classList.remove("swap");
  if (swap) { void head.offsetWidth; head.classList.add("swap"); }
  $("#dashTitle").textContent = d.name;
  const first = (state.me.full_name || state.me.username).split(" ")[0];
  $("#dashHello").textContent = "Bonjour " + first + " 👋";

  // Fond : celui du dashboard, sinon le fond global des réglages
  setBg(d.background_url || state.branding.dashboard_background);

  const sec = $("#sections");
  if (!d.groups.length) {
    sec.innerHTML = state.me.is_admin
      ? `<div class="empty glass">Ce dashboard n'a pas encore de section.<br><button class="btn-accent" id="emptyAdd">Ajouter des applications</button></div>`
      : `<div class="empty glass">Aucune application sur ce dashboard.</div>`;
    if (state.me.is_admin) $("#emptyAdd").onclick = () => enterAdmin("apps");
    return;
  }
  const editing = state.me.is_admin && state.editMode;
  let n = 0;                                            // rang global des tuiles : cascade d'une section à l'autre
  sec.innerHTML = d.groups.map((g) => `
    <section class="section" data-agid="${g.id}">
      <div class="section-head" ${editing ? 'draggable="true"' : ""}>${editing ? `<span class="drag-h" title="Glisser pour réordonner">⠿</span>` : ""}
        <span class="bar" ${g.color ? `style="background:${esc(g.color)}"` : ""}></span>
        <h2>${esc(g.name)}</h2><span class="cnt">${g.apps.length}</span></div>
      <div class="grid" data-gid="${g.id}">${g.apps.map((a) => tileHtml(a, editing, n++)).join("") || `<div class="muted">Vide — ajoutez des applications à cette section dans l'administration.</div>`}</div>
    </section>`).join("");
  if (editing) enableDnD();
}

function tileHtml(a, admin, i = 0) {
  const ico = a.image_url ? `<img src="${esc(a.image_url)}" alt="" loading="lazy">` : `<span class="ph">${esc(initials(a.name))}</span>`;
  const tip = a.tooltip ? `<span class="tip">${esc(a.tooltip)}</span>` : "";
  const tgt = a.open_new_tab ? ` target="_blank" rel="noopener"` : "";
  return `<a class="tile glass" style="--i:${i}" href="${esc(a.url)}"${tgt} data-aid="${a.id}"${admin ? ' draggable="true"' : ""}><div class="ico">${ico}</div><span class="tname">${esc(a.name)}</span>${tip}</a>`;
}

// Glisser-déposer (admin) : tuiles dans une section, sections entre elles.
function enableDnD() {
  $$("#sections .tile").forEach((t) => t.addEventListener("click", (e) => { if (state.editMode) e.preventDefault(); }));
  $$("#sections .grid").forEach((grid) => makeSortable(grid, ".tile", async (ids) => {
    try { await api(`/api/app-groups/${grid.dataset.gid}/reorder`, { method: "POST", body: JSON.stringify({ ids }) }); toast("Ordre enregistré"); } catch (e) { toast(e.message); }
  }, "aid"));
  makeSortable($("#sections"), ".section", async (ids) => {
    try { await api(`/api/dashboards/${state.dash.id}/reorder`, { method: "POST", body: JSON.stringify({ ids }) }); toast("Ordre enregistré"); } catch (e) { toast(e.message); }
  }, "agid", ".section-head");
}

function makeSortable(container, itemSel, onDrop, dataKey, handleSel) {
  let dragEl = null;
  $$(itemSel, container).forEach((it) => {
    const handle = handleSel ? it.querySelector(handleSel) : it;
    if (!handle) return;
    handle.addEventListener("dragstart", (e) => { dragEl = it; it.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    handle.addEventListener("dragend", () => {
      if (!dragEl) return; dragEl.classList.remove("dragging"); dragEl = null;
      onDrop($$(itemSel, container).map((x) => Number(x.dataset[dataKey])));
    });
  });
  container.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (!dragEl || dragEl.parentElement !== container) return;
    const after = afterElement(container, itemSel, e.clientX, e.clientY);
    if (after == null) container.appendChild(dragEl);
    else container.insertBefore(dragEl, after);
  });
}

function afterElement(container, itemSel, x, y) {
  const els = $$(itemSel + ":not(.dragging)", container).filter((el) => el.parentElement === container);
  let closest = null, closestDist = Infinity;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (y < r.bottom && (y < r.top || x < cx)) {
      const dist = Math.hypot(x - cx, y - cy);
      if (dist < closestDist) { closestDist = dist; closest = el; }
    }
  }
  return closest;
}

async function loadWeather(slug) {
  try {
    const w = await api("/api/me/weather" + (slug ? "?site=" + encodeURIComponent(slug) : ""));
    $("#wIcon").textContent = w.icon; $("#wTemp").textContent = w.temperature + "°";
    $("#wLabel").textContent = w.label; $("#wSite").value = w.slug;
  } catch { $("#wIcon").textContent = "🌡️"; $("#wTemp").textContent = "—"; $("#wLabel").textContent = ""; }
}

// ---------- Recherche (apps + web) ----------
const searchInput = $("#searchInput"), searchDrop = $("#searchDrop");
searchInput.addEventListener("input", renderSearch);
searchInput.addEventListener("focus", renderSearch);
searchInput.addEventListener("blur", () => setTimeout(() => searchDrop.classList.add("hidden"), 150));
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") return resetSearch();
  if (e.key === "Enter") {
    const m = matchApps(searchInput.value);
    if (m[0]) window.open(m[0].url, m[0].open_new_tab ? "_blank" : "_self");
    else webSearch();
    resetSearch();
  }
});
document.addEventListener("keydown", (e) => {
  // « / » place le curseur dans la recherche (hors champ de saisie)
  if (e.key === "/" && !$("#view-app").classList.contains("hidden") && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    e.preventDefault(); searchInput.focus();
  }
});
function matchApps(q) {
  const t = q.trim().toLowerCase(); if (!t) return [];
  return state.apps.filter((a) => a.name.toLowerCase().includes(t) || (a.tooltip || "").toLowerCase().includes(t)).slice(0, 8);
}
function webSearch() {
  const q = searchInput.value.trim(), base = state.branding.search_engine_url;
  if (q && base) window.open(base + encodeURIComponent(q), "_blank", "noopener");
}
function resetSearch() {
  searchInput.value = "";
  searchInput.blur();
  searchDrop.classList.add("hidden");
}
function renderSearch() {
  const q = searchInput.value.trim();
  if (!q) { searchDrop.classList.add("hidden"); return; }
  const m = matchApps(q), eng = state.branding.search_engine_name;
  searchDrop.innerHTML =
    m.map((a) => `<div class="search-item" data-url="${esc(a.url)}" data-nt="${a.open_new_tab}">
      ${a.image_url ? `<img src="${esc(a.image_url)}" alt="">` : `<span class="ig">${esc(initials(a.name))}</span>`}
      <span>${esc(a.name)}</span></div>`).join("") +
    (eng ? `<div class="search-item" id="webItem"><span class="ig">🌐</span><span>Rechercher «&nbsp;${esc(q)}&nbsp;» sur ${esc(eng)}</span></div>` : "") +
    (!m.length && !eng ? `<div class="search-item muted">Aucune application trouvée</div>` : "");
  searchDrop.classList.remove("hidden");
  $$(".search-item[data-url], #webItem", searchDrop).forEach((it) => {
    if (it.id === "webItem") { it.onmousedown = () => { webSearch(); resetSearch(); }; return; }
    it.onmousedown = () => { window.open(it.dataset.url, it.dataset.nt === "true" ? "_blank" : "_self"); resetSearch(); };
  });
}

// ---------- Menu utilisateur / navigation ----------
$("#userBtn").onclick = () => $("#userDrop").classList.toggle("hidden");
document.addEventListener("click", (e) => { if (!$("#userMenu").contains(e.target)) $("#userDrop").classList.add("hidden"); });
$("#logoutBtn").onclick = logout;
$("#adminLink").onclick = () => { $("#userDrop").classList.add("hidden"); enterAdmin(); };
$("#goHome").onclick = () => enterApp();
const backToPortal = () => { navigate("back", () => show("app")); enterApp(); };
$("#adminBrand").onclick = backToPortal;
$("#backPortal").onclick = backToPortal;

// ---- Mode édition (admin) ----
$("#editToggle").onclick = () => {
  if (!state.me?.is_admin) return;
  state.editMode = !state.editMode;
  applyEditMode();
  if (state.dash) renderDashboard(state.dash, false);
};
function applyEditMode() {
  const on = state.editMode, btn = $("#editToggle");
  btn.textContent = on ? "🔒" : "✏️";
  btn.title = on ? "Verrouiller" : "Mode édition";
  btn.classList.toggle("active", on);
  $("#view-app").classList.toggle("editing", on);
}

// ---------- Modale ----------
function modal(title, bodyHtml, onSave, saveLabel = "Enregistrer") {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = bodyHtml;
  $("#modalFoot").innerHTML = `<button class="btn-ghost" id="mCancel">Annuler</button>${onSave ? `<button class="btn-accent" id="mSave">${esc(saveLabel)}</button>` : ""}`;
  clearTimeout(closeModal._t);
  $("#modal").classList.remove("hidden", "closing");
  $("#mCancel").onclick = closeModal;
  $("#modalClose").onclick = closeModal;
  if (onSave) $("#mSave").onclick = async () => {
    const b = $("#mSave"); b.classList.add("loading");
    try { await onSave(); closeModal(); } catch (e) { toast(e.message); } finally { b.classList.remove("loading"); }
  };
}
function closeModal() {
  const m = $("#modal");
  if (m.classList.contains("hidden") || m.classList.contains("closing")) return;
  m.classList.add("closing");
  closeModal._t = setTimeout(() => m.classList.add("hidden"), 170);
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) closeModal(); });

function checklist(id, options, selected) {
  const sel = new Set(selected || []);
  return `<div class="checklist" id="${id}">${options.length ? options.map((o) =>
    `<label><input type="checkbox" value="${o.id}" ${sel.has(o.id) ? "checked" : ""}>${esc(o.label)}</label>`).join("")
    : `<div class="muted" style="padding:.4rem">Aucun élément</div>`}</div>`;
}
function checkedIds(id) { return $$("#" + id + " input:checked").map((i) => Number(i.value)); }
const confirmDel = (what) => confirm(`Supprimer ${what} ? Cette action est définitive.`);

// ============================================================
//  ADMINISTRATION
// ============================================================
const adminData = {};
const ADMIN_TABS = {};
function enterAdmin(tab = "apps") {
  if (!state.me.is_admin) return;
  if ($("#view-admin").classList.contains("hidden")) navigate("fwd", () => show("admin"));
  setBg(state.branding.dashboard_background);
  $$("#adminNav a").forEach((a) => (a.onclick = () => selectAdminTab(a.dataset.tab)));
  selectAdminTab(tab);
}
// Entrée en cascade du contenu d'un onglet, une fois ce contenu affiché (posée au clic,
// elle rejouait l'animation de l'onglet précédent, encore à l'écran pendant le chargement).
// Pas à chaque enregistrement : seulement à l'ouverture d'un onglet.
function playAdminEnter() {
  const main = $("#adminMain");
  $$(".list > .row, .atable tbody tr", main).forEach((el, i) => el.style.setProperty("--i", i));
  main.classList.remove("enter"); void main.offsetWidth; main.classList.add("enter");
  clearTimeout(playAdminEnter._t); playAdminEnter._t = setTimeout(() => main.classList.remove("enter"), 900);
}

// Clics rapides entre onglets : un onglet lent qui finit après le suivant écraserait
// son contenu. Dans ce cas, l'onglet demandé en dernier est simplement réaffiché.
let _tabSeq = 0, _tabDone = false, _tabCur = null;
function selectAdminTab(tab) {
  const seq = ++_tabSeq;
  _tabCur = tab; _tabDone = false;
  $$("#adminNav a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  runAdminTab(tab, seq);
}
function runAdminTab(tab, seq) {
  ADMIN_TABS[tab]()
    .catch((e) => { if (seq === _tabSeq) $("#adminMain").innerHTML = `<div class="glass panel"><p class="muted">Erreur : ${esc(e.message)}</p></div>`; })
    .finally(() => {
      if (seq === _tabSeq) {
        _tabDone = true;
        playAdminEnter();
      }
      else if (_tabDone) { _tabDone = false; runAdminTab(_tabCur, _tabSeq); }
    });
}

async function loadRefs() {
  const [apps, appGroups, dashboards, groups] = await Promise.all([
    api("/api/apps"), api("/api/app-groups"), api("/api/dashboards"), api("/api/groups"),
  ]);
  Object.assign(adminData, { apps, appGroups, dashboards, groups });
}
const groupOpts = () => adminData.groups.map((g) => ({ id: g.id, label: g.is_everyone ? "★ " + g.name + " (tous les connectés)" : g.name + (g.source === "ad" ? " (AD)" : "") }));

// ---- Changelog ----
// Rendu Markdown minimal (titres, listes, gras, code, liens), échappé.
function mdToHtml(md) {
  const esch = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (t) => esch(t)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = []; let inList = false;
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  md.split(/\r?\n/).forEach((line) => {
    const l = line.trim(); let m;
    if (!l) closeList();
    else if ((m = l.match(/^#\s+(.*)/))) { closeList(); out.push(`<h2>${inline(m[1])}</h2>`); }
    else if ((m = l.match(/^##\s+(.*)/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); }
    else if ((m = l.match(/^###\s+(.*)/))) { closeList(); out.push(`<h4>${inline(m[1])}</h4>`); }
    else if ((m = l.match(/^[-*]\s+(.*)/))) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${inline(m[1])}</li>`); }
    else { closeList(); out.push(`<p>${inline(l)}</p>`); }
  });
  closeList();
  return out.join("\n");
}
ADMIN_TABS.changelog = async function () {
  const d = await api("/api/changelog");
  $("#adminMain").innerHTML = `<div class="admin-head"><h1>Changelog</h1><span class="muted">v${esc(d.version)}</span></div>
    <div class="glass panel changelog-body" style="line-height:1.55">${mdToHtml(d.markdown || "")}</div>`;
};

// ---- Applications ----
ADMIN_TABS.apps = async function () {
  await loadRefs();
  const m = $("#adminMain");
  m.innerHTML = `<div class="admin-head"><h1>Applications</h1><button class="btn-accent" id="addApp">＋ Ajouter</button></div>
    <div class="list">${adminData.apps.map((a) => `
      <div class="row glass">
        ${a.image_url ? `<img class="logo" src="${esc(a.image_url)}" alt="">` : `<span class="ph">${esc(initials(a.name))}</span>`}
        <div class="grow"><div class="t">${esc(a.name)} ${a.is_active ? "" : `<span class="badge off">masquée</span>`}</div><div class="s">${esc(a.url)}</div></div>
        <button class="icon-btn" data-edit="${a.id}" title="Modifier">✏️</button>
        <button class="icon-btn" data-del="${a.id}" title="Supprimer">🗑️</button>
      </div>`).join("") || `<div class="glass empty">Aucune application. Cliquez sur « Ajouter ».</div>`}</div>`;
  $("#addApp").onclick = () => appForm();
  $$("[data-edit]", m).forEach((b) => (b.onclick = () => appForm(adminData.apps.find((x) => x.id == b.dataset.edit))));
  $$("[data-del]", m).forEach((b) => (b.onclick = async () => {
    if (!confirmDel("cette application")) return;
    try { await api("/api/apps/" + b.dataset.del, { method: "DELETE" }); ADMIN_TABS.apps(); } catch (e) { toast(e.message); }
  }));
};

function appForm(a) {
  const agOpts = adminData.appGroups.map((g) => ({ id: g.id, label: g.name }));
  const everyone = adminData.groups.find((g) => g.is_everyone);
  modal(a ? "Modifier l'application" : "Ajouter une application", `
    ${a ? "" : `<label>Suggestions</label><div class="chips">${APP_CATALOG.map((c, k) => `<button type="button" class="chip" data-k="${k}"><img src="${esc(c.logo)}" alt="" loading="lazy">${esc(c.name)}</button>`).join("")}</div>`}
    <label>Nom</label><input id="fName" value="${esc(a?.name || "")}">
    <label>Lien (URL)</label>
    <div class="row-inline"><input id="fUrl" placeholder="https://…" value="${esc(a?.url || "")}">
      <button class="btn-ghost shrink" id="fLogo" type="button">✨ Logo auto</button></div>
    <label>Logo (URL ou envoi)</label>${imagePickerHtml("fImg", a?.image_url, { plate: true })}
    <label>Texte au survol</label><input id="fTip" value="${esc(a?.tooltip || "")}">
    <div class="row-inline" style="margin-top:.6rem">
      <label class="check"><input type="checkbox" id="fNew" ${a ? (a.open_new_tab ? "checked" : "") : "checked"}> Ouvrir dans un nouvel onglet</label>
      <label class="check"><input type="checkbox" id="fActive" ${a ? (a.is_active ? "checked" : "") : "checked"}> Affichée</label>
    </div>
    <label>Sections</label>${checklist("fAg", agOpts, a?.app_group_ids)}
    <label>Visible par les groupes</label>${checklist("fGr", groupOpts(), a ? a.group_ids : (everyone ? [everyone.id] : []))}
  `, async () => {
    const payload = {
      name: $("#fName").value.trim(), url: $("#fUrl").value.trim(),
      image_url: $("#fImg").value.trim(), tooltip: $("#fTip").value.trim(),
      open_new_tab: $("#fNew").checked, is_active: $("#fActive").checked, sort_order: a?.sort_order || 0,
      app_group_ids: checkedIds("fAg"), group_ids: checkedIds("fGr"),
    };
    if (!payload.name || !payload.url) throw new Error("Nom et URL requis");
    if (!/^[a-z][a-z0-9+.-]*:/i.test(payload.url)) payload.url = "https://" + payload.url;
    await api(a ? "/api/apps/" + a.id : "/api/apps", { method: a ? "PATCH" : "POST", body: JSON.stringify(payload) });
    toast("Application enregistrée"); ADMIN_TABS.apps();
  });
  bindImagePicker("fImg");
  $$("#modalBody .chip").forEach((c) => (c.onclick = () => {
    const s = APP_CATALOG[Number(c.dataset.k)];
    $("#fName").value = s.name; $("#fUrl").value = s.url; $("#fTip").value = s.tip;
    $("#fImg").value = s.logo; $("#fImg").dispatchEvent(new Event("input"));
    if (!s.url) $("#fUrl").focus();
  }));
  $("#fLogo").onclick = async () => {
    const url = $("#fUrl").value.trim(), name = $("#fName").value.trim();
    if (!url && !name) return toast("Saisir l'URL ou le nom d'abord");
    $("#fLogo").textContent = "…";
    try {
      const p = new URLSearchParams(); if (url) p.append("url", url); if (name) p.append("name", name);
      const r = await api("/api/logo?" + p);
      if (r.logo) { $("#fImg").value = r.logo; $("#fImg").dispatchEvent(new Event("input")); toast("Logo trouvé"); }
      else toast("Aucun logo trouvé");
    } catch (err) { toast(err.message); } finally { $("#fLogo").textContent = "✨ Logo auto"; }
  };
}

// ---- Sections (groupes d'applications) ----
ADMIN_TABS.appgroups = async function () {
  await loadRefs();
  const m = $("#adminMain");
  m.innerHTML = `<div class="admin-head"><h1>Sections</h1><button class="btn-accent" id="add">＋ Ajouter</button></div>
    <p class="muted" style="margin:-.6rem 0 1rem">Une section regroupe des applications ; elle peut être affichée sur plusieurs dashboards.</p>
    <div class="list">${adminData.appGroups.map((g) => `
      <div class="row glass"><span style="width:6px;height:34px;border-radius:4px;background:${esc(g.color || "var(--accent)")}"></span>
        <div class="grow"><div class="t">${esc(g.name)}</div><div class="s">${g.app_ids.length} application(s)</div></div>
        <button class="icon-btn" data-edit="${g.id}" title="Modifier">✏️</button><button class="icon-btn" data-del="${g.id}" title="Supprimer">🗑️</button>
      </div>`).join("") || `<div class="glass empty">Aucune section.</div>`}</div>`;
  $("#add").onclick = () => appGroupForm();
  $$("[data-edit]", m).forEach((b) => (b.onclick = () => appGroupForm(adminData.appGroups.find((x) => x.id == b.dataset.edit))));
  $$("[data-del]", m).forEach((b) => (b.onclick = async () => {
    if (!confirmDel("cette section (les applications sont conservées)")) return;
    try { await api("/api/app-groups/" + b.dataset.del, { method: "DELETE" }); ADMIN_TABS.appgroups(); } catch (e) { toast(e.message); }
  }));
};
function appGroupForm(g) {
  const opts = adminData.apps.map((a) => ({ id: a.id, label: a.name }));
  modal(g ? "Modifier la section" : "Nouvelle section", `
    <label>Nom</label><input id="gName" value="${esc(g?.name || "")}">
    <div class="row-inline">
      <div><label class="check" style="margin-top:1rem"><input type="checkbox" id="gOwn" ${g?.color ? "checked" : ""}> Couleur propre</label><input type="color" id="gColor" value="${esc(g?.color || state.branding.accent_color || "#3b82f6")}"></div>
      <div><label>Ordre</label><input type="number" id="gOrder" value="${g?.sort_order || 0}"></div>
    </div>
    <label>Applications</label>${checklist("gApps", opts, g?.app_ids)}
  `, async () => {
    const payload = { name: $("#gName").value.trim(), color: $("#gOwn").checked ? $("#gColor").value : null, sort_order: Number($("#gOrder").value), app_ids: checkedIds("gApps") };
    if (!payload.name) throw new Error("Nom requis");
    await api(g ? "/api/app-groups/" + g.id : "/api/app-groups", { method: g ? "PATCH" : "POST", body: JSON.stringify(payload) });
    toast("Section enregistrée"); ADMIN_TABS.appgroups();
  });
  $("#gColor").oninput = () => ($("#gOwn").checked = true);
}

// ---- Dashboards ----
ADMIN_TABS.dashboards = async function () {
  await loadRefs();
  const m = $("#adminMain");
  m.innerHTML = `<div class="admin-head"><h1>Dashboards</h1><button class="btn-accent" id="add">＋ Ajouter</button></div>
    <div class="list">${adminData.dashboards.map((d) => `
      <div class="row glass">
        ${d.background_url ? `<img class="logo" style="width:60px;height:40px;border-radius:8px;object-fit:cover;padding:0" src="${esc(d.background_url)}" alt="">` : `<span class="ph">🧭</span>`}
        <div class="grow"><div class="t">${esc(d.name)} ${d.is_default ? `<span class="badge def">défaut</span>` : ""}</div>
          <div class="s">/${esc(d.slug)} · ${d.app_group_ids.length} section(s) · ${d.group_ids.length} groupe(s)</div></div>
        <button class="icon-btn" data-edit="${d.id}" title="Modifier">✏️</button><button class="icon-btn" data-del="${d.id}" title="Supprimer">🗑️</button>
      </div>`).join("") || `<div class="glass empty">Aucun dashboard.</div>`}</div>`;
  $("#add").onclick = () => dashForm();
  $$("[data-edit]", m).forEach((b) => (b.onclick = () => dashForm(adminData.dashboards.find((x) => x.id == b.dataset.edit))));
  $$("[data-del]", m).forEach((b) => (b.onclick = async () => {
    if (!confirmDel("ce dashboard")) return;
    try { await api("/api/dashboards/" + b.dataset.del, { method: "DELETE" }); ADMIN_TABS.dashboards(); } catch (e) { toast(e.message); }
  }));
};
function dashForm(d) {
  const agOpts = adminData.appGroups.map((g) => ({ id: g.id, label: g.name }));
  const everyone = adminData.groups.find((g) => g.is_everyone);
  modal(d ? "Modifier le dashboard" : "Nouveau dashboard", `
    <div class="row-inline">
      <div><label>Nom</label><input id="dName" value="${esc(d?.name || "")}"></div>
      <div><label>Adresse (slug)</label><input id="dSlug" value="${esc(d?.slug || "")}"></div>
    </div>
    <label>Fond propre (sinon le fond global des réglages)</label>${imagePickerHtml("dBg", d?.background_url, { wide: true })}
    <div class="row-inline">
      <label class="check" style="margin-top:1rem"><input type="checkbox" id="dDef" ${d?.is_default ? "checked" : ""}> Dashboard par défaut</label>
      <div><label>Ordre</label><input type="number" id="dOrder" value="${d?.sort_order || 0}"></div>
    </div>
    <label>Sections affichées</label>${checklist("dAg", agOpts, d?.app_group_ids)}
    <label>Visible par les groupes</label>${checklist("dGr", groupOpts(), d ? d.group_ids : (everyone ? [everyone.id] : []))}
  `, async () => {
    const payload = { name: $("#dName").value.trim(), slug: slugify($("#dSlug").value || $("#dName").value),
      background_url: $("#dBg").value.trim(), is_default: $("#dDef").checked, sort_order: Number($("#dOrder").value),
      app_group_ids: checkedIds("dAg"), group_ids: checkedIds("dGr") };
    if (!payload.name || !payload.slug) throw new Error("Nom requis");
    await api(d ? "/api/dashboards/" + d.id : "/api/dashboards", { method: d ? "PATCH" : "POST", body: JSON.stringify(payload) });
    toast("Dashboard enregistré"); ADMIN_TABS.dashboards();
  });
  bindImagePicker("dBg");
  $("#dName").oninput = () => { if (!d) $("#dSlug").value = slugify($("#dName").value); };
}

// ---- Groupes (locaux + AD) ----
ADMIN_TABS.groups = async function () {
  await loadRefs();
  adminData.sites = await api("/api/sites");
  const m = $("#adminMain");
  const siteOpts = (g) => adminData.sites.map((s) => `<option value="${s.id}" ${g.site_id == s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  const hasAd = adminData.groups.some((g) => g.source === "ad");
  m.innerHTML = `<div class="admin-head"><h1>Groupes</h1><div class="admin-actions">
      ${hasAd ? `<button class="btn-ghost btn-danger" id="delAll">🗑️ Supprimer les groupes AD</button>` : ""}
      <button class="btn-ghost" id="sync">⟳ Synchroniser depuis l'AD</button>
      <button class="btn-accent" id="addGrp">＋ Groupe local</button></div></div>
    <div class="glass table-wrap"><table class="atable"><thead><tr>
      <th>Groupe</th><th>Membres</th><th>Site (météo)</th><th>Dashboard par défaut</th><th></th></tr></thead>
    <tbody>${adminData.groups.map((g) => `<tr>
      <td><b>${esc(g.name)}</b> ${g.is_everyone ? `<span class="badge def">intégré</span>` : g.source === "ad" ? `<span class="badge admin">AD</span>` : `<span class="badge off">local</span>`}
        ${g.description ? `<div class="muted">${esc(g.description)}</div>` : ""}</td>
      <td>${g.is_everyone ? "tous" : g.members}</td>
      <td><select data-site="${g.id}"><option value="">— aucun —</option>${siteOpts(g)}</select></td>
      <td><select data-def="${g.id}"><option value="">— aucun —</option>${adminData.dashboards.map((d) => `<option value="${d.id}" ${g.default_dashboard_id == d.id ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select></td>
      <td style="text-align:right;white-space:nowrap">
        ${g.source === "ad" ? `<button class="icon-btn" data-imp="${g.id}" title="Créer les comptes des membres AD">👥</button>` : ""}
        ${g.source === "local" && !g.is_everyone ? `<button class="icon-btn" data-ren="${g.id}" title="Renommer">✏️</button>` : ""}
        ${g.is_everyone ? "" : `<button class="icon-btn" data-del="${g.id}" title="Supprimer">🗑️</button>`}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted" style="margin-top:.6rem">« Tout le monde » contient tous les utilisateurs connectés. Les groupes locaux s'attribuent dans la fiche utilisateur ; les groupes AD suivent l'annuaire. 👥 = créer les comptes des membres d'un groupe AD.</p>`;
  $("#addGrp").onclick = () => modal("Nouveau groupe local", `<label>Nom</label><input id="ngName"><label>Description</label><input id="ngDesc">`, async () => {
    await api("/api/groups", { method: "POST", body: JSON.stringify({ name: $("#ngName").value, description: $("#ngDesc").value }) });
    toast("Groupe créé"); ADMIN_TABS.groups();
  }, "Créer");
  $("#sync").onclick = async () => {
    $("#sync").textContent = "Synchronisation…";
    try { const r = await api("/api/groups/sync", { method: "POST" }); toast(r.synced + " groupe(s) synchronisé(s)" + (r.skipped ? `, ${r.skipped} ignoré(s) (nom déjà pris par un groupe local)` : "")); ADMIN_TABS.groups(); }
    catch (e) { toast(e.message); $("#sync").textContent = "⟳ Synchroniser depuis l'AD"; }
  };
  if (hasAd) $("#delAll").onclick = async () => {
    if (!confirm("Supprimer tous les groupes issus de l'AD ? Les groupes locaux sont conservés.")) return;
    try { const r = await api("/api/groups", { method: "DELETE" }); toast(r.deleted + " groupe(s) supprimé(s)"); ADMIN_TABS.groups(); } catch (e) { toast(e.message); }
  };
  $$("[data-site]", m).forEach((s) => (s.onchange = async () => { try { await api("/api/groups/" + s.dataset.site, { method: "PATCH", body: JSON.stringify({ site_id: s.value ? Number(s.value) : null }) }); toast("Site mis à jour"); } catch (e) { toast(e.message); } }));
  $$("[data-def]", m).forEach((s) => (s.onchange = async () => { try { await api("/api/groups/" + s.dataset.def, { method: "PATCH", body: JSON.stringify({ default_dashboard_id: s.value ? Number(s.value) : null }) }); toast("Mis à jour"); } catch (e) { toast(e.message); } }));
  $$("[data-imp]", m).forEach((b) => (b.onclick = async () => {
    b.textContent = "…";
    try { const r = await api("/api/groups/" + b.dataset.imp + "/import-users", { method: "POST" }); toast(`${r.created} compte(s) créé(s), ${r.linked} rattaché(s) / ${r.total}`); ADMIN_TABS.groups(); }
    catch (e) { toast(e.message); b.textContent = "👥"; }
  }));
  $$("[data-ren]", m).forEach((b) => (b.onclick = () => {
    const g = adminData.groups.find((x) => x.id == b.dataset.ren);
    modal("Modifier le groupe", `<label>Nom</label><input id="ngName" value="${esc(g.name)}"><label>Description</label><input id="ngDesc" value="${esc(g.description || "")}">`, async () => {
      await api("/api/groups/" + g.id, { method: "PATCH", body: JSON.stringify({ name: $("#ngName").value, description: $("#ngDesc").value }) });
      toast("Groupe mis à jour"); ADMIN_TABS.groups();
    });
  }));
  $$("[data-del]", m).forEach((b) => (b.onclick = async () => {
    if (!confirmDel("ce groupe (et les droits qui lui sont donnés)")) return;
    try { await api("/api/groups/" + b.dataset.del, { method: "DELETE" }); ADMIN_TABS.groups(); } catch (e) { toast(e.message); }
  }));
};

// ---- Utilisateurs ----
ADMIN_TABS.users = async function () {
  await loadRefs();
  const [users, sites] = await Promise.all([api("/api/users"), api("/api/sites")]);
  adminData.users = users; adminData.sites = sites;
  const m = $("#adminMain");
  const src = { local: "Local", ldap: "AD", sso: "SSO" };
  m.innerHTML = `<div class="admin-head"><h1>Utilisateurs</h1><button class="btn-accent" id="addUser">＋ Ajouter</button></div>
    <div class="glass table-wrap"><table class="atable"><thead><tr><th>Utilisateur</th><th>Source</th><th>Site</th><th>Groupes</th><th>Rôle</th><th></th></tr></thead>
    <tbody>${users.map((u) => `<tr>
      <td><b>${esc(u.full_name || u.username)}</b> ${u.is_active ? "" : `<span class="badge off">désactivé</span>`}<div class="muted">${esc(u.email || u.username)}</div></td>
      <td>${src[u.auth_source] || esc(u.auth_source)}</td>
      <td>${esc(u.site || "—")}</td><td>${u.groups}</td>
      <td>${u.role === "ADMIN" ? `<span class="badge admin">Admin</span>` : "Utilisateur"}</td>
      <td style="text-align:right;white-space:nowrap"><button class="icon-btn" data-edit="${u.id}" title="Modifier">✏️</button>${u.id === state.me.id ? "" : `<button class="icon-btn" data-del="${u.id}" title="Supprimer">🗑️</button>`}</td></tr>`).join("")}</tbody></table></div>`;
  $("#addUser").onclick = () => userForm();
  $$("[data-edit]", m).forEach((b) => (b.onclick = () => userForm(users.find((x) => x.id == b.dataset.edit))));
  $$("[data-del]", m).forEach((b) => (b.onclick = async () => {
    if (!confirmDel("cet utilisateur")) return;
    try { await api("/api/users/" + b.dataset.del, { method: "DELETE" }); ADMIN_TABS.users(); } catch (e) { toast(e.message); }
  }));
};
function ovTable(id, items, current) {
  const map = {}; (current || []).forEach((o) => (map[o.id] = o.effect));
  return `<div class="checklist" id="${id}">${items.map((it) => `<div class="ov-row"><span>${esc(it.name)}</span>
    <select data-id="${it.id}"><option value="">Hérité</option>
      <option value="ALLOW" ${map[it.id] === "ALLOW" ? "selected" : ""}>Autoriser</option>
      <option value="DENY" ${map[it.id] === "DENY" ? "selected" : ""}>Refuser</option></select></div>`).join("") || `<div class="muted" style="padding:.4rem">Aucun</div>`}</div>`;
}
function ovValues(id) { return $$("#" + id + " select").filter((s) => s.value).map((s) => ({ id: Number(s.dataset.id), effect: s.value })); }
function userForm(u) {
  const siteOpts = adminData.sites.map((s) => `<option value="${s.id}" ${u?.site_id == s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  const localGroups = adminData.groups.filter((g) => g.source === "local" && !g.is_everyone).map((g) => ({ id: g.id, label: g.name }));
  const isNew = !u, isLocal = !u || u.auth_source === "local", isMe = u && u.id === state.me.id;

  modal(isNew ? "Nouvel utilisateur" : "Modifier — " + (u.full_name || u.username), `
    ${isNew ? `<div class="row-inline"><div><label>Identifiant</label><input id="uUsername" autocomplete="off"></div>
      <div><label>Nom complet</label><input id="uName"></div></div>` :
      isLocal ? `<label>Nom complet</label><input id="uName" value="${esc(u.full_name || "")}">` : `<p class="muted">Compte ${u.auth_source === "sso" ? "SSO" : "Active Directory"} : nom, e-mail et mot de passe viennent de l'annuaire.</p>`}
    ${isLocal ? `<label>E-mail</label><input id="uEmail" value="${esc(u?.email || "")}">
      <label>${isNew ? "Mot de passe (8 caractères minimum)" : "Nouveau mot de passe (vide = inchangé)"}</label><input type="password" id="uPass" autocomplete="new-password">` : ""}
    <div class="row-inline">
      <div><label>Site (météo)</label><select id="uSite"><option value="">— non défini —</option>${siteOpts}</select></div>
      <div><label class="check" style="margin-top:1rem"><input type="checkbox" id="uAdmin" ${u?.role === "ADMIN" ? "checked" : ""} ${isMe ? "disabled" : ""}> Administrateur</label>
        ${isNew ? "" : `<label class="check"><input type="checkbox" id="uActive" ${u.is_active ? "checked" : ""} ${isMe ? "disabled" : ""}> Compte actif</label>`}</div>
    </div>
    <label>Groupes locaux</label>${checklist("uGroups", localGroups, u?.local_group_ids)}
    ${!isNew ? `
    <p class="muted" style="margin-top:1rem">Exceptions : elles priment sur les droits de groupe, « Refuser » l'emporte sur « Autoriser ».</p>
    <label>Applications</label>${ovTable("uApp", adminData.apps, u.app_overrides)}
    <label>Dashboards</label>${ovTable("uDash", adminData.dashboards, u.dashboard_overrides)}` : ""}
  `, async () => {
    const common = {
      role: $("#uAdmin").checked ? "ADMIN" : "USER",
      site_id: $("#uSite").value ? Number($("#uSite").value) : null,
      local_group_ids: checkedIds("uGroups"),
    };
    if (isNew) {
      const payload = { ...common, username: $("#uUsername").value.trim(), full_name: $("#uName").value.trim() || $("#uUsername").value.trim(),
        email: $("#uEmail").value.trim(), password: $("#uPass").value };
      if (!payload.username || !payload.password) throw new Error("Identifiant et mot de passe requis");
      await api("/api/users", { method: "POST", body: JSON.stringify(payload) });
    } else {
      const payload = { ...common, app_overrides: ovValues("uApp"), dashboard_overrides: ovValues("uDash") };
      if (!isMe) payload.is_active = $("#uActive").checked; else delete payload.role;
      if (isLocal) {
        payload.full_name = $("#uName").value.trim();
        payload.email = $("#uEmail").value.trim();
        if ($("#uPass").value) payload.password = $("#uPass").value;
      }
      await api("/api/users/" + u.id, { method: "PATCH", body: JSON.stringify(payload) });
    }
    toast("Utilisateur enregistré"); ADMIN_TABS.users();
  });
}

// ---- Sites (météo) ----
ADMIN_TABS.sites = async function () {
  const sites = await api("/api/sites");
  const m = $("#adminMain");
  m.innerHTML = `<div class="admin-head"><h1>Sites (météo)</h1><button class="btn-accent" id="addSite">＋ Ajouter</button></div>
    <p class="muted" style="margin:-.6rem 0 1rem">La météo du site de l'utilisateur (ou de son groupe) s'affiche dans la barre du haut. Sans site, le widget est masqué. Données Open-Meteo, accès Internet requis.</p>
    <div class="list">${sites.map((s) => `<div class="row glass"><span class="ph">📍</span>
      <div class="grow"><div class="t">${esc(s.name)}</div><div class="s">${s.latitude.toFixed(4)}, ${s.longitude.toFixed(4)}</div></div>
      <button class="icon-btn" data-edit="${s.id}" title="Modifier">✏️</button><button class="icon-btn" data-del="${s.id}" title="Supprimer">🗑️</button></div>`).join("")
      || `<div class="glass empty">Aucun site : le widget météo est masqué.</div>`}</div>`;
  $("#addSite").onclick = () => siteForm();
  $$("[data-edit]", m).forEach((b) => (b.onclick = () => siteForm(sites.find((x) => x.id == b.dataset.edit))));
  $$("[data-del]", m).forEach((b) => (b.onclick = async () => {
    if (!confirmDel("ce site")) return;
    try { await api("/api/sites/" + b.dataset.del, { method: "DELETE" }); ADMIN_TABS.sites(); } catch (e) { toast(e.message); }
  }));
};
function siteForm(s) {
  modal(s ? "Modifier le site" : "Nouveau site", `
    <label>Rechercher une ville</label>${cityPickerHtml("sCity")}
    <label>Nom affiché</label><input id="sName" value="${esc(s?.name || "")}">
    <div class="row-inline"><div><label>Latitude</label><input id="sLat" type="number" step="any" value="${s?.latitude ?? ""}"></div>
      <div><label>Longitude</label><input id="sLon" type="number" step="any" value="${s?.longitude ?? ""}"></div></div>
  `, async () => {
    const payload = { name: $("#sName").value.trim(), latitude: Number($("#sLat").value), longitude: Number($("#sLon").value) };
    if (!payload.name || $("#sLat").value === "" || $("#sLon").value === "") throw new Error("Nom et coordonnées requis");
    await api(s ? "/api/sites/" + s.id : "/api/sites", { method: s ? "PATCH" : "POST", body: JSON.stringify(payload) });
    toast("Site enregistré"); ADMIN_TABS.sites();
  });
  bindCityPicker("sCity", (c) => { $("#sName").value = c.name; $("#sLat").value = c.latitude; $("#sLon").value = c.longitude; });
}

// ---- Mises à jour ----
// Pastille « mise à jour disponible » (admins) : lit l'état enregistré par la
// vérification quotidienne, sans interroger GitHub.
async function refreshUpdateHint() {
  const pill = $("#updPill"), badge = $("#updBadge");
  pill.classList.add("hidden"); badge.classList.add("hidden");
  if (!state.me?.is_admin) return;
  try {
    const u = await api("/api/update/status");
    if (u.status.available && u.status.latest) {
      pill.textContent = "⬆ " + u.status.latest.version;
      pill.title = "MyApps " + u.status.latest.version + " est disponible";
      pill.classList.remove("hidden"); badge.classList.remove("hidden");
      pill.onclick = () => enterAdmin("updates");
    }
  } catch {}
}

const fmtDate = (iso) => { try { return new Date(iso).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }); } catch { return iso || "—"; } };

ADMIN_TABS.updates = async function () {
  const u = await api("/api/update/status");
  renderUpdates(u);
};

function renderUpdates(u) {
  const st = u.status || {}, latest = st.latest || {}, pol = u.policy, m = $("#adminMain");
  const installing = st.installing || u.pending_request;
  let state_html;
  if (installing) state_html = `<span class="big">⏳</span><div><b>Installation de ${esc(st.installing || latest.version || "")} en cours…</b><div class="muted">Le portail va redémarrer, cette page se rechargera seule.</div></div>`;
  else if (st.error) state_html = `<span class="big">⚠️</span><div><b>Vérification impossible</b><div class="muted">${esc(st.error)}</div></div>`;
  else if (st.available) state_html = `<span class="big">⬆</span><div><b>MyApps ${esc(latest.version)} est disponible</b>${st.major ? ` <span class="badge ko">version majeure</span>` : ""}<div class="muted">Publiée le ${esc(fmtDate(latest.published))}</div></div>`;
  else if (st.blocked) state_html = `<span class="big">✋</span><div><b>MyApps ${esc(latest.version)} : mise à jour manuelle</b><div class="muted">${esc(st.blocked)}</div></div>`;
  else if (st.checked_at) state_html = `<span class="big">✅</span><div><b>MyApps est à jour</b></div>`;
  else state_html = `<span class="big">🔎</span><div><b>Pas encore vérifié</b></div>`;

  const hist = (st.history || []).map((h) => `<tr><td>${esc(fmtDate(h.at))}</td><td>${esc(h.version)}</td>
      <td>${h.ok ? `<span class="badge ok">installée</span>` : `<span class="badge ko">échec</span>`}</td><td class="muted">${esc(h.message)}</td></tr>`).join("");

  m.innerHTML = `<div class="admin-head"><h1>Mises à jour</h1><div class="admin-actions">
      <button class="btn-ghost" id="updCheck">🔎 Vérifier maintenant</button>
      ${st.available && !installing ? `<button class="btn-accent" id="updInstall">Installer ${esc(latest.version)}</button>` : ""}</div></div>
    <div class="glass panel">
      <div class="upd-state">${state_html}</div>
      <p class="muted" style="margin:.8rem 0 0">Version installée : <b>${esc(u.current)}</b> · dernière vérification : ${st.checked_at ? esc(fmtDate(st.checked_at)) : "jamais"} · source : <code>github.com/${esc(u.repo)}</code></p>
      ${!u.updater_installed ? `<div class="setup-tip">ℹ️<div>Installation sans <code>install.sh</code> (développement) : la vérification fonctionne, l'installation automatique est indisponible.</div></div>` : ""}
      ${latest.notes && (st.available || st.blocked) ? `<h3 style="margin-top:1rem">Nouveautés de la ${esc(latest.version)}</h3><div class="upd-notes changelog-body">${mdToHtml(latest.notes)}</div>` : ""}
    </div>
    <div class="glass panel">
      <h2>Réglages</h2>
      <div class="radio-list">
        <label><input type="radio" name="updMode" value="notify" ${pol.mode === "notify" ? "checked" : ""}><div>Notification seule <span>Recherche chaque nuit ; une pastille prévient les administrateurs, qui installent d'un clic.</span></div></label>
        <label><input type="radio" name="updMode" value="auto" ${pol.mode === "auto" ? "checked" : ""}><div>Installation automatique la nuit <span>Entre 3 h et 4 h. Les versions majeures restent à valider à la main. Retour automatique à la version précédente en cas d'échec.</span></div></label>
        <label><input type="radio" name="updMode" value="off" ${pol.mode === "off" ? "checked" : ""}><div>Désactivées <span>Aucune connexion au serveur de mises à jour.</span></div></label>
      </div>
      <label>Canal</label>
      <select id="updChannel" style="max-width:320px">
        <option value="stable" ${pol.channel === "stable" ? "selected" : ""}>Stable (recommandé)</option>
        <option value="beta" ${pol.channel === "beta" ? "selected" : ""}>Bêta (pré-versions)</option>
      </select>
      <p class="muted" style="margin-top:.6rem">Chaque version est signée : une archive modifiée ou d'une autre origine est refusée.</p>
    </div>
    ${hist ? `<div class="glass table-wrap" style="margin-bottom:1rem"><table class="atable"><thead><tr><th>Date</th><th>Version</th><th>Résultat</th><th>Détail</th></tr></thead><tbody>${hist}</tbody></table></div>` : ""}
    ${u.log.length ? `<details class="glass panel"><summary style="cursor:pointer">Journal des installations</summary><pre class="upd-log">${esc(u.log.join("\n"))}</pre></details>` : ""}`;

  $("#updCheck").onclick = async () => {
    const b = $("#updCheck"); b.classList.add("loading"); b.disabled = true;
    try { renderUpdates(await api("/api/update/check", { method: "POST" })); refreshUpdateHint(); }
    catch (e) { toast(e.message); b.disabled = false; }
  };
  const save = async () => {
    try {
      const mode = $("input[name=updMode]:checked").value;
      renderUpdates(await api("/api/update/settings", { method: "PUT", body: JSON.stringify({ mode, channel: $("#updChannel").value }) }));
      toast("Réglages des mises à jour enregistrés");
    } catch (e) { toast(e.message); }
  };
  $$("input[name=updMode]").forEach((r) => (r.onchange = save));
  $("#updChannel").onchange = save;
  if ($("#updInstall")) $("#updInstall").onclick = () => installUpdate(latest.version, st.major, u.current);
  if (installing) waitForUpdate(st.installing || latest.version);
}

async function installUpdate(version, major, current) {
  const warnMajor = major ? `\n\nAttention : version majeure (${current} → ${version}). Lisez les notes de version avant de continuer.` : "";
  if (!confirm(`Installer MyApps ${version} ?\n\nLa base est sauvegardée, le portail redémarre (moins d'une minute). En cas d'échec, la version actuelle est rétablie.${warnMajor}`)) return;
  try {
    await api("/api/update/install", { method: "POST", body: JSON.stringify({ allow_major: !!major }) });
    toast("Installation lancée");
    ADMIN_TABS.updates();
  } catch (e) { toast(e.message); }
}

// Suit l'installation : la version servie par /api/health change quand c'est fini.
let _updWatch = null;
function waitForUpdate(version) {
  if (_updWatch) return;
  const t0 = Date.now();
  _updWatch = setInterval(async () => {
    if (Date.now() - t0 > 20 * 60 * 1000) { clearInterval(_updWatch); _updWatch = null; return; }
    try {
      const h = await fetch("/api/health").then((r) => r.json());
      if (h.version === version) {
        clearInterval(_updWatch); _updWatch = null;
        toast("MyApps " + version + " installé — rechargement…");
        setTimeout(() => location.reload(), 1500);
        return;
      }
      const u = await api("/api/update/status");
      if (!u.status.installing && !u.pending_request) {      // terminé sans changer de version : échec
        clearInterval(_updWatch); _updWatch = null;
        if ($("#adminNav a.active")?.dataset.tab === "updates") renderUpdates(u);
        const r = u.status.last_result;
        if (r && !r.ok) toast("Échec de la mise à jour : " + r.message);
      }
    } catch {}                                                 // redémarrage en cours
  }, 3000);
}

// ---- Réglages (apparence, LDAP, SSO) ----
ADMIN_TABS.settings = async function () {
  const s = await api("/api/settings");
  const L = s.ldap, S = s.sso, B = s.branding;
  const isPreset = (u) => BACKGROUNDS.some((x) => x.url === u);
  const m = $("#adminMain");
  m.innerHTML = `<div class="admin-head"><h1>Réglages</h1><div class="admin-actions">
      <button class="btn-ghost" id="rerun">🪄 Relancer l'assistant</button>
      <button class="btn-accent" id="save">Enregistrer</button></div></div>
  <div class="glass panel">
    <h2>Identité</h2>
    <label>Nom du portail</label><input id="bName" maxlength="60" value="${esc(B.portal_name)}">
    <label>Logo</label>${imagePickerHtml("bLogo", B.logo_url, { plate: B.logo_plate })}
    <div class="row-inline" style="margin-top:.4rem">
      <label class="check"><input type="checkbox" id="bShow" ${B.show_name ? "checked" : ""}> Afficher le nom à côté du logo</label>
      <label class="check"><input type="checkbox" id="bPlate" ${B.logo_plate ? "checked" : ""}> Pastille blanche derrière le logo</label>
    </div>
    <label class="check" style="margin-top:.5rem"><input type="checkbox" id="bMotion" ${B.animations !== false ? "checked" : ""}> Animations (transitions, effets au clic) — coupées d'office si le système de l'utilisateur demande moins d'animations</label>
    <label>Couleur d'accent</label>${swatchesHtml("bAccent", B.accent_color)}
    <label>Couleur de fond (en-têtes, voile sur les images, menus)</label>${swatchesHtml("bBase", B.base_color, BASES)}
    <div class="row-inline">
      <div><label>Police des titres</label>${fontSelectHtml("bFontT", B.font_title)}</div>
      <div><label>Police du texte</label>${fontSelectHtml("bFontB", B.font_body)}</div>
    </div>
    <p class="muted" style="margin-top:.3rem">Une police autre que celle du système est chargée depuis Google Fonts par le navigateur de chaque utilisateur.</p>
    <label>Recherche web</label>
    <select id="bSearch">${[["google", "Google"], ["duckduckgo", "DuckDuckGo"], ["bing", "Bing"], ["qwant", "Qwant"], ["none", "Aucune"]]
      .map(([k, l]) => `<option value="${k}" ${B.search_engine === k ? "selected" : ""}>${l}</option>`).join("")}</select>
  </div>
  <div class="glass panel">
    <h2>Fonds d'écran</h2>
    <label>Fond des dashboards</label>${bgGridHtml("bDashGrid", B.dashboard_background)}
    <div style="margin-top:.5rem">${imagePickerHtml("bDash", isPreset(B.dashboard_background) ? "" : B.dashboard_background, { wide: true, placeholder: "…ou votre image (URL ou envoi)" })}</div>
    <label style="margin-top:1rem">Fond de la page de connexion</label>${bgGridHtml("bLoginGrid", B.login_background)}
    <div style="margin-top:.5rem">${imagePickerHtml("bLogin", isPreset(B.login_background) ? "" : B.login_background, { wide: true, placeholder: "…ou votre image (URL ou envoi)" })}</div>
    <p class="muted" style="margin-top:.4rem">Un dashboard qui a son propre fond l'emporte sur le fond global.</p>
  </div>
  <div class="glass panel">
    <h2>Active Directory (LDAP)</h2>
    <label class="check"><input type="checkbox" id="lEn" ${L.enabled ? "checked" : ""}> Activer la connexion avec les comptes de l'annuaire</label>
    <div class="row-inline"><div><label>Serveur</label><input id="lSrv" value="${esc(L.server)}" placeholder="dc01.exemple.local"></div>
      <div class="shrink" style="width:110px"><label>Port</label><input id="lPort" type="number" value="${L.port}"></div>
      <div class="shrink"><label class="check" style="margin-bottom:.7rem"><input type="checkbox" id="lSsl" ${L.use_ssl ? "checked" : ""}> LDAPS</label></div></div>
    <label>Base DN</label><input id="lBase" value="${esc(L.base_dn)}" placeholder="DC=exemple,DC=local">
    <div class="row-inline"><div><label>Filtre utilisateur</label><input id="lFilter" value="${esc(L.search_filter)}"></div>
      <div><label>Filtre des groupes</label><input id="lGFilter" value="${esc(L.group_filter)}"></div></div>
    <label>Attribut du site</label><input id="lSite" value="${esc(L.site_attr)}" placeholder="physicalDeliveryOfficeName">
    <div class="row-inline"><div><label>Compte de service (bind)</label><input id="lBind" value="${esc(L.bind_user)}"></div>
      <div><label>Mot de passe du compte de service</label><input id="lPwd" type="password" autocomplete="new-password" placeholder="${L.has_bind_password ? "(inchangé)" : ""}"></div></div>
    <div style="display:flex;align-items:center;gap:.8rem;margin-top:1rem;flex-wrap:wrap">
      <button class="btn-ghost" id="ldapTest">🔌 Tester la connexion</button><span class="muted" id="ldapTestRes"></span></div>
  </div>
  <div class="glass panel">
    <h2>SSO (OpenID Connect)</h2>
    <label class="check"><input type="checkbox" id="sEn" ${S.enabled ? "checked" : ""}> Activer le bouton SSO sur la page de connexion</label>
    <div class="row-inline"><div><label>Nom du fournisseur (bouton)</label><input id="sProv" value="${esc(S.provider)}" placeholder="Authentik, Keycloak, Microsoft…"></div>
      <div><label>Issuer / Authority</label><input id="sAuth" value="${esc(S.authority)}" placeholder="https://sso.exemple.fr/application/o/myapps/"></div></div>
    <div class="row-inline"><div><label>Client ID</label><input id="sId" value="${esc(S.client_id)}"></div>
      <div><label>Client Secret</label><input id="sSec" type="password" autocomplete="new-password" placeholder="${S.has_client_secret ? "(inchangé)" : ""}"></div></div>
    <label>Redirect URI (à déclarer chez le fournisseur)</label><input id="sRedir" value="${esc(S.redirect_uri || location.origin + "/api/auth/sso/callback")}">
    <div class="row-inline"><div><label>Scopes</label><input id="sScopes" value="${esc(S.scopes)}"></div>
      <div><label>Claim des groupes</label><input id="sGroups" value="${esc(S.groups_claim)}"></div></div>
    <label class="check" style="margin-top:.6rem"><input type="checkbox" id="sAuto" ${S.auto_create ? "checked" : ""}> Créer automatiquement les comptes à la première connexion</label>
  </div>`;

  const B2 = { ...B };
  const syncPlate = () => $("#bLogoPrev").classList.toggle("plate", $("#bPlate").checked);
  bindImagePicker("bLogo");
  $("#bPlate").onchange = syncPlate;
  bindSwatches("bAccent", (c) => (B2.accent_color = c));
  bindSwatches("bBase", (c) => (B2.base_color = c));
  bindBgGrid("bDashGrid", (u) => { B2.dashboard_background = u; $("#bDash").value = ""; $("#bDashPrev").style.backgroundImage = ""; });
  bindImagePicker("bDash", (u) => { if (u) { B2.dashboard_background = u; markBgGrid("bDashGrid", u); } });
  bindBgGrid("bLoginGrid", (u) => { B2.login_background = u; $("#bLogin").value = ""; $("#bLoginPrev").style.backgroundImage = ""; });
  bindImagePicker("bLogin", (u) => { if (u) { B2.login_background = u; markBgGrid("bLoginGrid", u); } });

  const payload = () => ({
    branding: { portal_name: $("#bName").value.trim(), logo_url: $("#bLogo").value.trim(), show_name: $("#bShow").checked,
      logo_plate: $("#bPlate").checked, accent_color: B2.accent_color, search_engine: $("#bSearch").value,
      base_color: B2.base_color, font_title: $("#bFontT").value, font_body: $("#bFontB").value,
      animations: $("#bMotion").checked,
      dashboard_background: B2.dashboard_background || "", login_background: B2.login_background || "" },
    ldap: { ldap_enabled: $("#lEn").checked ? "1" : "0", ldap_server: $("#lSrv").value.trim(), ldap_port: $("#lPort").value,
      ldap_use_ssl: $("#lSsl").checked ? "1" : "0", ldap_base_dn: $("#lBase").value.trim(), ldap_search_filter: $("#lFilter").value.trim(),
      ldap_group_filter: $("#lGFilter").value.trim(), ldap_site_attr: $("#lSite").value.trim(), ldap_bind_user: $("#lBind").value.trim(),
      ldap_bind_password: $("#lPwd").value },
    sso: { sso_enabled: $("#sEn").checked ? "1" : "0", sso_provider: $("#sProv").value.trim(), sso_authority: $("#sAuth").value.trim(),
      sso_client_id: $("#sId").value.trim(), sso_client_secret: $("#sSec").value, sso_redirect_uri: $("#sRedir").value.trim(),
      sso_scopes: $("#sScopes").value.trim(), sso_groups_claim: $("#sGroups").value.trim(), sso_auto_create: $("#sAuto").checked ? "1" : "0" },
  });
  const save = async () => {
    const r = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload()) });
    applyBranding(r.branding);
    setBg(r.branding.dashboard_background);
    $("#lPwd").value = ""; $("#sSec").value = "";
  };
  $("#save").onclick = async () => {
    const b = $("#save"); b.classList.add("loading");
    try { await save(); toast("Réglages enregistrés"); } catch (e) { toast(e.message); } finally { b.classList.remove("loading"); }
  };
  $("#ldapTest").onclick = async () => {
    const res = $("#ldapTestRes"); res.textContent = "Test en cours…"; res.style.color = "";
    try {
      await save();
      const r = await api("/api/settings/ldap/test", { method: "POST" });
      res.textContent = (r.ok ? "✅ " : "❌ ") + r.detail;
      res.style.color = r.ok ? "#4ade80" : "#fda4af";
    } catch (e) { res.textContent = "❌ " + e.message; res.style.color = "#fda4af"; }
  };
  $("#rerun").onclick = async () => {
    if (!confirm("Relancer l'assistant ? Il permet de revoir l'identité, les fonds et de créer un nouveau dashboard. Rien n'est supprimé.")) return;
    try { await api("/api/setup/restart", { method: "POST" }); state.setupRequired = true; startWizard({ adminExists: true }); } catch (e) { toast(e.message); }
  };
};

boot();
