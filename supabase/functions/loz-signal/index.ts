// Lauren's feedback button, and the request box behind it.
//
// She asked for this on 5 October 2026: "is there anyway for me to interact
// with the AI? I prefer chatting to it and being able to have some control in
// pushing it to gather stories" and "this view introduces friction where I
// need to go through you."
//
// A button in the brief email lands here with her choice already made, so the
// page opens with that choice selected and one tap sends it. Notes are
// optional. Nothing is read from an inbox and nothing routes through Krish.
//
// SECURITY NOTE, stated plainly because it is a real trade-off. These links
// carry no signature. The brief is rendered in GitHub Actions, which has no
// signing secret shared with this function, and adding one needs a repository
// secret nobody in that session could create. So the endpoint instead bounds
// what a stray request can do: it writes only to lozatron.signals, only four
// known actions, a bounded note, and only for an edition id matching the
// deterministic format and dated within the retention window. The worst case
// is a junk preference row in a table nothing acts on automatically. No key
// for this project ever reaches the browser, because an anon key here would
// reach mind/make OS tables.

import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const ACTIONS: Record<string, { label: string; sent: string }> = {
  more: { label: "More like this", sent: "More like this, noted." },
  less: { label: "Not for me", sent: "Not for me, noted." },
  keep: { label: "Worth keeping", sent: "Filed as worth keeping." },
  ask: { label: "Chase this", sent: "On it. That goes into the next brief's sourcing." },
};

// 2026-10-06T09-briefing, or 2026-10-06T0906-briefing for an off-slot run.
const EDITION = /^\d{4}-\d{2}-\d{2}T\d{2,4}-(briefing|breaking)$/;
const RETAIN_DAYS = 45;

