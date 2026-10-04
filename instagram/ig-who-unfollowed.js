/* =============================================================================
 * ig-who-unfollowed.js — chi ti ha tolto il follow. Sola lettura.
 * =============================================================================
 *
 * QUESTO SCRIPT NON PUÒ MODIFICARE IL TUO ACCOUNT.
 * Non per configurazione, ma per costruzione: non contiene una singola
 * richiesta di scrittura. Cerca "method:" nel file — non lo trovi. L'unica
 * funzione di rete, igFetch(), fa solo GET verso www.instagram.com, le stesse
 * che fa l'app quando apri la lista "Following". Rischio di action block: nullo.
 *
 * USO
 *   1. Apri https://www.instagram.com/ (loggato) e la console (Cmd+Opt+J).
 *   2. Incolla tutto questo file, Invio.
 *   3. IU.scan()
 *
 * COMANDI
 *   IU.scan()        analizza e salva uno snapshot
 *   IU.diff()        ⭐ chi ti ha tolto il follow dall'ultimo scan
 *   IU.report()      chi non ti segue (fotografia attuale)
 *   IU.html()        report HTML cliccabile da aprire e lavorare
 *   IU.csv()         export CSV
 *   IU.json()        export JSON completo
 *   IU.open(n)       apre n profili in tab, per agire a mano
 *   IU.keep(...u)    "questi li tengo comunque" — spariscono dai report
 *   IU.unkeep(...u)  rimuove dalla lista "tengo"
 *   IU.keeplist()    mostra la lista "tengo"
 *   IU.history()     snapshot salvati
 *   IU.stop()        interrompe uno scan in corso
 *   IU.reset()       cancella tutto lo stato salvato
 *
 * CADENZA — diff() confronta con lo scan precedente. Uno scan a settimana è
 * più che sufficiente: più spesso non aggiunge informazione, aggiunge solo
 * richieste. Il primo scan non produce diff, serve come riferimento.
 * ========================================================================== */

