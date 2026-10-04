const { JSDOM } = require("jsdom");
const BASE = process.env.BASE;
function jsdomFetch(w) {
  return (u, o = {}) => {
    if (o.body instanceof w.URLSearchParams)
      o = { ...o, body: o.body.toString(), headers: { ...(o.headers || {}), "Content-Type": "application/x-www-form-urlencoded" } };
    return fetch(new URL(u, BASE), o);
  };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, label, ms = 8000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (fn()) return; } catch {} await sleep(50); } throw new Error("Timeout : " + label); }
const check = (c, m) => { if (!c) throw new Error("ÉCHEC : " + m); console.log("  ✓ " + m); };
async function page() {
  const dom = await JSDOM.fromURL(BASE + "/", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = jsdomFetch(w);
      w.confirm = () => true; } });
  return dom.window;
}
async function login(w, u, p) {
  const $ = (s) => w.document.querySelector(s);
  await waitFor(() => !$("#view-login").classList.contains("hidden"), "page de connexion");
  $("#loginUser").value = u; $("#loginPass").value = p;
  $("#loginForm").dispatchEvent(new w.Event("submit", { cancelable: true }));
}
(async () => {
  const tok = (await (await fetch(BASE + "/api/auth/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "Tr0ub4dor&3x" }) })).json()).access_token;
  const H = { Authorization: "Bearer " + tok, "Content-Type": "application/json" };
  await fetch(BASE + "/api/users", { method: "POST", headers: H, body: JSON.stringify({ username: "bob", full_name: "Bob Durand", password: "bob-pw-1234" }) });
  await fetch(BASE + "/api/setup/restart", { method: "POST", headers: H });

  let w = await page(); let $ = (s) => w.document.querySelector(s);
  await login(w, "bob", "bob-pw-1234");
  await waitFor(() => !$("#view-app").classList.contains("hidden") && w.document.querySelectorAll("#sections .tile").length, "portail bob");
  check($("#view-setup").classList.contains("hidden"), "utilisateur simple : pas d'assistant même s'il n'est pas terminé");
  check(w.document.querySelectorAll("#sections .tile").length === 2, "bob voit les 2 tuiles (dashboard « Tout le monde »)");
  check($("#adminLink").classList.contains("hidden") && $("#editToggle").classList.contains("hidden"), "bob n'a ni administration ni mode édition");

  w = await page(); $ = (s) => w.document.querySelector(s);
  await login(w, "alice", "Tr0ub4dor&3x");
  await waitFor(() => !$("#view-setup").classList.contains("hidden"), "assistant repris");
  check($("#setupBody h1").textContent.includes("Identité"), "admin : l'assistant reprend à l'étape Identité");
  check($("#wzPortal").value === "Intranet Test", "valeurs existantes pré-remplies");
  $("#wzLater").click();
  await waitFor(() => !$("#view-app").classList.contains("hidden"), "portail après « plus tard »");
  check(true, "« Terminer plus tard » ouvre le portail");
  await fetch(BASE + "/api/setup/complete", { method: "POST", headers: H });
  process.exit(0);
})().catch((e) => { console.error(e.message); process.exit(1); });