function editionAcceptable(id: string): boolean {
  if (!EDITION.test(id)) return false;
  const day = Date.parse(id.slice(0, 10) + "T00:00:00Z");
  if (Number.isNaN(day)) return false;
  const age = (Date.now() - day) / 86_400_000;
  return age >= -2 && age <= RETAIN_DAYS;
}

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
   .replace(/"/g, "&quot;");

// Typography and palette are the brief email's own, so the page reads as the
// same publication rather than a form bolted onto it.
const INK = "#14171a", PAPER = "#faf8f5", MUTED = "#6b7075", RULE = "#e4e0da";
const ANSWER = "#1f6f5c", LINK = "#1b5e8c";
const SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif";
const MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace";

function shell(inner: string, title: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title><style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:${PAPER};color:${INK};font-family:${SANS};
 font-size:17px;line-height:1.5;-webkit-text-size-adjust:100%}
.wrap{max-width:540px;margin:0 auto;padding:40px 20px 72px}
.mast{font-family:${MONO};font-size:12px;letter-spacing:.14em;
 text-transform:uppercase;color:${MUTED};margin:0 0 28px}
h1{font-size:22px;line-height:1.3;font-weight:600;margin:0 0 6px;
 letter-spacing:-.01em}
.src{font-family:${MONO};font-size:12px;color:${MUTED};margin:0 0 28px}
.q{font-family:${MONO};font-size:12px;letter-spacing:.08em;
 text-transform:uppercase;color:${MUTED};margin:0 0 10px}
fieldset{border:0;padding:0;margin:0 0 26px}
legend{padding:0}
.opts{display:grid;gap:8px}
label.opt{display:flex;align-items:center;gap:12px;min-height:52px;
 padding:0 16px;border:1px solid ${RULE};border-radius:4px;background:#fff;
 cursor:pointer;font-size:16px}
label.opt:focus-within{outline:2px solid ${LINK};outline-offset:2px}
label.opt input{width:20px;height:20px;accent-color:${ANSWER};margin:0;flex:none}
label.opt.on{border-color:${ANSWER};box-shadow:inset 0 0 0 1px ${ANSWER}}
textarea{width:100%;min-height:124px;padding:14px;border:1px solid ${RULE};
 border-radius:4px;font:inherit;background:#fff;color:${INK};resize:vertical}
textarea:focus{outline:2px solid ${LINK};outline-offset:1px}
.hint{font-size:14px;color:${MUTED};margin:8px 0 0}
button{width:100%;min-height:52px;margin:26px 0 0;border:0;border-radius:4px;
 background:${INK};color:${PAPER};font:inherit;font-weight:600;font-size:17px;
 cursor:pointer}
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

function form(edition: string, cluster: string, titleText: string,
              outlet: string, action: string): string {
  const opts = Object.entries(ACTIONS).map(([key, meta]) => `
    <label class="opt${key === action ? " on" : ""}">
      <input type="radio" name="action" value="${key}"${key === action ? " checked" : ""}>
      <span>${esc(meta.label)}</span>
    </label>`).join("");
  return shell(`
<h1>${esc(titleText || "This edition")}</h1>
<p class="src">${esc(outlet || "Lozatron")}</p>
<hr class="rule">
<form method="POST" action="">
  <input type="hidden" name="edition" value="${esc(edition)}">
  <input type="hidden" name="cluster" value="${esc(cluster)}">
  <input type="hidden" name="title" value="${esc(titleText)}">
  <fieldset>
    <legend class="q">Your call</legend>
    <div class="opts">${opts}</div>
  </fieldset>
  <label class="q" for="note">Anything to add</label>
  <textarea id="note" name="note" maxlength="4000"
    placeholder="Optional. What to chase, what to drop, what you want more of."></textarea>
  <p class="hint">Goes straight to Lozatron. Nobody else reads it first.</p>
  <button type="submit">Send</button>
</form>`, "Lozatron feedback");
}

function thanks(action: string): string {
  const meta = ACTIONS[action] ?? ACTIONS.keep;
  return shell(`
<p class="done">${esc(meta.sent)}</p>
<p class="hint">You can close this. It is already recorded.</p>`, "Sent");
}

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  const headers = { "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer" };

  if (req.method === "GET") {
    const edition = url.searchParams.get("e") ?? "";
    if (!editionAcceptable(edition)) {
      return new Response(shell(
        `<p class="done">That link has expired.</p>
         <p class="hint">Reply to the brief instead and it will reach Krish.</p>`,
        "Expired"), { status: 410, headers });
    }
    const action = url.searchParams.get("a") ?? "keep";
    return new Response(form(
      edition,
      url.searchParams.get("c") ?? "",
      url.searchParams.get("t") ?? "",
      url.searchParams.get("o") ?? "",
      action in ACTIONS ? action : "keep",
    ), { headers });
  }

  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const body = new URLSearchParams(await req.text());
  const edition = (body.get("edition") ?? "").trim();
  const action = (body.get("action") ?? "").trim();
  if (!editionAcceptable(edition) || !(action in ACTIONS)) {
    return new Response(shell(
      `<p class="done">That did not go through.</p>
       <p class="hint">Reply to the brief instead and it will reach Krish.</p>`,
      "Not sent"), { status: 400, headers });
  }

  const row = {
    edition_id: edition,
    cluster_key: (body.get("cluster") ?? "").trim() || null,
    story_title: (body.get("title") ?? "").slice(0, 500),
    action,
    note: (body.get("note") ?? "").slice(0, 4000),
    agent: (req.headers.get("user-agent") ?? "").slice(0, 300),
  };

  // Through an RPC in `public`, not a table insert. The lozatron schema is
  // deliberately not exposed over the API, and widening the project's exposed
  // schema list for one insert would put a private schema on a public surface.
  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/loz_record_signal`, {
    method: "POST",
    headers: {
      apikey: SERVICE_KEY,
      Authorization: `Bearer ${SERVICE_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      p_edition_id: row.edition_id,
      p_action: row.action,
      p_cluster_key: row.cluster_key,
      p_story_title: row.story_title,
      p_note: row.note,
      p_agent: row.agent,
    }),
  });

  if (!res.ok) {
    // Her input must never vanish silently. Echo it back so it can be
    // copied, and say what happened.
    const note = esc(row.note);
    return new Response(shell(
      `<p class="done">Lozatron could not file that just now.</p>
       <p class="hint">Your words are below so they are not lost. Sending this
       to Krish will get it in.</p><hr class="rule">
       <textarea readonly>${note}</textarea>`,
      "Not filed"), { status: 502, headers });
  }

  return new Response(thanks(action), { headers });
});
