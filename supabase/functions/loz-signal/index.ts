// Lauren's feedback button.
//
// She asked for this on 5 October 2026: "is there anyway for me to interact
// with the AI? I prefer chatting to it and being able to have some control in
// pushing it to gather stories" and "this view introduces friction where I
// need to go through you."
//
// WHY THIS IS PLAIN TEXT, AND NOT A STYLED PAGE.
//
// This endpoint used to render a designed HTML page with a note box. It
// reached her phone as a wall of raw source code. The cause is not in this
// file: the Supabase gateway serves every response from this project as
// `content-type: text/plain` with `x-content-type-options: nosniff` and
// `content-security-policy: default-src 'none'; sandbox`, whatever the
// function sets. Verified on 6 October against both Edge Functions and
// Storage, so it is a project-wide policy and not a function bug. A browser
// given text/plain prints the markup instead of rendering it.
//
// So this stops fighting the platform. The response is written as plain prose
// that reads correctly *because* it is served as text. One tap records her
// verdict and she gets a sentence back.
//
// The note box needs a host that will serve HTML. That page is written and
// committed at web/api/index.js, ready to deploy; LOZ_SIGNAL_URL in the
// renderer repoints the email's buttons at it in one variable. Until then the
// verdict buttons work and the notes channel does not.
//
// If this ever regresses, the symptom is a wall of source code on a phone,
// and the smoke test is `curl -D -` reading the content-type, never the
// status code alone.
//
// WHY A GET WRITES.
//
// Normally a GET should not change state. Here it must: without HTML there is
// no form to POST, so the tap itself has to be the write. The cost is that a
// link prescanner could file a phantom row. That is bounded on purpose, and
// it is the same bound the security note below already relies on: the only
// thing reachable is one inert preference row in a table nothing acts on
// automatically. A form she cannot use would be worse. POST still works, for
// the hosted page once it exists.
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

// `sent` is what she reads after tapping. Written as a sentence a person
// would say, because it is the only thing she sees.
const ACTIONS: Record<string, { label: string; sent: string }> = {
  more: { label: "More like this", sent: "Noted: you want more like this." },
  less: { label: "Not for me", sent: "Noted: not for you." },
  keep: { label: "Worth keeping", sent: "Filed as worth keeping." },
  ask: {
    label: "Chase this",
    sent: "On it. That goes into the next brief's sourcing.",
  },
};

// MIRRORS core.EDITION_ID_RE. Keep the two in step.
//
// Two forms, because app.edition_id emits two. Slot runs produce
// `2026-10-06T09-briefing`; any run without a slot, which is every forced or
// manual run, produces `2026-10-06T163621Z-briefing`. This accepted only the
// first, so the buttons on a manually sent edition were dead on arrival.
const EDITION = /^\d{4}-\d{2}-\d{2}T(?:\d{2,4}|\d{6}Z)-(briefing|breaking)$/;
const RETAIN_DAYS = 45;

function editionAcceptable(id: string): boolean {
  if (!EDITION.test(id)) return false;
  const day = Date.parse(id.slice(0, 10) + "T00:00:00Z");
  if (Number.isNaN(day)) return false;
  const age = (Date.now() - day) / 86_400_000;
  return age >= -2 && age <= RETAIN_DAYS;
}

// Served as text/plain whatever we ask for, so the body has to read as text.
// No markup, no entities: a `&` is a `&`.
const TEXT = {
  "Content-Type": "text/plain; charset=utf-8",
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
};

// One blank line under the masthead, the sentence, then what to do next.
// Reads as a note rather than a receipt.
function page(body: string): string {
  return `LOZATRON\n\n${body}\n`;
}

const EXPIRED = page(
  "That link has expired.\n\n" +
    "Replying to the brief still reaches Krish.",
);

const FAILED = page(
  "Lozatron could not file that just now.\n\n" +
    "Nothing was recorded, so it is worth sending again. If it fails twice,\n" +
    "replying to the brief reaches Krish and he will get it in.",
);

async function record(row: {
  edition_id: string;
  cluster_key: string | null;
  story_title: string;
  action: string;
  note: string;
  agent: string;
}): Promise<boolean> {
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
  return res.ok;
}

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  const agent = (req.headers.get("user-agent") ?? "").slice(0, 300);

  // The tap itself. One GET, one recorded verdict, one sentence back.
  if (req.method === "GET") {
    const edition = url.searchParams.get("e") ?? "";
    const action = url.searchParams.get("a") ?? "";
    if (!editionAcceptable(edition)) {
      return new Response(EXPIRED, { status: 410, headers: TEXT });
    }
    if (!(action in ACTIONS)) {
      return new Response(EXPIRED, { status: 400, headers: TEXT });
    }

    const ok = await record({
      edition_id: edition,
      cluster_key: (url.searchParams.get("c") ?? "").trim() || null,
      story_title: (url.searchParams.get("t") ?? "").slice(0, 500),
      action,
      note: "",
      agent,
    });
    if (!ok) return new Response(FAILED, { status: 502, headers: TEXT });

    const title = (url.searchParams.get("t") ?? "").trim();
    const subject = title ? `\n\nOn: ${title}` : "";
    return new Response(
      page(
        `${ACTIONS[action].sent}${subject}\n\n` +
          "You can close this. Nothing else is needed.\n\n" +
          "To say more than a tap, reply to the brief and it reaches Krish.",
      ),
      { headers: TEXT },
    );
  }

  // Kept for the hosted note page at web/api/index.js, which forwards here
  // server to server so the service key never leaves this project.
  if (req.method !== "POST") {
    return new Response(page("Method not allowed."), {
      status: 405,
      headers: TEXT,
    });
  }

  const body = new URLSearchParams(await req.text());
  const edition = (body.get("edition") ?? "").trim();
  const action = (body.get("action") ?? "").trim();
  if (!editionAcceptable(edition) || !(action in ACTIONS)) {
    return new Response(page("That did not go through."), {
      status: 400,
      headers: TEXT,
    });
  }

  const note = (body.get("note") ?? "").slice(0, 4000);
  const ok = await record({
    edition_id: edition,
    cluster_key: (body.get("cluster") ?? "").trim() || null,
    story_title: (body.get("title") ?? "").slice(0, 500),
    action,
    note,
    agent,
  });
  if (!ok) return new Response(FAILED, { status: 502, headers: TEXT });

  return new Response(page(ACTIONS[action].sent), { headers: TEXT });
});
