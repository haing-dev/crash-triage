/* Cloudflare Worker in front of the static site. Everything is served from the static assets, except:
     /private/*      owner-only data (the Fallen Biotech Screener): needs the owner cookie
     /owner/login    POST password -> sets the owner cookie (30 days)
     /owner/logout   clears it
   The password is the Worker secret OWNER_PASSWORD (set by the deploy workflow from the GitHub secret of the same
   name). The cookie holds an expiry time and an HMAC of it keyed by the password, so changing the password logs
   every browser out. */
const DAYS = 30, COOKIE = "owner";
const enc = new TextEncoder();

async function hmac(secret, msg) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("");
}
function same(a, b) {                                     // constant-time string comparison
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
function cookie(req, name) {
  const m = (req.headers.get("Cookie") || "").match(new RegExp("(?:^|;\\s*)" + name + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}
async function isOwner(req, env) {
  if (!env.OWNER_PASSWORD) return false;
  const v = cookie(req, COOKIE);
  if (!v || !v.includes(".")) return false;
  const [exp, sig] = v.split(".");
  if (!(Number(exp) > Date.now())) return false;
  return same(sig, await hmac(env.OWNER_PASSWORD, "owner:" + exp));
}
const back = (to, extra = {}) => new Response(null, { status: 303, headers: { Location: to, "Cache-Control": "no-store", ...extra } });

export default {
  async fetch(req, env) {
    const url = new URL(req.url), p = url.pathname;
    if (p === "/owner/login" && req.method === "POST") {
      const form = await req.formData().catch(() => null), pw = form && form.get("password");
      if (!env.OWNER_PASSWORD || !same(String(pw || ""), env.OWNER_PASSWORD)) {
        await new Promise(r => setTimeout(r, 800));                       // slows down guessing
        return back("/#/login?failed=1");
      }
      const exp = String(Date.now() + DAYS * 864e5);
      const v = exp + "." + await hmac(env.OWNER_PASSWORD, "owner:" + exp);
      return back("/#/slide", { "Set-Cookie": `${COOKIE}=${encodeURIComponent(v)}; Path=/; Max-Age=${DAYS * 86400}; HttpOnly; Secure; SameSite=Strict` });
    }
    if (p === "/owner/logout")
      return back("/#/crash", { "Set-Cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict` });
    if (p.startsWith("/private/")) {
      if (!(await isOwner(req, env))) return new Response("Owner login required", { status: 401, headers: { "Cache-Control": "no-store" } });
      const r = await env.ASSETS.fetch(req), h = new Headers(r.headers);
      h.set("Cache-Control", "private, no-store");
      return new Response(r.body, { status: r.status, headers: h });
    }
    return env.ASSETS.fetch(req);
  },
};
