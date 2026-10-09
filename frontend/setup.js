/* MyApps — assistant de premier démarrage
   Étapes : bienvenue (code d'installation) → compte administrateur → identité →
   fonds d'écran → premier dashboard → applications → terminé.
   Les deux premières n'existent que tant qu'aucun administrateur n'a été créé ;
   la suite utilise les routes d'administration habituelles avec le jeton obtenu. */
"use strict";

const wz = {
  steps: [], i: 0, code: "", err: "",
  branding: {}, dash: { name: "Accueil", section: "Mes applications", everyone: true, weather: true, city: null },
  dashId: null, sectionId: null, everyoneId: null, siteId: null, apps: [],
};

function startWizard({ adminExists }) {
  // Nouvelle passe (premier démarrage ou relance depuis les réglages) : on repart à zéro.
  Object.assign(wz, {
    branding: { ...state.branding }, bgInit: false, sameBg: true, code: "",
    dash: { name: "Accueil", section: "Mes applications", everyone: true, weather: true, city: null },
    dashId: null, sectionId: null, everyoneId: null, siteId: null, apps: [],
  });
  wz.steps = [
    ...(adminExists ? [] : [STEP_WELCOME, STEP_ADMIN]),
    STEP_IDENTITY, STEP_BACKGROUNDS, STEP_DASHBOARD, STEP_APPS, STEP_DONE,
  ];
  wz.i = 0;
  ["login", "app", "admin"].forEach((v) => $("#view-" + v).classList.add("hidden"));
  $("#splash").classList.add("hidden");
  $("#view-setup").classList.remove("hidden");
  setBg(state.branding.login_background || state.branding.dashboard_background || "");
  renderWizard();
}

function wizardLocked(idx) {
  // Une fois l'administrateur créé, on ne revient plus sur le code ni le compte.
  return state.token && (wz.steps[idx] === STEP_WELCOME || wz.steps[idx] === STEP_ADMIN);
}

function renderWizard() {
  const step = wz.steps[wz.i];
  $("#setupSteps").innerHTML = wz.steps.map((s, k) =>
    `<li class="${k < wz.i ? "done" : ""}${k === wz.i ? " cur" : ""}"><span class="n">${k < wz.i ? "✓" : k + 1}</span><span class="t">${esc(s.title)}</span></li>`).join("");
  $("#setupProgress").style.width = Math.round((wz.i / (wz.steps.length - 1)) * 100) + "%";
  wz.err = "";
  $("#setupBody").innerHTML = step.html() + `<div class="setup-err hidden" id="wzErr"></div>`;

  const canBack = wz.i > 0 && !wizardLocked(wz.i - 1) && step !== STEP_DONE;
  const later = state.token && step !== STEP_DONE && !wizardLocked(wz.i);
  $("#setupFoot").innerHTML = `
    ${canBack ? `<button class="btn-ghost" id="wzBack">← Précédent</button>` : ""}
    ${later ? `<button class="btn-link" id="wzLater" title="L'assistant reprendra à votre prochaine connexion">Terminer plus tard</button>` : ""}
    <span class="spacer"></span>
    ${step.skip ? `<button class="btn-ghost" id="wzSkip">${esc(step.skip)}</button>` : ""}
    <button class="btn-accent" id="wzNext">${esc(step.nextLabel || "Continuer →")}</button>`;
  if (canBack) $("#wzBack").onclick = () => { wz.i--; navigate("back", renderWizard); };
  if (later) $("#wzLater").onclick = () => { $("#view-setup").classList.add("hidden"); enterApp(); };
  if (step.skip) $("#wzSkip").onclick = () => { wz.i++; navigate("fwd", renderWizard); };
  $("#wzNext").onclick = wizardNext;
  step.bind?.();
  const first = $("#setupBody input:not([type=checkbox]):not([type=color]):not([type=file])");
  if (first && step.focus !== false) first.focus();
}

