// Navigateur simulé partagé par les parcours e2e : un seul stock de cookies (comme un vrai
// navigateur) pour jsdom (pages, scripts) ET pour fetch. Le portail n'est servi qu'avec le cookie
// d'accès posé à la connexion ; jsdom ne sait pas « naviguer » (location.replace) : on rouvre la
// page avec les mêmes cookies et le stockage voulu.
const { JSDOM, CookieJar } = require("jsdom");
const BASE = process.env.BASE;
const jar = new CookieJar();

function jsdomFetch(w) {
  return async (u, o = {}) => {
    if (o.body instanceof w.URLSearchParams)
      o = { ...o, body: o.body.toString(), headers: { ...(o.headers || {}), "Content-Type": "application/x-www-form-urlencoded" } };
    const url = new URL(u, BASE).toString();
    const headers = { ...(o.headers || {}) };
    const cookie = jar.getCookieStringSync(url);
    if (cookie) headers.Cookie = cookie;
    const r = await fetch(url, { ...o, headers });
    for (const c of r.headers.getSetCookie?.() || []) jar.setCookieSync(c, url);
    return r;
  };
}

async function open(path = "/", { storage = {}, beforeParse } = {}) {
  return JSDOM.fromURL(BASE + path, {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, cookieJar: jar,
    beforeParse(w) {
      w.fetch = jsdomFetch(w);
      for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
      if (beforeParse) beforeParse(w);
    },
  });
}

module.exports = { BASE, jar, jsdomFetch, open };