(() => {
  "use strict";

  /* ========================================================================
   * CONFIG
   * ====================================================================== */
  const CONFIG = {
    PAGE_SIZE: 50,
    DELAY_MIN: 1_500,
    DELAY_MAX: 3_000,
    LONG_BREAK_EVERY: 15,
    LONG_BREAK_MIN: 20_000,
    LONG_BREAK_MAX: 35_000,
    MAX_RETRIES: 4,
    BACKOFF_BASE: 4_000,
    REQUEST_TIMEOUT: 30_000,
    MAX_HISTORY: 8, // snapshot conservati
  };

  const ORIGIN = "https://www.instagram.com";
  const APP_ID = "936619743392459";
  const K_SNAPS = "__iu_snaps_v2";
  const K_KEEP = "__iu_keep_v2";
  const K_CK = "__iu_ck_v2";

  const FATAL_SIGNALS = [
    "feedback_required", "challenge_required", "checkpoint_required",
    "login_required", "user_has_logged_out", "rate_limit_error",
  ];

  /* ========================================================================
   * UTILITY
   * ====================================================================== */
  let ABORT = false;
  class AbortError extends Error {}
  class FatalError extends Error {}

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => Math.floor(Math.random() * (b - a + 1)) + a;
  const ts = () => new Date().toLocaleTimeString("it-IT");
  const log = (...a) => console.log(`%c[IU ${ts()}]`, "color:#4ea1ff", ...a);
  const warn = (...a) => console.warn(`[IU ${ts()}]`, ...a);
  const err = (...a) => console.error(`[IU ${ts()}]`, ...a);
  const fmtDate = (iso) => new Date(iso).toLocaleString("it-IT");

  async function waitFor(ms, label) {
    const until = Date.now() + ms;
    if (ms >= 10_000) ui.status(`${label} — ${Math.round(ms / 1000)}s`);
    while (Date.now() < until) {
      if (ABORT) throw new AbortError("interrotto");
      await sleep(Math.min(400, until - Date.now()));
    }
  }

  function getCookie(name) {
    const p = `; ${document.cookie}`.split(`; ${name}=`);
    return p.length === 2 ? p.pop().split(";").shift() : undefined;
  }

  const store = {
    get(k, d = null) { try { const r = localStorage.getItem(k); return r ? JSON.parse(r) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch {} },
  };

  const norm = (u) => String(u || "").toLowerCase().replace(/^@/, "").trim();

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  /* ========================================================================
   * RETE — solo GET. Nessun metodo, nessun body, nessun csrftoken.
   * ====================================================================== */
  async function igFetch(path) {
    const url = ORIGIN + path;

    for (let attempt = 0; attempt <= CONFIG.MAX_RETRIES; attempt++) {
      if (ABORT) throw new AbortError("interrotto");

      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), CONFIG.REQUEST_TIMEOUT);
      let res;
      try {
        res = await fetch(url, {
          credentials: "include",
          signal: ctrl.signal,
          headers: { "X-IG-App-ID": APP_ID, "X-Requested-With": "XMLHttpRequest" },
        });
      } catch (e) {
        clearTimeout(timer);
        if (ABORT) throw new AbortError("interrotto");
        if (attempt === CONFIG.MAX_RETRIES) throw new FatalError(`rete: ${e.message}`);
        const w = CONFIG.BACKOFF_BASE * 2 ** attempt + rand(0, 2000);
        warn(`errore di rete, ritento fra ${Math.round(w / 1000)}s`);
        await waitFor(w, "backoff");
        continue;
      }
      clearTimeout(timer);

      const body = await res.text();
      const lower = body.slice(0, 2000).toLowerCase();

      const sig = FATAL_SIGNALS.find((s) => lower.includes(s));
      if (sig)
        throw new FatalError(
          `Instagram ha risposto "${sig}". Mi fermo subito. ` +
          `Aspetta qualche ora, usa l'app normalmente, poi riprova.`
        );

      if (res.status === 401 || res.status === 403)
        throw new FatalError(`HTTP ${res.status}: sessione scaduta. Ricarica e rifai login.`);

      if (res.status === 429 || res.status >= 500) {
        if (attempt === CONFIG.MAX_RETRIES)
          throw new FatalError(`HTTP ${res.status} persistente. Rate limited: fermati per qualche ora.`);
        const ra = parseInt(res.headers.get("retry-after") || "", 10);
        const w = Number.isFinite(ra)
          ? ra * 1000 + rand(1000, 5000)
          : CONFIG.BACKOFF_BASE * 2 ** attempt + rand(0, 3000);
        warn(`HTTP ${res.status}, attendo ${Math.round(w / 1000)}s`);
        await waitFor(w, `HTTP ${res.status}`);
        continue;
      }

      if (!res.ok) throw new FatalError(`HTTP ${res.status} inatteso`);

      try { return JSON.parse(body); }
      catch {
        throw new FatalError(
          "risposta non-JSON: di solito è una pagina di verifica. Apri instagram.com e controlla."
        );
      }
    }
    throw new FatalError("tentativi esauriti");
  }

  /* ========================================================================
   * SCAN
   * ====================================================================== */
  async function fetchList(userId, kind, onProgress) {
    const ckKey = `${K_CK}_${kind}`;
    const ck = store.get(ckKey, null);
    let users = ck?.users || [];
    let cursor = ck?.cursor || null;
    let page = 0;

    if (users.length) log(`${kind}: riprendo da ${users.length}`);

    while (true) {
      if (ABORT) throw new AbortError("interrotto");

      const qs = new URLSearchParams({ count: String(CONFIG.PAGE_SIZE) });
      if (cursor) qs.set("max_id", cursor);

      const data = await igFetch(`/api/v1/friendships/${userId}/${kind}/?${qs}`);
      const batch = Array.isArray(data.users) ? data.users : [];

      for (const u of batch) {
        users.push({
          id: String(u.pk ?? u.pk_id ?? u.id ?? ""),
          username: u.username,
          full_name: u.full_name || "",
          is_private: !!u.is_private,
          is_verified: !!u.is_verified,
        });
      }

      cursor = data.next_max_id ? String(data.next_max_id) : null;
      page++;
      onProgress?.(kind, users.length);
      store.set(ckKey, { users, cursor });

      if (!cursor || batch.length === 0) break;

      await waitFor(
        page % CONFIG.LONG_BREAK_EVERY === 0
          ? rand(CONFIG.LONG_BREAK_MIN, CONFIG.LONG_BREAK_MAX)
          : rand(CONFIG.DELAY_MIN, CONFIG.DELAY_MAX),
        `${kind}: pausa`
      );
    }

    store.del(ckKey);
    return users;
  }

  /** Salva lo snapshot in forma compatta, per non saturare localStorage. */
  function pushSnapshot(following, followers) {
    const compact = (list) => list.map((u) => [u.id, u.username]);
    const snaps = store.get(K_SNAPS, []);
    snaps.push({ t: new Date().toISOString(), fg: compact(following), fr: compact(followers) });

    while (snaps.length > CONFIG.MAX_HISTORY) snaps.shift();
    // Se la quota esplode, sacrifica gli snapshot più vecchi.
    while (snaps.length > 1 && !store.set(K_SNAPS, snaps)) snaps.shift();
    if (snaps.length === 1) store.set(K_SNAPS, snaps);
    return snaps;
  }

  let LAST = null; // ultimo risultato completo, in memoria

  async function scan() {
    ABORT = false;
    const userId = getCookie("ds_user_id");
    if (!userId) return err("cookie ds_user_id assente. Sei loggato su instagram.com?");

    ui.show();
    ui.status("scan — sola lettura");
    const t0 = Date.now();

    try {
      const onProgress = (kind, n) => ui.status(`${kind}: ${n}`);

      // L'API attuale non espone follows_viewer sulla lista following:
      // servono entrambe le liste e una differenza insiemistica.
      const following = await fetchList(userId, "following", onProgress);
      await waitFor(rand(3000, 6000), "");
      const followers = await fetchList(userId, "followers", onProgress);

      const frIds = new Set(followers.map((u) => u.id));
      const fgIds = new Set(following.map((u) => u.id));

      const snaps = pushSnapshot(following, followers);

      LAST = {
        scannedAt: new Date().toISOString(),
        counts: { following: following.length, followers: followers.length },
        notFollowingBack: following.filter((u) => !frIds.has(u.id)),
        youDontFollowBack: followers.filter((u) => !fgIds.has(u.id)),
        mutual: following.filter((u) => frIds.has(u.id)),
      };

      ui.status(`fatto in ${Math.round((Date.now() - t0) / 1000)}s`);
      log(`Scan completato. Snapshot salvati: ${snaps.length}/${CONFIG.MAX_HISTORY}`);

      report();
      if (snaps.length >= 2) { console.log(""); diff(); }
      else log("Primo snapshot: al prossimo scan IU.diff() ti dirà chi ti ha tolto il follow.");

      return LAST;
    } catch (e) {
      handleError(e);
    }
  }

  /* ========================================================================
   * DIFF — la funzione che risponde davvero a "chi mi ha unfollowato"
   * ====================================================================== */
  function diff(olderIndex = -2, newerIndex = -1) {
    const snaps = store.get(K_SNAPS, []);
    if (snaps.length < 2) {
      warn(`servono almeno 2 scan per un confronto (ne hai ${snaps.length}). Rilancia IU.scan() fra qualche giorno.`);
      return null;
    }

    const A = snaps.at(olderIndex);
    const B = snaps.at(newerIndex);
    if (!A || !B) return warn("indici snapshot non validi");

    const nameOf = new Map([...A.fg, ...A.fr, ...B.fg, ...B.fr]);
    const setOf = (pairs) => new Set(pairs.map((p) => p[0]));

    const aFr = setOf(A.fr), bFr = setOf(B.fr);
    const aFg = setOf(A.fg), bFg = setOf(B.fg);

    const mk = (id) => ({ id, username: nameOf.get(id) || `id:${id}` });

    // Erano fra i tuoi follower, non lo sono più.
    const goneFromFollowers = [...aFr].filter((id) => !bFr.has(id));

    // Distinzione che evita accuse sbagliate: se una persona sparisce dai tuoi
    // follower MA anche dalla lista di chi segui — e tu non hai fatto unfollow —
    // di solito l'account è stato disattivato/eliminato, o ti ha bloccato.
    // Se sparisce solo dai follower e tu continui a seguirla, ti ha tolto il follow.
    const unfollowedYou = [];
    const vanished = [];
    for (const id of goneFromFollowers) {
      if (aFg.has(id) && !bFg.has(id)) vanished.push(mk(id));
      else unfollowedYou.push(mk(id));
    }

    const res = {
      from: A.t,
      to: B.t,
      unfollowedYou,
      vanished,
      newFollowers: [...bFr].filter((id) => !aFr.has(id)).map(mk),
      youUnfollowed: [...aFg].filter((id) => !bFg.has(id) && !vanished.some((v) => v.id === id)).map(mk),
      youFollowed: [...bFg].filter((id) => !aFg.has(id)).map(mk),
      delta: { followers: bFr.size - aFr.size, following: bFg.size - aFg.size },
    };

    const keep = new Set(store.get(K_KEEP, []).map(norm));
    const show = (label, list, color) => {
      const f = list.filter((u) => !keep.has(norm(u.username)));
      if (!f.length) return;
      console.log(`%c${label} (${f.length})`, `color:${color};font-weight:bold`);
      console.table(f.map((u) => ({ username: u.username, profilo: `${ORIGIN}/${u.username}/` })));
    };

    console.group(`%c[IU] Confronto  ${fmtDate(A.t)}  →  ${fmtDate(B.t)}`, "color:#4ea1ff;font-weight:bold");
    log(`follower: ${res.delta.followers >= 0 ? "+" : ""}${res.delta.followers}   ` +
        `seguiti: ${res.delta.following >= 0 ? "+" : ""}${res.delta.following}`);
    show("❌ TI HANNO TOLTO IL FOLLOW", res.unfollowedYou, "#e05252");
    show("👻 spariti (account chiuso o ti hanno bloccato)", res.vanished, "#c0a050");
    show("✅ nuovi follower", res.newFollowers, "#5cb85c");
    show("↩️  hai smesso di seguire", res.youUnfollowed, "#888");
    show("➡️  hai iniziato a seguire", res.youFollowed, "#888");
    if (!res.unfollowedYou.length && !res.vanished.length)
      log("%cNessuno ti ha tolto il follow in questo periodo.", "color:#5cb85c");
    console.groupEnd();

    return res;
  }

  /* ========================================================================
   * REPORT / EXPORT
   * ====================================================================== */
  function requireScan() {
    if (!LAST) { warn("esegui prima IU.scan()"); return null; }
    return LAST;
  }

  function keepSet() { return new Set(store.get(K_KEEP, []).map(norm)); }
  const notKept = (list) => { const k = keepSet(); return list.filter((u) => !k.has(norm(u.username))); };

  function report() {
    const r = requireScan();
    if (!r) return;
    const targets = notKept(r.notFollowingBack);
    const kept = r.notFollowingBack.length - targets.length;

    console.group(`%c[IU] Fotografia — ${fmtDate(r.scannedAt)}`, "color:#4ea1ff;font-weight:bold");
    log(`segui ${r.counts.following} · ti seguono ${r.counts.followers} · reciproci ${r.mutual.length}`);
    log(`non ti seguono: ${r.notFollowingBack.length}${kept ? ` (${kept} in "tengo")` : ""}`);
    console.table(
      targets.slice(0, 200).map((u) => ({
        username: u.username,
        nome: u.full_name,
        privato: u.is_private ? "sì" : "",
        verificato: u.is_verified ? "sì" : "",
        profilo: `${ORIGIN}/${u.username}/`,
      }))
    );
    if (targets.length > 200) log(`…e altri ${targets.length - 200}. Usa IU.html() o IU.csv().`);
    console.groupEnd();
    return targets;
  }

  function download(name, content, mime) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([content], { type: mime }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    log(`scaricato: ${name}`);
  }

  function csv() {
    const r = requireScan();
    if (!r) return;
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [["categoria", "username", "nome", "privato", "verificato", "url"].join(",")];
    const add = (c, l) => l.forEach((u) =>
      rows.push([c, u.username, u.full_name, u.is_private, u.is_verified, `${ORIGIN}/${u.username}/`].map(esc).join(","))
    );
    add("non_ti_segue", r.notFollowingBack);
    add("non_ricambi", r.youDontFollowBack);
    add("reciproco", r.mutual);
    download(`ig-${Date.now()}.csv`, rows.join("\n"), "text/csv");
  }

  function json() {
    const r = requireScan();
    if (!r) return;
    download(`ig-${Date.now()}.json`,
      JSON.stringify({ ...r, diff: store.get(K_SNAPS, []).length >= 2 ? diffSilent() : null }, null, 2),
      "application/json");
  }

  function diffSilent() {
    const snaps = store.get(K_SNAPS, []);
    if (snaps.length < 2) return null;
    const saved = console.group, savedT = console.table, savedL = console.log;
    console.group = console.table = console.log = () => {};
    let r;
    try { r = diff(); } finally { console.group = saved; console.table = savedT; console.log = savedL; }
    return r;
  }

  /** Report HTML autonomo: apri il file e lavora la lista a mano, cliccando. */
  function html() {
    const r = requireScan();
    if (!r) return;
    const d = diffSilent();
    const targets = notKept(r.notFollowingBack);

    const card = (u, extra = "") =>
      `<li><a href="${ORIGIN}/${encodeURIComponent(u.username)}/" target="_blank" rel="noopener">@${escapeHtml(u.username)}</a>` +
      `${u.full_name ? ` <span class="n">${escapeHtml(u.full_name)}</span>` : ""}` +
      `${u.is_verified ? ' <span class="b v">✓</span>' : ""}` +
      `${u.is_private ? ' <span class="b p">privato</span>' : ""}${extra}</li>`;

    const section = (title, list, cls) =>
      !list?.length ? "" :
      `<section class="${cls}"><h2>${escapeHtml(title)} <span class="c">${list.length}</span></h2>` +
      `<ul>${list.map((u) => card(u)).join("")}</ul></section>`;

    const doc = `<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram — chi non ti segue</title><style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e3e3e3;--acc:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e8e8e8;--mut:#9aa0a6;--line:#2c2f34;--acc:#6ea8fe}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,sans-serif;max-width:860px;margin-inline:auto}
h1{font-size:1.5rem;margin:0 0 4px}.sub{color:var(--mut);font-size:.9rem;margin-bottom:28px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:28px}
.stat{border:1px solid var(--line);border-radius:10px;padding:10px 14px;flex:1;min-width:120px}
.stat b{display:block;font-size:1.4rem}.stat span{color:var(--mut);font-size:.8rem}
h2{font-size:1.05rem;margin:28px 0 10px;display:flex;align-items:center;gap:8px}
.c{background:var(--line);border-radius:20px;padding:1px 9px;font-size:.78rem;font-weight:500}
ul{list-style:none;padding:0;margin:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
li{padding:9px 14px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
li:last-child{border-bottom:0}li:hover{background:color-mix(in srgb,var(--fg) 5%,transparent)}
a{color:var(--acc);text-decoration:none;font-weight:600}a:hover{text-decoration:underline}
.n{color:var(--mut);font-size:.87rem}
.b{font-size:.7rem;border-radius:4px;padding:1px 6px;border:1px solid var(--line);color:var(--mut)}
.gone h2{color:#e05252}footer{margin-top:36px;color:var(--mut);font-size:.82rem;border-top:1px solid var(--line);padding-top:14px}
</style></head><body>
<h1>Chi non ti segue</h1>
<div class="sub">Scan del ${escapeHtml(fmtDate(r.scannedAt))}${d ? ` · confronto dal ${escapeHtml(fmtDate(d.from))}` : ""}</div>
<div class="stats">
<div class="stat"><b>${r.counts.following}</b><span>segui</span></div>
<div class="stat"><b>${r.counts.followers}</b><span>ti seguono</span></div>
<div class="stat"><b>${r.mutual.length}</b><span>reciproci</span></div>
<div class="stat"><b>${targets.length}</b><span>non ti seguono</span></div>
</div>
${d ? section("❌ Ti hanno tolto il follow", notKept(d.unfollowedYou), "gone") : ""}
${d ? section("👻 Spariti (account chiuso o ti hanno bloccato)", notKept(d.vanished), "") : ""}
${d ? section("✅ Nuovi follower", d.newFollowers, "") : ""}
${section("Non ti seguono", targets, "")}
${section("Li segui e non ricambiano — no, li ricambi tu", [], "")}
${section("Ti seguono ma non li segui", r.youDontFollowBack, "")}
<footer>Generato in locale dal tuo browser. Nessun dato è stato inviato a terzi.<br>
Clicca un profilo per aprirlo e fare unfollow a mano.</footer>
</body></html>`;

    download(`ig-report-${Date.now()}.html`, doc, "text/html");
    log("Apri il file scaricato: lista cliccabile, lavorala con calma.");
  }

  function open(n = 5) {
    const r = requireScan();
    if (!r) return;
    const slice = notKept(r.notFollowingBack).slice(0, Math.min(n, 10));
    if (!slice.length) return warn("niente da aprire");
    log(`apro ${slice.length} profili — unfollow a mano in ogni tab`);
    slice.forEach((u) => window.open(`${ORIGIN}/${u.username}/`, "_blank"));
  }

  /* ========================================================================
   * LISTA "TENGO"
   * ====================================================================== */
  function keep(...users) {
    const cur = new Set(store.get(K_KEEP, []).map(norm));
    users.flat().map(norm).filter(Boolean).forEach((u) => cur.add(u));
    store.set(K_KEEP, [...cur]);
    log(`lista "tengo": ${cur.size} account`);
    return [...cur];
  }
  function unkeep(...users) {
    const cur = new Set(store.get(K_KEEP, []).map(norm));
    users.flat().map(norm).forEach((u) => cur.delete(u));
    store.set(K_KEEP, [...cur]);
    log(`lista "tengo": ${cur.size} account`);
    return [...cur];
  }
  function keeplist() {
    const l = store.get(K_KEEP, []);
    log(l.length ? l.join(", ") : 'lista "tengo" vuota');
    return l;
  }

  function history() {
    const s = store.get(K_SNAPS, []);
    if (!s.length) return log("nessuno snapshot");
    console.table(s.map((x, i) => ({
      "#": i - s.length, quando: fmtDate(x.t), segui: x.fg.length, "ti seguono": x.fr.length,
    })));
    log("usa IU.diff(-3,-1) per confrontare snapshot non adiacenti");
    return s.map((x) => x.t);
  }

  /* ========================================================================
   * ERRORI / UI
   * ====================================================================== */
  function handleError(e) {
    ABORT = true;
    if (e instanceof AbortError) { ui.status("interrotto"); return log("interrotto."); }
    ui.status("FERMATO — vedi console");
    err("STOP:", e.message);
    if (e instanceof FatalError)
      err("Si è fermato di proposito. Insistere è ciò che trasforma un rate limit in un blocco.");
  }

  const ui = {
    el: null,
    show() {
      if (this.el) return;
      const d = document.createElement("div");
      d.style.cssText = ["position:fixed","bottom:16px","left:16px","z-index:2147483647",
        "background:#111","color:#eee","padding:10px 14px","border-radius:8px",
        "font:13px/1.4 system-ui,sans-serif","border:1px solid #444","max-width:300px",
        "box-shadow:0 4px 16px rgba(0,0,0,.5)"].join(";");
      d.innerHTML = '<b style="color:#4ea1ff">IU</b> <span id="iu-msg">pronto</span>' +
        '<br><button id="iu-stop" style="margin-top:8px;background:#a33;color:#fff;border:0;padding:4px 10px;border-radius:4px;cursor:pointer">stop</button>';
      document.body.appendChild(d);
      const b = d.querySelector("#iu-stop");
      if (b) b.onclick = () => { ABORT = true; this.status("interruzione…"); };
      this.el = d;
    },
    status(m) { const s = this.el?.querySelector("#iu-msg"); if (s) s.textContent = m; },
    hide() { this.el?.remove(); this.el = null; },
  };

  /* ========================================================================
   * API
   * ====================================================================== */
  if (location.hostname !== "www.instagram.com") {
    alert("Esegui su https://www.instagram.com/");
    return;
  }

  window.IU = {
    scan, diff, report, html, csv, json, open,
    keep, unkeep, keeplist, history,
    stop: () => { ABORT = true; log("interruzione richiesta"); },
    reset: () => {
      [K_SNAPS, K_KEEP, `${K_CK}_following`, `${K_CK}_followers`].forEach(store.del);
      LAST = null; ui.hide(); log("stato cancellato");
    },
    config: CONFIG,
    get last() { return LAST; },
  };

  const n = store.get(K_SNAPS, []).length;
  console.log(
    `%cIU pronto — sola lettura.%c  IU.scan() per iniziare.` +
    (n >= 2 ? `\n${n} snapshot salvati: IU.diff() per vedere chi ti ha tolto il follow.`
            : n === 1 ? `\n1 snapshot salvato: dopo il prossimo scan avrai il confronto.` : ""),
    "color:#4ea1ff;font-weight:bold;font-size:14px", "color:inherit"
  );
})();