async function wizardNext() {
  const btn = $("#wzNext"), step = wz.steps[wz.i];
  btn.classList.add("loading");
  wizErr("");
  try {
    if (step.next) await step.next();
    wz.i = Math.min(wz.i + 1, wz.steps.length - 1);
    navigate("fwd", renderWizard);
  } catch (e) {
    wizErr(e.message);
  } finally {
    btn.classList.remove("loading");
  }
}

function wizErr(msg) {
  const el = $("#wzErr"); if (!el) return;
  el.textContent = msg; el.classList.toggle("hidden", !msg);
}

const val = (id) => ($("#" + id)?.value || "").trim();

// ---------------------------------------------------------------------------
const STEP_WELCOME = {
  title: "Bienvenue",
  html: () => `
    <h1>Bienvenue 👋</h1>
    <p class="lead">Configurons votre portail d'applications en quelques minutes. Tout restera modifiable ensuite dans <b>Administration</b>.</p>
    <ul class="feature-list">
      <li>🎨<div><b>À vos couleurs</b>Nom, logo, couleur et fonds d'écran.</div></li>
      <li>🧭<div><b>Dashboards</b>Des pages de tuiles par équipe ou par métier.</div></li>
      <li>🔐<div><b>Droits par groupe</b>Comptes locaux, Active Directory ou SSO.</div></li>
      <li>🔎<div><b>Recherche</b>Retrouver une application au clavier.</div></li>
    </ul>
    <h3>Code d'installation</h3>
    <input class="setup-code" id="wzCode" placeholder="XXXX-XXXX-XX" autocomplete="off" spellcheck="false" value="${esc(wz.code)}">
    <div class="setup-tip">🔑<div>Ce code prouve que vous êtes bien l'administrateur de la machine. Il est affiché à la fin du script d'installation, ou avec&nbsp;:<br><code>sudo cat /var/lib/myapps/setup-code</code> <span class="muted">(adapter le dossier si l'installation a été faite avec --name ou --data)</span></div></div>`,
  async next() {
    const code = val("wzCode");
    if (!code) throw new Error("Saisissez le code d'installation.");
    const r = await fetch("/api/setup/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || "Code refusé"); }
    wz.code = code;
  },
};

function pwScore(p) {
  let s = 0;
  if (p.length >= 8) s++;
  if (p.length >= 12) s++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s++;
  if (/\d/.test(p)) s++;
  if (/[^A-Za-z0-9]/.test(p)) s++;
  return Math.min(s, 4);
}

const STEP_ADMIN = {
  title: "Administrateur",
  html: () => `
    <h1>Compte administrateur</h1>
    <p class="lead">Ce compte local gère le portail. Vous pourrez ensuite ajouter d'autres administrateurs, ou brancher un annuaire Active Directory.</p>
    <div class="row-inline">
      <div><label>Identifiant</label><input id="wzUser" value="admin" autocomplete="username"></div>
      <div><label>Nom affiché</label><input id="wzName" placeholder="Prénom Nom"></div>
    </div>
    <label>E-mail (facultatif)</label><input id="wzMail" type="email" placeholder="vous@exemple.fr">
    <div class="row-inline">
      <div><label>Mot de passe (8 caractères minimum)</label><input id="wzPw" type="password" autocomplete="new-password"><div class="pw-meter"><span id="wzPwMeter"></span></div></div>
      <div><label>Confirmation</label><input id="wzPw2" type="password" autocomplete="new-password"><div class="pw-meter" style="visibility:hidden"><span></span></div></div>
    </div>`,
  bind() {
    $("#wzPw").oninput = () => {
      const s = pwScore($("#wzPw").value);
      const m = $("#wzPwMeter");
      m.style.width = (s / 4) * 100 + "%";
      m.style.background = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#10b981"][s];
    };
  },
  async next() {
    const pw = $("#wzPw").value, pw2 = $("#wzPw2").value;
    if (!val("wzUser")) throw new Error("Identifiant requis.");
    if (pw.length < 8) throw new Error("Mot de passe trop court (8 caractères minimum).");
    if (pw !== pw2) throw new Error("Les deux mots de passe ne correspondent pas.");
    const r = await fetch("/api/setup/admin", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: wz.code, username: val("wzUser"), full_name: val("wzName"), email: val("wzMail"), password: pw }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "Création impossible");
    state.token = j.access_token; state.me = j.user; saveToken(state.token);
  },
};

