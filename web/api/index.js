// Lauren's feedback page.
//
// Lives on Vercel rather than beside the data on Supabase for one measured
// reason: Supabase Edge Functions return `content-type: text/plain` with
// `x-content-type-options: nosniff` on this project no matter what the
// function sets, so a browser printed the raw HTML as source. Verified twice,
// including with an explicit `Headers` instance. Krish's words on seeing it
// were "awful and ridiculous", and he was right.
//
// The write still happens on Supabase. This page forwards the submission to
// the edge function server to server, so the service-role key stays in one
// place and never comes near a browser or a second platform. An anon key on
// that project reaches mind/make OS tables, which is why that rule is strict.

const SIGNAL_API = process.env.LOZ_SIGNAL_API
  || "https://gojpffsrxybbpbdzzrvs.supabase.co/functions/v1/loz-signal";

const ACTIONS = {
  more: { label: "More like this", sent: "More like this, noted." },
  less: { label: "Not for me", sent: "Not for me, noted." },
  keep: { label: "Worth keeping", sent: "Filed as worth keeping." },
  ask: { label: "Chase this", sent: "On it. That goes into the next brief's sourcing." },
};

// MIRRORS core.EDITION_ID_RE and the SQL check in loz_record_signal.
const EDITION = /^\d{4}-\d{2}-\d{2}T(?:\d{2,4}|\d{6}Z)-(briefing|breaking)$/;
const RETAIN_DAYS = 45;

function editionOk(id) {
  if (!EDITION.test(id || "")) return false;
  const day = Date.parse(id.slice(0, 10) + "T00:00:00Z");
  if (Number.isNaN(day)) return false;
  const age = (Date.now() - day) / 86400000;
  return age >= -2 && age <= RETAIN_DAYS;
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

// The brief email's own palette and type, so this reads as the same
// publication rather than a form bolted onto it.
const INK = "#14171a", PAPER = "#faf8f5", MUTED = "#6b7075", RULE = "#e4e0da";
const ANSWER = "#1f6f5c", LINK = "#1b5e8c";
const SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif";
const MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace";

function shell(inner, title) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title><style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;background:${PAPER};color:${INK};font-family:${SANS};
 font-size:17px;line-height:1.5;-webkit-text-size-adjust:100%}
.wrap{max-width:540px;margin:0 auto;padding:40px 20px 72px}
.mast{font-family:${MONO};font-size:12px;letter-spacing:.14em;
 text-transform:uppercase;color:${MUTED};margin:0 0 28px}
