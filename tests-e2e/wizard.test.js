// Parcours complet de l'assistant dans un DOM simulé (jsdom), contre un vrai serveur.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = process.env.BASE, CODE = fs.readFileSync(process.env.CODE_FILE, "utf8").trim();

function jsdomFetch(w) {
  return (u, o = {}) => {
    if (o.body instanceof w.URLSearchParams)
      o = { ...o, body: o.body.toString(), headers: { ...(o.headers || {}), "Content-Type": "application/x-www-form-urlencoded" } };
    return fetch(new URL(u, BASE), o);
  };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, label, ms = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch {} await sleep(50); }
  throw new Error("Timeout : " + label);
}
function check(cond, msg) { if (!cond) throw new Error("ÉCHEC : " + msg); console.log("  ✓ " + msg); }

(async () => {
  const errors = [];
  const dom = await JSDOM.fromURL(BASE + "/", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = jsdomFetch(w);
      w.confirm = () => true;
      w.open = () => null;
      w.addEventListener("error", (e) => errors.push(e.message));
    },
  });
  const w = dom.window, d = w.document, $ = (s) => d.querySelector(s);
  const h1 = () => $("#setupBody h1")?.textContent || "";
  const setVal = (sel, v) => { const el = $(sel); el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); el.dispatchEvent(new w.Event("change", { bubbles: true })); };
  const next = async (expectH1) => {
    $("#wzNext").click();
    await waitFor(() => h1().includes(expectH1) || ($("#wzErr") && !$("#wzErr").classList.contains("hidden") && $("#wzErr").textContent), "étape " + expectH1);
    const err = $("#wzErr");
    if (err && !err.classList.contains("hidden") && err.textContent && !h1().includes(expectH1)) throw new Error("Erreur assistant : " + err.textContent);
  };

  await waitFor(() => !$("#view-setup").classList.contains("hidden"), "assistant affiché");
  check(h1().includes("Bienvenue"), "assistant ouvert sur Bienvenue");

  setVal("#wzCode", "FAUX-CODE");
  $("#wzNext").click();
  await waitFor(() => !$("#wzErr").classList.contains("hidden"), "erreur code");
  check(/incorrect/i.test($("#wzErr").textContent), "mauvais code refusé : " + $("#wzErr").textContent);

  setVal("#wzCode", CODE.toLowerCase());
  await next("Compte administrateur");
  check(true, "code accepté (minuscules)");

  setVal("#wzUser", "alice"); setVal("#wzName", "Alice Martin"); setVal("#wzPw", "Tr0ub4dor&3x"); setVal("#wzPw2", "autre");
  $("#wzNext").click();
  await waitFor(() => !$("#wzErr").classList.contains("hidden"), "erreur mdp");
  check(/correspondent/.test($("#wzErr").textContent), "confirmation différente refusée");
  setVal("#wzPw2", "Tr0ub4dor&3x");
  await next("Identité");
  check(!!w.localStorage.getItem("token"), "admin créé, jeton stocké");
  check(!$("#wzBack"), "pas de retour possible vers le compte admin");

  setVal("#wzPortal", "Intranet Test");
  $('#wzAccent .swatch[data-c="#10b981"]').click();
  setVal("#wzSearch", "duckduckgo");
  check($(".preview-bar .word").textContent === "Intranet Test", "aperçu du nom en direct");
  check(w.getComputedStyle(d.documentElement).getPropertyValue("--accent").trim() === "#10b981", "couleur d'accent appliquée en direct");
  await next("Fonds d'écran");

  $('#wzBgDash .bg-opt[data-url="/backgrounds/ocean.svg"]').click();
  await next("Premier dashboard");

  setVal("#wzCityQ", "Lyon"); $("#wzCityGo").click();
  await waitFor(() => d.querySelectorAll("#wzCityRes button").length, "résultats ville", 10000);
  d.querySelector("#wzCityRes button").click();
  check(/Lyon/.test($("#wzCityPicked").textContent), "ville retenue");
  await next("Vos applications");

  d.querySelector('.chip[data-k="1"]').click();
  check($("#wzAppName").value === "Outlook" && $("#wzAppUrl").value.startsWith("https://"), "suggestion Outlook pré-remplie");
  $("#wzAppAdd").click();
  await waitFor(() => d.querySelectorAll("#wzAdded .row").length === 1, "app ajoutée");
  check(true, "Outlook ajoutée");
  setVal("#wzAppName", "Wiki"); setVal("#wzAppUrl", "wiki.exemple.local");
  await next("C'est prêt");
  check(/2 applications ajoutées/.test($("#setupBody").textContent), "récapitulatif : 2 applications (saisie en cours conservée)");
  check(/Lyon/.test($("#setupBody").textContent), "récapitulatif : météo Lyon");

  $("#wzNext").click();
  await waitFor(() => !$("#view-app").classList.contains("hidden") && d.querySelectorAll("#sections .tile").length, "portail affiché");
  const tiles = [...d.querySelectorAll("#sections .tile")].map((t) => t.textContent.trim());
  check(tiles.length === 2 && tiles.some((t) => t.includes("Wiki")), "portail : 2 tuiles (" + tiles.join(", ") + ")");
  check($("#dashTitle").textContent === "Accueil", "dashboard « Accueil » ouvert");
  check(d.title === "Intranet Test", "titre de l'onglet = nom du portail");
  check($("#sections .tile[href^='https://wiki.exemple.local']") !== null, "https:// ajouté à l'adresse saisie sans schéma");
  check(!$("#weather").classList.contains("hidden"), "widget météo visible");
  check($("#searchInput").placeholder.includes("DuckDuckGo"), "recherche web DuckDuckGo");

  const st = await (await fetch(BASE + "/api/setup/status")).json();
  check(st.required === false && st.admin_exists === true, "assistant marqué terminé côté serveur");

  // Rechargement : plus d'assistant, portail direct (jeton conservé)
  const dom2 = await JSDOM.fromURL(BASE + "/", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w2) { w2.fetch = jsdomFetch(w2); w2.localStorage.setItem("token", w.localStorage.getItem("token")); } });
  await waitFor(() => !dom2.window.document.querySelector("#view-app").classList.contains("hidden"), "portail au rechargement");
  check(true, "rechargement : portail direct, pas d'assistant");

  // Administration : chaque onglet s'affiche sans erreur
  for (const tab of ["apps", "appgroups", "dashboards", "groups", "users", "sites", "settings", "updates", "changelog"]) {
    $("#adminLink").click();
    d.querySelector(`#adminNav a[data-tab="${tab}"]`).click();
    await waitFor(() => $("#adminMain h1") && !/Erreur/.test($("#adminMain").textContent.slice(0, 80)), "onglet " + tab);
  }
  check(true, "9 onglets d'administration rendus");
  // Régression 1.3.2 : l'animation d'entrée se jouait sur l'onglet précédent, encore affiché.
  // On laisse finir l'entrée de l'onglet affiché (900 ms) pour ne tester que l'effet du clic.
  await waitFor(() => !$("#adminMain").classList.contains("enter"), "fin de l'animation de l'onglet affiché", 3000);
  d.querySelector('#adminNav a[data-tab="users"]').click();
  check(!$("#adminMain").classList.contains("enter"), "pas d'animation d'entrée sur l'ancien onglet pendant le chargement");
  await waitFor(() => /Utilisateurs/.test($("#adminMain h1")?.textContent || "") && $("#adminMain").classList.contains("enter"), "animation d'entrée sur le nouvel onglet");
  check(true, "animation d'entrée jouée sur le nouvel onglet une fois affiché");
  // Mouvement : encre au clic, fermeture animée de la boîte de dialogue
  const tile = d.querySelector("#sections .tile");
  if (tile) {
    tile.dispatchEvent(new w.MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 5, clientY: 5 }));
    check(tile.querySelector(":scope > .ink > span"), "encre créée au clic sur une tuile");
  }
  $("#adminLink").click();
  d.querySelector('#adminNav a[data-tab="apps"]').click();
  await waitFor(() => $("#addApp"), "onglet applications");
  $("#addApp").click();
  check(!$("#modal").classList.contains("hidden"), "boîte de dialogue ouverte");
  $("#mCancel").click();
  check($("#modal").classList.contains("closing"), "fermeture animée en cours");
  await waitFor(() => $("#modal").classList.contains("hidden"), "boîte de dialogue fermée");

  // Mises à jour : vérification réelle contre GitHub, l'état s'affiche (à jour, disponible ou erreur)
  $("#adminLink").click();
  d.querySelector('#adminNav a[data-tab="updates"]').click();
  await waitFor(() => $("#updCheck"), "onglet mises à jour");
  $("#updCheck").click();
  await waitFor(() => /dernière vérification : (?!jamais)/.test($("#adminMain").textContent), "vérification effectuée", 20000);
  check(d.querySelector('input[name=updMode][value=notify]').checked, "mises à jour : notification seule par défaut (" + $(".upd-state b").textContent + ")");
  check(errors.length === 0, "aucune erreur JavaScript" + (errors.length ? " : " + errors.join(" | ") : ""));
  process.exit(0);
})().catch((e) => { console.error(e.message); process.exit(1); });