const STEP_IDENTITY = {
  title: "Identité",
  html: () => {
    const b = wz.branding;
    return `
    <h1>Identité du portail</h1>
    <p class="lead">Le nom et le logo apparaissent sur la page de connexion, dans la barre du haut et dans l'onglet du navigateur.</p>
    <label>Nom du portail</label><input id="wzPortal" maxlength="60" value="${esc(b.portal_name || "MyApps")}" placeholder="Intranet, Mon entreprise…">
    <label>Logo (PNG, SVG… — facultatif)</label>${imagePickerHtml("wzLogo", b.logo_url, { plate: b.logo_plate })}
    <div class="row-inline" style="margin-top:.4rem">
      <label class="check"><input type="checkbox" id="wzShowName" ${b.show_name !== false ? "checked" : ""}> Afficher le nom à côté du logo</label>
      <label class="check"><input type="checkbox" id="wzPlate" ${b.logo_plate ? "checked" : ""}> Pastille blanche derrière le logo</label>
    </div>
    <label>Couleur d'accent</label>${swatchesHtml("wzAccent", b.accent_color)}
    <div class="row-inline">
      <div><label>Couleur de fond</label>${swatchesHtml("wzBase", b.base_color, BASES)}</div>
      <div><label>Police</label>${fontSelectHtml("wzFont", b.font_title)}</div>
    </div>
    <label>Recherche web dans la barre du haut</label>
    <select id="wzSearch">
      ${[["google", "Google"], ["duckduckgo", "DuckDuckGo"], ["bing", "Bing"], ["qwant", "Qwant"], ["none", "Aucune (applications seulement)"]]
        .map(([k, l]) => `<option value="${k}" ${b.search_engine === k ? "selected" : ""}>${l}</option>`).join("")}
    </select>
    <div class="preview-bar"><span class="brand-slot"></span><span class="fake-search"></span><span class="fake-btn">Bouton</span></div>
    <p class="muted" style="margin-top:.4rem">Aperçu en direct. Sans logo, un monogramme est généré avec les initiales du nom.</p>`;
  },
  bind() {
    const live = () => {
      Object.assign(wz.branding, {
        portal_name: val("wzPortal") || "MyApps", logo_url: val("wzLogo"),
        show_name: $("#wzShowName").checked, logo_plate: $("#wzPlate").checked,
      });
      $("#wzLogoPrev").classList.toggle("plate", wz.branding.logo_plate);
      applyBranding(wz.branding);
    };
    bindImagePicker("wzLogo", live);
    $("#wzPortal").oninput = live;
    $("#wzShowName").onchange = live;
    $("#wzPlate").onchange = live;
    bindSwatches("wzAccent", (c) => { wz.branding.accent_color = c; live(); });
    bindSwatches("wzBase", (c) => { wz.branding.base_color = c; live(); });
    $("#wzFont").onchange = () => { wz.branding.font_title = wz.branding.font_body = $("#wzFont").value; live(); };
    $("#wzSearch").onchange = () => (wz.branding.search_engine = $("#wzSearch").value);
    live();
  },
  async next() {
    const b = wz.branding;
    const r = await api("/api/settings", { method: "PUT", body: JSON.stringify({ branding: {
      portal_name: b.portal_name, logo_url: b.logo_url || "", show_name: b.show_name, logo_plate: b.logo_plate,
      accent_color: b.accent_color || "#3b82f6", search_engine: $("#wzSearch").value,
      base_color: b.base_color || "#0b1020", font_title: b.font_title || "system", font_body: b.font_body || "system",
    } }) });
    applyBranding(r.branding);
    wz.branding = { ...r.branding };
  },
};

