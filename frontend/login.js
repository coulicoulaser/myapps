/* MyApps — écran de connexion public. Une fois connecté (ou au retour du SSO), le serveur pose
   un cookie d'accès et la page se recharge : « / » sert alors le portail, qui reprend le jeton. */
"use strict";

function enterPortal() { location.replace("/"); }

async function loginBoot() {
  try { applyBranding(await fetch("/api/auth/branding").then((r) => r.json())); }
  catch (e) { console.warn("MyApps : apparence non chargée, thème par défaut", e); applyBranding({}); }
  setBg(state.branding.login_background || state.branding.dashboard_background || "");

  // Retour SSO : jeton dans le fragment de l'URL (jamais envoyé au serveur), cookie posé.
  const hash = new URLSearchParams(location.hash.slice(1));
  if (hash.get("sso_token")) { saveToken(hash.get("sso_token")); return enterPortal(); }
  const params = new URLSearchParams(location.search);
  if (params.get("sso_error")) toast("Échec de la connexion SSO (" + params.get("sso_error") + ")");
  if (location.hash || location.search) history.replaceState({}, "", location.pathname);

  // Jeton gardé : /api/auth/me le vérifie et repose le cookie d'accès.
  if (state.token) {
    try {
      const r = await fetch("/api/auth/me", { headers: { Authorization: "Bearer " + state.token } });
      if (r.ok) return enterPortal();
      if (r.status === 401 || r.status === 403) { saveToken(null); state.token = null; }
    } catch (e) { console.error("MyApps : /api/auth/me injoignable", e); }
  }
  try {
    const sso = await fetch("/api/auth/sso/config").then((r) => r.json());
    if (sso.enabled) { $("#ssoBtn").classList.remove("hidden"); $("#loginSep").classList.remove("hidden"); $("#ssoName").textContent = sso.provider; }
  } catch (e) { console.warn("MyApps : configuration SSO non chargée, bouton SSO masqué", e); }
  $("#splash").classList.add("hidden");
  $("#view-login").classList.remove("hidden");
  $("#loginUser").focus();
}

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#loginForm button[type='submit']");
  btn.classList.add("loading");
  $("#loginErr").classList.add("hidden");
  const body = new URLSearchParams({ username: $("#loginUser").value, password: $("#loginPass").value, remember: $("#loginRemember").checked ? "true" : "false" });
  try {
    const r = await fetch("/api/auth/login", { method: "POST", body });
    if (r.status >= 400 && r.status < 500) throw Object.assign(new Error("refus"), { denied: true });
    if (!r.ok) throw new Error("Erreur " + r.status);
    const j = await r.json();
    saveToken(j.access_token);
    try { sessionStorage.setItem("myapps_welcome", "1"); } catch {}
    enterPortal();
  } catch (err) {
    if (!err.denied) console.error("MyApps : connexion impossible", err);
    const el = $("#loginErr");
    el.textContent = err.denied ? "Identifiants invalides." : "Connexion impossible : serveur injoignable ou réponse incomplète. Réessayez.";
    el.classList.remove("hidden");
    btn.classList.remove("loading");
  }
});
$("#ssoBtn").addEventListener("click", () => { location.href = "/api/auth/sso/login"; });

loginBoot();