h1{font-size:22px;line-height:1.3;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.src{font-family:${MONO};font-size:12px;color:${MUTED};margin:0 0 28px}
.q{display:block;font-family:${MONO};font-size:12px;letter-spacing:.08em;
 text-transform:uppercase;color:${MUTED};margin:0 0 10px}
fieldset{border:0;padding:0;margin:0 0 26px}
legend{padding:0}
.opts{display:grid;gap:8px}
label.opt{display:flex;align-items:center;gap:12px;min-height:52px;padding:0 16px;
 border:1px solid ${RULE};border-radius:4px;background:#fff;cursor:pointer;font-size:16px}
label.opt:focus-within{outline:2px solid ${LINK};outline-offset:2px}
label.opt input{width:20px;height:20px;accent-color:${ANSWER};margin:0;flex:none}
label.opt.on{border-color:${ANSWER};box-shadow:inset 0 0 0 1px ${ANSWER}}
textarea{width:100%;min-height:124px;padding:14px;border:1px solid ${RULE};
 border-radius:4px;font:inherit;background:#fff;color:${INK};resize:vertical}
textarea:focus{outline:2px solid ${LINK};outline-offset:1px}
.hint{font-size:14px;color:${MUTED};margin:8px 0 0}
button{width:100%;min-height:52px;margin:26px 0 0;border:0;border-radius:4px;
 background:${INK};color:${PAPER};font:inherit;font-weight:600;font-size:17px;cursor:pointer}
button:focus-visible{outline:2px solid ${LINK};outline-offset:3px}
.done{font-size:19px;line-height:1.45;margin:0 0 10px}
.rule{height:1px;background:${RULE};border:0;margin:0 0 28px}
a{color:${LINK}}
@media (prefers-color-scheme:dark){
 body{background:#14171a;color:#f3f1ee}
 label.opt,textarea{background:#1c2024;border-color:#2e3338;color:#f3f1ee}
 button{background:#f3f1ee;color:#14171a}
 .rule{background:#2e3338}
}
</style></head><body><div class="wrap">
<p class="mast">Lozatron</p>${inner}</div></body></html>`;
}

function form(edition, cluster, title, outlet, action) {
  const opts = Object.keys(ACTIONS).map((key) => `
    <label class="opt${key === action ? " on" : ""}">
      <input type="radio" name="action" value="${key}"${key === action ? " checked" : ""}>
      <span>${esc(ACTIONS[key].label)}</span>
    </label>`).join("");

  // The chase button carries no story, so the page says what it is for
  // instead of showing an empty headline.
  const heading = title
    ? `<h1>${esc(title)}</h1><p class="src">${esc(outlet || "Lozatron")}</p>`
    : `<h1>Tell Lozatron what to chase</h1><p class="src">Today's brief</p>`;

  return shell(`
${heading}
<hr class="rule">
<form method="POST" action="">
  <input type="hidden" name="edition" value="${esc(edition)}">
  <input type="hidden" name="cluster" value="${esc(cluster)}">
  <input type="hidden" name="title" value="${esc(title)}">
  <fieldset>
    <legend class="q">Your call</legend>
    <div class="opts">${opts}</div>
  </fieldset>
  <label class="q" for="note">Anything to add</label>
  <textarea id="note" name="note" maxlength="4000"
    placeholder="Optional. What to chase, what to drop, what you want more of."></textarea>
  <p class="hint">Goes straight to Lozatron. Nobody else reads it first.</p>
  <button type="submit">Send</button>
</form>`, title ? "Lozatron feedback" : "Tell Lozatron what to chase");
}

function send(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => { data += chunk; if (data.length > 2e5) req.destroy(); });
    req.on("end", () => resolve(data));
    req.on("error", () => resolve(""));
  });
}

module.exports = async (req, res) => {
  const url = new URL(req.url, `https://${req.headers.host}`);

  if (req.method === "GET") {
    const edition = url.searchParams.get("e") || "";
    if (!editionOk(edition)) {
      return send(res, 410, shell(
        `<p class="done">That link has expired.</p>
         <p class="hint">Reply to the brief instead and it will reach Krish.</p>`, "Expired"));
    }
    const action = url.searchParams.get("a") || "keep";
    return send(res, 200, form(
      edition,
      url.searchParams.get("c") || "",
      url.searchParams.get("t") || "",
      url.searchParams.get("o") || "",
      ACTIONS[action] ? action : "keep",
    ));
  }

  if (req.method !== "POST") {
    return send(res, 405, shell(`<p class="done">Method not allowed.</p>`, "Not allowed"));
  }

  const body = new URLSearchParams(await readBody(req));
  const edition = (body.get("edition") || "").trim();
  const action = (body.get("action") || "").trim();
  const note = (body.get("note") || "").slice(0, 4000);
  if (!editionOk(edition) || !ACTIONS[action]) {
    return send(res, 400, shell(
      `<p class="done">That did not go through.</p>
       <p class="hint">Reply to the brief instead and it will reach Krish.</p>`, "Not sent"));
  }

  let ok = false;
  try {
    const upstream = await fetch(SIGNAL_API, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        edition, action,
        cluster: body.get("cluster") || "",
        title: (body.get("title") || "").slice(0, 500),
        note,
      }).toString(),
    });
    ok = upstream.ok;
  } catch (_) {
    ok = false;
  }

  if (!ok) {
    // Her input must never vanish silently. Echo it back so it can be copied.
    return send(res, 502, shell(
      `<p class="done">Lozatron could not file that just now.</p>
       <p class="hint">Your words are below so they are not lost. Sending this to
       Krish will get it in.</p><hr class="rule">
       <textarea readonly>${esc(note)}</textarea>`, "Not filed"));
  }

  return send(res, 200, shell(
    `<p class="done">${esc(ACTIONS[action].sent)}</p>
     <p class="hint">You can close this. It is already recorded.</p>`, "Sent"));
};