const STEP_BACKGROUNDS = {
  title: "Fonds d'écran",
  html: () => {
    const b = wz.branding;
    if (!wz.bgInit) {
      // Première visite : on propose un fond plutôt qu'un écran vide.
      wz.bgInit = true;
      if (!b.dashboard_background) b.dashboard_background = BACKGROUNDS[0].url;
      if (!b.login_background) b.login_background = b.dashboard_background;
      wz.sameBg = b.login_background === b.dashboard_background;
    }
    return `
    <h1>Fonds d'écran</h1>
    <p class="lead">Choisissez un fond proposé ou envoyez vos images (photo de vos locaux, visuel de marque…). Une image large, au moins 1920 px, rend mieux.</p>
    <h3>Fond des dashboards</h3>
    ${bgGridHtml("wzBgDash", b.dashboard_background)}
    <label>…ou votre image</label>${imagePickerHtml("wzBgDashUrl", BACKGROUNDS.some((x) => x.url === b.dashboard_background) ? "" : b.dashboard_background, { wide: true })}
    <label class="check" style="margin-top:1rem"><input type="checkbox" id="wzSameBg" ${wz.sameBg ? "checked" : ""}> Même fond pour la page de connexion</label>
    <div id="wzLoginBg" class="${wz.sameBg ? "hidden" : ""}">
      <h3>Fond de la page de connexion</h3>
      ${bgGridHtml("wzBgLogin", b.login_background)}
      <label>…ou votre image</label>${imagePickerHtml("wzBgLoginUrl", BACKGROUNDS.some((x) => x.url === b.login_background) ? "" : b.login_background, { wide: true })}
    </div>
    <div class="preview-dash" id="wzPreview"><span class="pv-title">${esc(b.portal_name || "MyApps")}</span><div class="pv-tiles"><i></i><i></i><i></i><i></i><i></i><i></i></div></div>`;
  },
  bind() {
    const b = wz.branding;
    const show = () => {
      $("#wzPreview").style.backgroundImage = b.dashboard_background ? `url("${b.dashboard_background}")` : "";
      setBg(b.dashboard_background);
      if (wz.sameBg) b.login_background = b.dashboard_background;
    };
    bindBgGrid("wzBgDash", (url) => { b.dashboard_background = url; $("#wzBgDashUrl").value = ""; $("#wzBgDashUrlPrev").style.backgroundImage = ""; show(); });
    bindImagePicker("wzBgDashUrl", (url) => { if (url) { b.dashboard_background = url; markBgGrid("wzBgDash", url); show(); } });
    bindBgGrid("wzBgLogin", (url) => { b.login_background = url; $("#wzBgLoginUrl").value = ""; $("#wzBgLoginUrlPrev").style.backgroundImage = ""; });
    bindImagePicker("wzBgLoginUrl", (url) => { if (url) { b.login_background = url; markBgGrid("wzBgLogin", url); } });
    $("#wzSameBg").onchange = () => {
      wz.sameBg = $("#wzSameBg").checked;
      $("#wzLoginBg").classList.toggle("hidden", wz.sameBg);
      if (wz.sameBg) b.login_background = b.dashboard_background;
      else markBgGrid("wzBgLogin", b.login_background);
    };
    show();
  },
  focus: false,
  async next() {
    const b = wz.branding;
    const r = await api("/api/settings", { method: "PUT", body: JSON.stringify({ branding: {
      dashboard_background: b.dashboard_background || "", login_background: (wz.sameBg ? b.dashboard_background : b.login_background) || "",
    } }) });
    applyBranding(r.branding);
    wz.branding = { ...r.branding };
  },
};

