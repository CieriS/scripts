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
 *   IU.report()      chi non ti segue (ultimo scan, anche dopo un reload)
 *   IU.html()        report HTML cliccabile da aprire e lavorare
 *   IU.csv()         export CSV
 *   IU.json()        export JSON completo
 *   IU.open(n)       apre i prossimi n profili in tab (max 10), per agire a mano
 *   IU.keep(...u)    "questi li tengo comunque" — spariscono dai report
 *   IU.unkeep(...u)  rimuove dalla lista "tengo"
 *   IU.keeplist()    mostra la lista "tengo"
 *   IU.history()     snapshot salvati
 *   IU.stop()        interrompe uno scan in corso
 *   IU.reset()       cancella tutto lo stato salvato
 *
 * AFFIDABILITÀ — Instagram a volte restituisce liste più corte del vero. Lo
 * scan confronta le liste lette con i contatori del profilo: se mancano
 * account lo dice ("liste incomplete") e segna lo snapshot, così diff() avvisa
 * che il confronto può contenere falsi unfollow. Gli snapshot sono separati
 * per account: cambiando account nello stesso browser non si mescolano.
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
    MAX_HISTORY: 8, // snapshot conservati per account
    LIST_GAP_MIN: 3_000, // pausa fra la lista "following" e la lista "followers"
    LIST_GAP_MAX: 6_000,
    CHECKPOINT_TTL: 30 * 60_000, // uno scan interrotto da più tempo riparte da zero
    COUNT_TOLERANCE: 0.02, // scarto ammesso fra lista letta e contatore del profilo…
    COUNT_TOLERANCE_MIN: 3, // …e suo minimo in account (i contatori contano anche i disattivati)
  };

  const ORIGIN = "https://www.instagram.com";
  const APP_ID = "936619743392459";
  const K_SNAPS = "__iu_snaps_v2";
  const K_KEEP = "__iu_keep_v2";
  const K_CK = "__iu_ck_v2";
  const SNAP_VERSION = 2;

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
  // soft: per le richieste facoltative. Un solo tentativo, nessuna attesa:
  // qualunque errore restituisce null invece di fermare lo scan. Solo i
  // segnali di blocco nel corpo della risposta fermano comunque.
  async function igFetch(path, { soft = false } = {}) {
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
        if (soft) return null;
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

      if (soft && !res.ok) return null;

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

      if (!res.ok) {
        if (soft) return null;
        throw new FatalError(`HTTP ${res.status} inatteso`);
      }

      try { return JSON.parse(body); }
      catch {
        if (soft) return null;
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
  /**
   * Contatori del profilo: servono solo a capire se le liste lette sono
   * complete. Se la richiesta non riesce lo scan prosegue, senza verifica.
   */
  async function fetchExpected(userId) {
    const data = await igFetch(`/api/v1/users/${userId}/info/`, { soft: true });
    const u = data?.user;
    if (!Number.isFinite(u?.follower_count) || !Number.isFinite(u?.following_count)) return null;
    return { following: u.following_count, followers: u.follower_count };
  }

  /** true se la lista letta è più corta del contatore, oltre lo scarto ammesso. */
  const isShort = (got, expected) =>
    got < expected - Math.max(CONFIG.COUNT_TOLERANCE_MIN, Math.ceil(expected * CONFIG.COUNT_TOLERANCE));

  async function fetchList(userId, kind, onProgress) {
    const ckKey = `${K_CK}_${kind}`;
    let ck = store.get(ckKey, null);
    // Riprendere uno scan interrotto ha senso solo a caldo e sullo stesso
    // account: altrimenti si mescolano liste di momenti (o persone) diversi.
    const fresh = ck && ck.uid === userId && ck.cursor && Date.now() - ck.t < CONFIG.CHECKPOINT_TTL;
    if (ck && !fresh) { store.del(ckKey); ck = null; }

    // Per id: l'API ripete alcuni utenti fra una pagina e la successiva.
    const byId = new Map((ck?.users || []).map((u) => [u.id, u]));
    let cursor = ck?.cursor || null;
    let page = 0;

    if (byId.size) log(`${kind}: riprendo da ${byId.size}`);

    while (true) {
      if (ABORT) throw new AbortError("interrotto");

      const qs = new URLSearchParams({ count: String(CONFIG.PAGE_SIZE) });
      if (cursor) qs.set("max_id", cursor);

      const data = await igFetch(`/api/v1/friendships/${userId}/${kind}/?${qs}`);
      const batch = Array.isArray(data.users) ? data.users : [];

      for (const u of batch) {
        const id = String(u.pk ?? u.pk_id ?? u.id ?? "");
        if (!id || byId.has(id)) continue;
        byId.set(id, {
          id,
          username: u.username,
          full_name: u.full_name || "",
          is_private: !!u.is_private,
          is_verified: !!u.is_verified,
        });
      }

      cursor = data.next_max_id ? String(data.next_max_id) : null;
      page++;
      onProgress?.(kind, byId.size);
      store.set(ckKey, { uid: userId, t: Date.now(), users: [...byId.values()], cursor });

      if (!cursor || batch.length === 0) break;

      await waitFor(
        page % CONFIG.LONG_BREAK_EVERY === 0
          ? rand(CONFIG.LONG_BREAK_MIN, CONFIG.LONG_BREAK_MAX)
          : rand(CONFIG.DELAY_MIN, CONFIG.DELAY_MAX),
        `${kind}: pausa`
      );
    }

    store.del(ckKey);
    return [...byId.values()];
  }

  /* ========================================================================
   * SNAPSHOT — uno per scan, etichettato con l'account che l'ha prodotto
   * ====================================================================== */
  // Gli snapshot senza etichetta (versioni precedenti) valgono per chi è loggato.
  const isMine = (uid) => (s) => !s.u || s.u === uid;
  const mySnaps = (uid = getCookie("ds_user_id")) => store.get(K_SNAPS, []).filter(isMine(uid));

  /** Salva lo snapshot in forma compatta, per non saturare localStorage. */
  function pushSnapshot(uid, t, following, followers, partial) {
    const compact = (list) => list.map((u) => [u.id, u.username]);
    const all = store.get(K_SNAPS, []);
    const mine = all.filter(isMine(uid));
    const others = all.filter((s) => !isMine(uid)(s));

    mine.forEach((s) => { s.u = uid; });
    // v: snapshot scritto da questa versione. Senza v viene da una versione
    // precedente, che poteva salvare liste mescolate o troncate.
    const snap = { v: SNAP_VERSION, t, u: uid, fg: compact(following), fr: compact(followers) };
    if (partial) snap.p = 1; // liste incomplete: il confronto con questo scan è inaffidabile
    mine.push(snap);
    while (mine.length > CONFIG.MAX_HISTORY) mine.shift();

    // Se la quota esplode, sacrifica gli snapshot più vecchi.
    const next = [...others, ...mine];
    let saved = store.set(K_SNAPS, next);
    while (!saved && next.length > 1) { next.shift(); saved = store.set(K_SNAPS, next); }
    return { saved, count: saved ? next.filter(isMine(uid)).length : 0 };
  }

  /** Le tre liste del report, a partire dalle due liste lette. */
  function buildResult(scannedAt, following, followers, extra = {}) {
    const frIds = new Set(followers.map((u) => u.id));
    const fgIds = new Set(following.map((u) => u.id));
    return {
      scannedAt,
      counts: { following: following.length, followers: followers.length },
      expected: null,
      partial: false,
      ...extra,
      notFollowingBack: following.filter((u) => !frIds.has(u.id)),
      youDontFollowBack: followers.filter((u) => !fgIds.has(u.id)),
      mutual: following.filter((u) => frIds.has(u.id)),
    };
  }

  /**
   * Ricostruisce il risultato da uno snapshot salvato, così dopo un reload
   * report ed export funzionano senza un nuovo scan (cioè senza altre richieste).
   * Lo snapshot è compatto: nome, privato e verificato non ci sono.
   */
  function fromSnapshot(snap) {
    const expand = (pairs) =>
      pairs.map(([id, username]) => ({ id, username, full_name: "", is_private: null, is_verified: null }));
    return buildResult(snap.t, expand(snap.fg), expand(snap.fr), { partial: !!snap.p, restored: true });
  }

  let LAST = null; // ultimo risultato completo, in memoria
  const OPENED = new Set(); // id dei profili già aperti con open() per questo risultato
  let RUNNING = false;

  async function scan() {
    if (RUNNING) return warn("uno scan è già in corso. IU.stop() per interromperlo.");
    const userId = getCookie("ds_user_id");
    if (!userId) return err("cookie ds_user_id assente. Sei loggato su instagram.com?");

    RUNNING = true;
    ABORT = false;
    ui.show();
    ui.status("scan — sola lettura");
    const t0 = Date.now();

    try {
      const onProgress = (kind, n) => ui.status(`${kind}: ${n}`);

      const expected = await fetchExpected(userId);
      if (expected) await waitFor(rand(CONFIG.DELAY_MIN, CONFIG.DELAY_MAX), "");
      else warn("non riesco a leggere i contatori del profilo: non posso verificare che le liste siano complete.");

      // L'API attuale non espone follows_viewer sulla lista following:
      // servono entrambe le liste e una differenza insiemistica.
      const following = await fetchList(userId, "following", onProgress);
      await waitFor(rand(CONFIG.LIST_GAP_MIN, CONFIG.LIST_GAP_MAX), "");
      const followers = await fetchList(userId, "followers", onProgress);

      const partial = !!expected &&
        (isShort(following.length, expected.following) || isShort(followers.length, expected.followers));

      const scannedAt = new Date().toISOString();
      const { saved, count } = pushSnapshot(userId, scannedAt, following, followers, partial);
      LAST = buildResult(scannedAt, following, followers, { expected, partial });
      OPENED.clear();

      ui.status(`fatto in ${Math.round((Date.now() - t0) / 1000)}s`);
      if (saved) log(`Scan completato. Snapshot salvati: ${count}/${CONFIG.MAX_HISTORY}`);
      else warn("Scan completato, ma lo snapshot NON è stato salvato: spazio del browser esaurito. " +
                "Esporta con IU.json(); il prossimo IU.diff() non vedrà questo scan.");

      report();
      if (count >= 2) { console.log(""); diff(); }
      else if (saved) log("Primo snapshot: al prossimo scan IU.diff() ti dirà chi ti ha tolto il follow.");

      return LAST;
    } catch (e) {
      handleError(e);
    } finally {
      RUNNING = false;
    }
  }

  /* ========================================================================
   * DIFF — la funzione che risponde davvero a "chi mi ha unfollowato"
   * ====================================================================== */
  /** Confronto fra due snapshot (A più vecchio di B). Nessun effetto collaterale. */
  function computeDiff(A, B) {
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
    const vanishedIds = new Set();
    for (const id of goneFromFollowers) {
      if (aFg.has(id) && !bFg.has(id)) { vanished.push(mk(id)); vanishedIds.add(id); }
      else unfollowedYou.push(mk(id));
    }

    return {
      from: A.t,
      to: B.t,
      // Uno dei due scan era incompleto: chi manca dalla lista sembra un unfollow.
      unreliable: !!(A.p || B.p),
      // Uno dei due viene da una versione precedente dello script: stesse conseguenze.
      legacy: A.v !== SNAP_VERSION || B.v !== SNAP_VERSION,
      unfollowedYou,
      vanished,
      newFollowers: [...bFr].filter((id) => !aFr.has(id)).map(mk),
      youUnfollowed: [...aFg].filter((id) => !bFg.has(id) && !vanishedIds.has(id)).map(mk),
      youFollowed: [...bFg].filter((id) => !aFg.has(id)).map(mk),
      delta: { followers: bFr.size - aFr.size, following: bFg.size - aFg.size },
    };
  }

  /** Confronto fra gli ultimi due snapshot dell'account, o null se non bastano. */
  function lastDiff() {
    const s = mySnaps();
    return s.length >= 2 ? computeDiff(s.at(-2), s.at(-1)) : null;
  }

  function diff(olderIndex = -2, newerIndex = -1) {
    const snaps = mySnaps();
    if (snaps.length < 2) {
      warn(`servono almeno 2 scan per un confronto (ne hai ${snaps.length}). Rilancia IU.scan() fra qualche giorno.`);
      return null;
    }

    let A = snaps.at(olderIndex);
    let B = snaps.at(newerIndex);
    if (!A || !B || A === B) {
      warn("indici snapshot non validi: IU.history() mostra quelli disponibili");
      return null;
    }
    if (A.t > B.t) [A, B] = [B, A]; // sempre dal più vecchio al più recente

    const res = computeDiff(A, B);

    const keep = keepSet();
    const show = (label, list, color) => {
      const f = list.filter((u) => !keep.has(norm(u.username)));
      if (!f.length) return;
      console.log(`%c${label} (${f.length})`, `color:${color};font-weight:bold`);
      console.table(f.map((u) => ({ username: u.username, profilo: `${ORIGIN}/${u.username}/` })));
    };

    console.group(`%c[IU] Confronto  ${fmtDate(A.t)}  →  ${fmtDate(B.t)}`, "color:#4ea1ff;font-weight:bold");
    if (res.unreliable)
      warn("⚠️ uno dei due scan era incompleto: questo confronto può contenere falsi \"ti hanno tolto il follow\".");
    if (res.legacy)
      warn("⚠️ lo snapshot precedente è stato salvato da una versione vecchia dello script, che " +
           "poteva registrare liste incomplete o mescolate: \"nuovi follower\" e \"hai iniziato a " +
           "seguire\" possono contenere account di vecchia data. Dal prossimo scan il confronto è affidabile.");
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
    if (r.partial)
      warn("⚠️ liste incomplete" +
           (r.expected
             ? `: letti ${r.counts.following}/${r.expected.following} seguiti e ` +
               `${r.counts.followers}/${r.expected.followers} follower`
             : "") +
           `. Instagram a volte tronca le liste: chi manca risulta "non ti segue" anche se ti segue. ` +
           `Riprova fra qualche ora.`);
    if (r.restored)
      log("fotografia ricostruita dall'ultimo snapshot salvato: nome, privato e verificato " +
          "tornano disponibili con un nuovo IU.scan().");
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
    // Excel e Numbers leggono come formula una cella che inizia con = + - @
    // (o tab/a capo): un apostrofo davanti la fa restare testo.
    const safe = (v) => { const s = String(v ?? ""); return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s; };
    const esc = (v) => `"${safe(v).replace(/"/g, '""')}"`;
    const rows = [["categoria", "username", "nome", "privato", "verificato", "url"].join(",")];
    const add = (c, l) => l.forEach((u) =>
      rows.push([c, u.username, u.full_name, u.is_private, u.is_verified, `${ORIGIN}/${u.username}/`].map(esc).join(","))
    );
    add("non_ti_segue", r.notFollowingBack);
    add("non_ricambi", r.youDontFollowBack);
    add("reciproco", r.mutual);
    // BOM + CRLF: senza, Excel mostra male accenti ed emoji dei nomi.
    download(`ig-${Date.now()}.csv`, "\uFEFF" + rows.join("\r\n"), "text/csv;charset=utf-8");
  }

  function json() {
    const r = requireScan();
    if (!r) return;
    download(`ig-${Date.now()}.json`,
      JSON.stringify({ ...r, diff: lastDiff() }, null, 2),
      "application/json");
  }

  /** Report HTML autonomo: apri il file e lavora la lista a mano, cliccando. */
  function html() {
    const r = requireScan();
    if (!r) return;
    const d = lastDiff();
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
${d?.legacy ? `<div class="sub">⚠️ Il confronto usa uno snapshot di una versione vecchia dello script: i nuovi follower possono includere account di vecchia data.</div>` : ""}
${r.partial || d?.unreliable ? `<div class="sub">⚠️ Liste incomplete in questo scan o nel precedente: l'elenco può contenere persone che in realtà ti seguono.</div>` : ""}
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
    // Solo quelli non ancora aperti: chiamate successive proseguono nella lista.
    const todo = notKept(r.notFollowingBack).filter((u) => !OPENED.has(u.id));
    const slice = todo.slice(0, Math.max(0, Math.min(n, 10)));
    if (!slice.length)
      return warn(OPENED.size ? "niente da aprire: li hai già aperti tutti" : "niente da aprire");

    let blocked = 0;
    for (const u of slice) {
      if (window.open(`${ORIGIN}/${u.username}/`, "_blank")) OPENED.add(u.id);
      else blocked++;
    }
    const done = slice.length - blocked;
    log(`aperti ${done} profili, ne restano ${todo.length - done} — unfollow a mano in ogni tab`);
    if (blocked)
      warn(`il browser ha bloccato ${blocked} schede: consenti i popup per instagram.com ` +
           `(icona nella barra degli indirizzi) e rilancia IU.open().`);
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
    const s = mySnaps();
    if (!s.length) return log("nessuno snapshot");
    console.table(s.map((x, i) => ({
      "#": i - s.length, quando: fmtDate(x.t), segui: x.fg.length, "ti seguono": x.fr.length,
      completo: x.p ? "no" : "sì",
      versione: x.v === SNAP_VERSION ? "attuale" : "vecchia",
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
      LAST = null; OPENED.clear(); ui.hide(); log("stato cancellato");
    },
    config: CONFIG,
    get last() { return LAST; },
  };

  const mine = mySnaps();
  const n = mine.length;
  if (n) LAST = fromSnapshot(mine.at(-1));
  console.log(
    `%cIU pronto — sola lettura.%c  IU.scan() per iniziare.` +
    (n >= 2 ? `\n${n} snapshot salvati: IU.diff() per vedere chi ti ha tolto il follow.`
            : n === 1 ? `\n1 snapshot salvato: dopo il prossimo scan avrai il confronto.` : ""),
    "color:#4ea1ff;font-weight:bold;font-size:14px", "color:inherit"
  );
})();