const STEP_DASHBOARD = {
  title: "Dashboard",
  skip: "Passer cette étape",
  html: () => {
    const d = wz.dash;
    return `
    <h1>Premier dashboard</h1>
    <p class="lead">Un dashboard est une page de tuiles. Ses applications sont rangées par sections (ex. « Bureautique », « Outils RH »). Vous en créerez d'autres, par équipe ou par métier, depuis l'administration.</p>
    <div class="row-inline">
      <div><label>Nom du dashboard</label><input id="wzDashName" value="${esc(d.name)}" placeholder="Accueil"></div>
      <div><label>Première section</label><input id="wzSection" value="${esc(d.section)}" placeholder="Mes applications"></div>
    </div>
    <label class="check" style="margin-top:1rem"><input type="checkbox" id="wzEveryone" ${d.everyone ? "checked" : ""}> Visible par tous les utilisateurs connectés</label>
    <p class="muted" style="margin:.2rem 0 0 1.6rem">Décoché, seuls les administrateurs le voient jusqu'à ce que vous l'ouvriez à des groupes.</p>
    <label class="check" style="margin-top:1rem"><input type="checkbox" id="wzWeather" ${d.weather ? "checked" : ""}> Afficher la météo dans la barre du haut</label>
    <div id="wzWeatherBox" class="${d.weather ? "" : "hidden"}" style="margin-left:1.6rem">
      <label>Ville</label>${cityPickerHtml("wzCity")}
      <p class="muted" id="wzCityPicked">${d.city ? "Retenu : " + esc(d.city.name) : "Données Open-Meteo, sans compte ni clé. Ajoutez d'autres sites plus tard dans Administration › Sites."}</p>
    </div>`;
  },
  bind() {
    $("#wzWeather").onchange = () => $("#wzWeatherBox").classList.toggle("hidden", !$("#wzWeather").checked);
    bindCityPicker("wzCity", (c) => { wz.dash.city = c; $("#wzCityPicked").textContent = "Retenu : " + c.name + (c.country ? " (" + c.country + ")" : ""); });
  },
  async next() {
    const d = wz.dash;
    d.name = val("wzDashName"); d.section = val("wzSection");
    d.everyone = $("#wzEveryone").checked; d.weather = $("#wzWeather").checked;
    if (!d.name) throw new Error("Donnez un nom au dashboard.");
    if (!d.section) throw new Error("Donnez un nom à la première section.");
    if (d.weather && !d.city && !wz.siteId) throw new Error("Choisissez une ville pour la météo, ou décochez l'option.");

    const groups = await api("/api/groups");
    wz.everyoneId = (groups.find((g) => g.is_everyone) || {}).id;
    const access = d.everyone && wz.everyoneId ? [wz.everyoneId] : [];

    const sec = { name: d.section, app_ids: wz.apps.map((a) => a.id) };
    if (wz.sectionId) await api("/api/app-groups/" + wz.sectionId, { method: "PATCH", body: JSON.stringify(sec) });
    else wz.sectionId = (await api("/api/app-groups", { method: "POST", body: JSON.stringify(sec) })).id;

    const dash = { name: d.name, slug: slugify(d.name), is_default: true, app_group_ids: [wz.sectionId], group_ids: access };
    if (wz.dashId) await api("/api/dashboards/" + wz.dashId, { method: "PATCH", body: JSON.stringify(dash) });
    else wz.dashId = (await api("/api/dashboards", { method: "POST", body: JSON.stringify(dash) })).id;

    if (d.weather && d.city && !wz.siteId) {
      try {
        wz.siteId = (await api("/api/sites", { method: "POST", body: JSON.stringify({ name: d.city.name, latitude: d.city.latitude, longitude: d.city.longitude }) })).id;
      } catch (e) {
        if (!/déjà/.test(e.message)) throw e;      // site déjà présent : rien à faire
      }
    }
    // Apps déjà ajoutées (retour arrière) : on aligne leur visibilité.
    for (const a of wz.apps) {
      await api("/api/apps/" + a.id, { method: "PATCH", body: JSON.stringify({ ...a.payload, app_group_ids: [wz.sectionId], group_ids: access }) });
    }
  },
};

function appAccess() {
  return wz.dash.everyone && wz.everyoneId && wz.dashId ? [wz.everyoneId] : [];
}

const STEP_APPS = {
  title: "Applications",
  skip: "Passer cette étape",
  html: () => `
    <h1>Vos applications</h1>
    <p class="lead">Ajoutez une première tuile ${wz.sectionId ? `dans « ${esc(wz.dash.section)} »` : ""}. Cliquez sur une suggestion pour pré-remplir, ou saisissez la vôtre. Vous pouvez en ajouter plusieurs.</p>
    <div class="chips">${APP_CATALOG.map((a, k) => `<button type="button" class="chip" data-k="${k}"><img src="${esc(a.logo)}" alt="" loading="lazy">${esc(a.name)}</button>`).join("")}</div>
    <div class="row-inline" style="margin-top:.6rem">
      <div><label>Nom</label><input id="wzAppName" placeholder="Messagerie"></div>
      <div><label>Adresse (URL)</label><input id="wzAppUrl" placeholder="https://…"></div>
    </div>
    <label>Logo</label>
    <div class="row-inline">${imagePickerHtml("wzAppLogo", "", { plate: true })}<button type="button" class="btn-ghost shrink" id="wzAppAuto" style="margin-bottom:12px">✨ Logo auto</button></div>
    <label>Texte au survol (facultatif)</label><input id="wzAppTip" placeholder="À quoi sert cette application ?">
    <div style="margin-top:.9rem"><button type="button" class="btn-ghost" id="wzAppAdd">＋ Ajouter cette application</button></div>
    <div class="added-apps" id="wzAdded"></div>
    ${!wz.sectionId ? `<div class="setup-tip">ℹ️<div>Pas de dashboard créé à l'étape précédente : les applications ajoutées ici seront rangées plus tard depuis Administration › Sections.</div></div>` : ""}`,
  bind() {
    bindImagePicker("wzAppLogo");
    $$(".chip").forEach((c) => (c.onclick = () => {
      const a = APP_CATALOG[Number(c.dataset.k)];
      $("#wzAppName").value = a.name; $("#wzAppUrl").value = a.url; $("#wzAppTip").value = a.tip;
      $("#wzAppLogo").value = a.logo; $("#wzAppLogo").dispatchEvent(new Event("input"));
      if (!a.url) { $("#wzAppUrl").placeholder = "https://" + slugify(a.name) + ".votre-domaine.fr"; $("#wzAppUrl").focus(); toast("Indiquez l'adresse de votre " + a.name); }
    }));
    $("#wzAppAuto").onclick = async () => {
      const url = val("wzAppUrl");
      if (!url) { $("#wzAppUrl").focus(); return toast("Saisissez d'abord l'adresse de l'application"); }
      const b = $("#wzAppAuto"); b.textContent = "…";
      try {
        const r = await api("/api/logo?" + new URLSearchParams({ url }));
        if (r.logo) { $("#wzAppLogo").value = r.logo; $("#wzAppLogo").dispatchEvent(new Event("input")); toast(logoFoundMsg(r)); }
        else toast(r.detail || "Aucun logo trouvé");
      } catch (e) { toast(e.message); } finally { b.textContent = "✨ Logo auto"; }
    };
    $("#wzAppAdd").onclick = async () => { try { await addWizardApp(true); } catch (e) { wizErr(e.message); } };
    renderAddedApps();
  },
  async next() {
    if (val("wzAppName") || val("wzAppUrl")) await addWizardApp(true);   // saisie en cours : on ne la perd pas
  },
};

async function addWizardApp(strict) {
  const payload = { name: val("wzAppName"), url: val("wzAppUrl"), image_url: val("wzAppLogo"), tooltip: val("wzAppTip"), open_new_tab: true };
  if (!payload.name || !payload.url) { if (strict) throw new Error("Nom et adresse requis pour ajouter l'application."); return; }
  if (!/^[a-z][a-z0-9+.-]*:/i.test(payload.url)) payload.url = "https://" + payload.url;
  const body = { ...payload, app_group_ids: wz.sectionId ? [wz.sectionId] : [], group_ids: appAccess() };
  const a = await api("/api/apps", { method: "POST", body: JSON.stringify(body) });
  wz.apps.push({ id: a.id, payload, name: a.name, url: a.url, image_url: a.image_url });
  ["wzAppName", "wzAppUrl", "wzAppTip", "wzAppLogo"].forEach((id) => ($("#" + id).value = ""));
  $("#wzAppLogo").dispatchEvent(new Event("input"));
  $("#wzAppUrl").placeholder = "https://…";
  wizErr("");
  renderAddedApps();
  toast("« " + a.name + " » ajoutée");
}

function renderAddedApps() {
  const el = $("#wzAdded"); if (!el) return;
  el.innerHTML = wz.apps.map((a) => `<div class="row glass">
      ${a.image_url ? `<img class="logo" src="${esc(a.image_url)}" alt="">` : `<span class="ph">${esc(initials(a.name))}</span>`}
      <div class="grow"><div class="t">${esc(a.name)}</div><div class="s">${esc(a.url)}</div></div>
      <button type="button" class="icon-btn" data-rm="${a.id}" title="Retirer">🗑️</button></div>`).join("");
  $$("[data-rm]", el).forEach((b) => (b.onclick = async () => {
    try {
      await api("/api/apps/" + b.dataset.rm, { method: "DELETE" });
      wz.apps = wz.apps.filter((a) => a.id !== Number(b.dataset.rm));
      renderAddedApps();
    } catch (e) { toast(e.message); }
  }));
}

const STEP_DONE = {
  title: "Terminé",
  nextLabel: "Ouvrir le portail →",
  html: () => `
    <h1>C'est prêt 🎉</h1>
    <p class="lead">Votre portail <b>${esc(state.branding.portal_name || "MyApps")}</b> est configuré.</p>
    <div class="summary">
      <div>🎨 Identité et couleurs enregistrées</div>
      <div>${wz.dashId ? `🧭 Dashboard « ${esc(wz.dash.name)} » ${wz.dash.everyone ? "visible par tous" : "réservé aux administrateurs"}` : "🧭 Aucun dashboard créé pour l'instant"}</div>
      <div>${wz.apps.length ? `🧩 ${wz.apps.length} application${wz.apps.length > 1 ? "s" : ""} ajoutée${wz.apps.length > 1 ? "s" : ""}` : "🧩 Aucune application pour l'instant"}</div>
      ${wz.siteId ? `<div>🌤️ Météo : ${esc(wz.dash.city?.name || "")}</div>` : ""}
    </div>
    <h3>Pour aller plus loin</h3>
    <ul class="feature-list">
      <li>👥<div><b>Utilisateurs</b>Créez des comptes locaux et des groupes dans Administration.</div></li>
      <li>🏢<div><b>Active Directory</b>Connexion avec les comptes de l'annuaire, groupes synchronisés (Réglages).</div></li>
      <li>🔐<div><b>SSO</b>Authentik, Keycloak, Entra ID… via OIDC (Réglages).</div></li>
      <li>✏️<div><b>Mode édition</b>Bouton ✏️ du portail : glissez les tuiles pour les réordonner.</div></li>
    </ul>`,
  focus: false,
  async next() {
    await api("/api/setup/complete", { method: "POST" });
    state.setupRequired = false;
    $("#view-setup").classList.add("hidden");
    showWelcome();
    enterApp();
    setTimeout(hideWelcome, 1400);
  },
};
