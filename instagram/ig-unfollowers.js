/* =============================================================================
 * ig-unfollowers.js — chi non ti segue più, senza farti bloccare.
 * =============================================================================
 *
 * USO
 *   1. Apri https://www.instagram.com/ e fai login.
 *   2. Apri la console (Cmd+Opt+J su Chrome/macOS).
 *   3. Incolla TUTTO questo file e premi Invio.
 *   4. Comandi disponibili:
 *
 *        IU.scan()        → analisi SOLA LETTURA. Nessun rischio di blocco.
 *        IU.report()      → mostra i risultati dell'ultimo scan
 *        IU.csv()         → scarica i risultati in CSV
 *        IU.json()        → scarica i risultati in JSON
 *        IU.open(n)       → apre i primi n profili in tab (unfollow manuale)
 *        IU.budget()      → quanto margine ti resta rispetto ai limiti
 *        IU.unfollow()    → OPZIONALE e rischioso. Vedi sotto.
 *        IU.stop()        → interrompe qualsiasi operazione in corso
 *        IU.reset()       → cancella scan e contatori salvati
 *
 * GARANZIE — leggi, sono importanti
 *   • scan/report/csv/json/open NON modificano nulla sul tuo account. Fanno le
 *     stesse GET che fa l'app di Instagram quando apri la lista "Following".
 *     Il rischio di action block su queste è sostanzialmente nullo.
 *   • unfollow() invia POST che modificano l'account. È L'UNICA parte che
 *     comporta un rischio reale, e nessuno — me incluso — può azzerarlo.
 *     Parte in DRY_RUN: simula senza inviare nulla finché non lo disattivi.
 *
 * PRIVACY — questo file non contatta nulla al di fuori di www.instagram.com.
 *   Cerca "fetch(" nel file: troverai una sola funzione di rete, igFetch(),
 *   e un solo host. Nessuna dipendenza, nessun CDN, nessuna telemetria.
 * ========================================================================== */

(() => {
  "use strict";

  /* ===========================================================================
   * CONFIGURAZIONE — modifica qui
   * ========================================================================= */

  const CONFIG = {
    // --- Sicurezza ---------------------------------------------------------
    // true  = unfollow() simula soltanto, non invia nulla. Default prudente.
    // false = unfollow() agisce davvero. Cambialo solo quando hai letto il CSV.
    DRY_RUN: true,

    // Username da non toccare MAI, anche se non ti seguono.
    // Confronto case-insensitive, senza @.
    WHITELIST: [
      // "amico_stretto",
      // "account_di_lavoro",
    ],

    // --- Limiti di scrittura (unfollow) ------------------------------------
    // Tarati DELIBERATAMENTE sotto le soglie riportate dalla community
    // (20-30/ora, 100-200/giorno per account maturi). Il margine è voluto:
    // le soglie reali non sono pubbliche e si adattano al singolo account.
    MAX_UNFOLLOW_PER_HOUR: 15,
    MAX_UNFOLLOW_PER_DAY: 80,
    MAX_UNFOLLOW_PER_SESSION: 20,

    // Pausa fra un unfollow e il successivo (ms, estratta a caso nel range).
    // 45-90s: molto più lento dei 4-6s dello script originale.
    UNFOLLOW_DELAY_MIN: 45_000,
    UNFOLLOW_DELAY_MAX: 90_000,

    // Ogni N unfollow, una pausa lunga aggiuntiva.
    UNFOLLOW_LONG_BREAK_EVERY: 8,
    UNFOLLOW_LONG_BREAK_MIN: 300_000, // 5 min
    UNFOLLOW_LONG_BREAK_MAX: 600_000, // 10 min

    // --- Ritmo di lettura (scan) -------------------------------------------
    PAGE_SIZE: 50,
    SCAN_DELAY_MIN: 1_800,
    SCAN_DELAY_MAX: 3_500,
    SCAN_LONG_BREAK_EVERY: 15,
    SCAN_LONG_BREAK_MIN: 20_000,
    SCAN_LONG_BREAK_MAX: 35_000,

    // --- Rete ---------------------------------------------------------------
    MAX_RETRIES: 4,
    BACKOFF_BASE: 4_000, // 4s, 8s, 16s, 32s + jitter
    REQUEST_TIMEOUT: 30_000,
  };

  /* ===========================================================================
   * COSTANTI
   * ========================================================================= */

  const ORIGIN = "https://www.instagram.com";
  const APP_ID = "936619743392459"; // stesso valore che invia l'app web
  const STORE_KEY = "__iu_state_v1";
  const BUDGET_KEY = "__iu_budget_v1";

  // Se la risposta contiene uno di questi, Instagram ti ha segnalato.
  // In questi casi si FERMA SUBITO: ritentare peggiora la situazione.
  const FATAL_SIGNALS = [
    "feedback_required",
    "challenge_required",
    "checkpoint_required",
    "login_required",
    "user_has_logged_out",
    "rate_limit_error",
  ];

  /* ===========================================================================
   * UTILITY
   * ========================================================================= */

  let ABORT = false;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;

  /** Attesa interrompibile: controlla ABORT ogni 500ms. */
  async function waitFor(ms, label) {
    const until = Date.now() + ms;
    if (ms >= 10_000) {
      ui.status(`${label} — attendo ${Math.round(ms / 1000)}s`);
    }
    while (Date.now() < until) {
      if (ABORT) throw new AbortError("interrotto dall'utente");
      await sleep(Math.min(500, until - Date.now()));
    }
  }

  class AbortError extends Error {}
  class FatalError extends Error {}

  const ts = () => new Date().toLocaleTimeString("it-IT");
  const log = (...a) => console.log(`%c[IU ${ts()}]`, "color:#4ea1ff", ...a);
  const warn = (...a) => console.warn(`[IU ${ts()}]`, ...a);
  const err = (...a) => console.error(`[IU ${ts()}]`, ...a);

  function getCookie(name) {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return undefined;
  }

  /** localStorage che non esplode se non disponibile. */
  const store = {
    get(key, fallback = null) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch {
        return fallback;
      }
    },
    set(key, val) {
      try {
        localStorage.setItem(key, JSON.stringify(val));
        return true;
      } catch {
        return false;
      }
    },
    del(key) {
      try {
        localStorage.removeItem(key);
      } catch {}
    },
  };

  /* ===========================================================================
   * BUDGET — contatori persistenti, sopravvivono a reload e chiusure di tab.
   * Lo script originale non aveva memoria: ricaricando la pagina ripartiva da
   * zero e potevi superare i limiti giornalieri senza accorgertene.
   * ========================================================================= */

  const budget = {
    _load() {
      const b = store.get(BUDGET_KEY, { events: [] });
      const cutoff = Date.now() - 24 * 3600 * 1000;
      b.events = (b.events || []).filter((t) => t > cutoff);
      return b;
    },
    record() {
      const b = this._load();
      b.events.push(Date.now());
      store.set(BUDGET_KEY, b);
    },
    counts() {
      const b = this._load();
      const now = Date.now();
      return {
        lastHour: b.events.filter((t) => t > now - 3600 * 1000).length,
        lastDay: b.events.length,
      };
    },
    /** @returns {null|string} motivo del blocco, o null se si può procedere */
    blockedReason() {
      const { lastHour, lastDay } = this.counts();
      if (lastDay >= CONFIG.MAX_UNFOLLOW_PER_DAY)
        return `limite giornaliero raggiunto (${lastDay}/${CONFIG.MAX_UNFOLLOW_PER_DAY})`;
      if (lastHour >= CONFIG.MAX_UNFOLLOW_PER_HOUR)
        return `limite orario raggiunto (${lastHour}/${CONFIG.MAX_UNFOLLOW_PER_HOUR})`;
      return null;
    },
  };

  /* ===========================================================================
   * LIVELLO RETE — l'unico punto del file che parla con l'esterno.
   * ========================================================================= */

  /**
   * Wrapper su fetch con backoff esponenziale, rispetto di Retry-After e
   * arresto immediato sui segnali di action block.
   *
   * Differenza chiave rispetto allo script originale: quello, in caso di
   * errore, rilanciava la richiesta immediatamente e all'infinito. Qui ogni
   * errore ha un tetto di tentativi e alcuni non vengono ritentati affatto.
   */
  async function igFetch(path, options = {}) {
    const url = path.startsWith("http") ? path : ORIGIN + path;

    for (let attempt = 0; attempt <= CONFIG.MAX_RETRIES; attempt++) {
      if (ABORT) throw new AbortError("interrotto dall'utente");

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), CONFIG.REQUEST_TIMEOUT);

      let res;
      try {
        res = await fetch(url, {
          ...options,
          credentials: "include",
          signal: controller.signal,
          headers: {
            "X-IG-App-ID": APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            ...(options.headers || {}),
          },
        });
      } catch (e) {
        clearTimeout(timer);
        if (ABORT) throw new AbortError("interrotto dall'utente");
        if (attempt === CONFIG.MAX_RETRIES)
          throw new FatalError(`rete non raggiungibile: ${e.message}`);
        const wait = CONFIG.BACKOFF_BASE * 2 ** attempt + rand(0, 2000);
        warn(`errore di rete (${e.message}), ritento fra ${Math.round(wait / 1000)}s`);
        await waitFor(wait, "backoff");
        continue;
      }
      clearTimeout(timer);

      const body = await res.text();
      const lower = body.slice(0, 2000).toLowerCase();

      // --- Segnali fatali: NON ritentare mai. ------------------------------
      const signal = FATAL_SIGNALS.find((s) => lower.includes(s));
      if (signal) {
        throw new FatalError(
          `Instagram ha risposto "${signal}". ` +
            `Fermo tutto. NON rilanciare lo script: aspetta almeno 24-48h, ` +
            `usa l'app normalmente e riprova con calma.`
        );
      }
      if (res.status === 401 || res.status === 403) {
        throw new FatalError(
          `HTTP ${res.status}: sessione non valida. Ricarica Instagram e rifai login.`
        );
      }

      // --- 429 / 5xx: ritentabili con backoff. -----------------------------
      if (res.status === 429 || res.status >= 500) {
        if (attempt === CONFIG.MAX_RETRIES)
          throw new FatalError(
            `HTTP ${res.status} persistente dopo ${CONFIG.MAX_RETRIES} tentativi. ` +
              `Sei rate-limited: fermati per qualche ora.`
          );
        const retryAfter = parseInt(res.headers.get("retry-after") || "", 10);
        const wait = Number.isFinite(retryAfter)
          ? retryAfter * 1000 + rand(1000, 5000)
          : CONFIG.BACKOFF_BASE * 2 ** attempt + rand(0, 3000);
        warn(`HTTP ${res.status}, attendo ${Math.round(wait / 1000)}s`);
        await waitFor(wait, `HTTP ${res.status}`);
        continue;
      }

      if (!res.ok) throw new FatalError(`HTTP ${res.status} inatteso`);

      try {
        return JSON.parse(body);
      } catch {
        throw new FatalError(
          "risposta non-JSON: di solito significa che Instagram ha servito " +
            "una pagina di verifica. Apri instagram.com e controlla l'account."
        );
      }
    }
    throw new FatalError("tentativi esauriti");
  }

  /* ===========================================================================
   * SCAN — sola lettura
   * ========================================================================= */

  /**
   * Scarica followers o following con paginazione e checkpoint.
   * Il checkpoint è una miglioria concreta: se lo scan si interrompe a metà,
   * riprende da dove era invece di rifare tutte le richieste da capo. Meno
   * richieste totali = meno superficie di rilevamento.
   */
  async function fetchList(userId, kind, onProgress) {
    const ckKey = `${STORE_KEY}_ck_${kind}`;
    const ck = store.get(ckKey, null);

    let users = ck?.users || [];
    let cursor = ck?.cursor || null;
    let page = 0;

    if (users.length) log(`${kind}: riprendo da ${users.length} già scaricati`);

    while (true) {
      if (ABORT) throw new AbortError("interrotto dall'utente");

      const qs = new URLSearchParams({ count: String(CONFIG.PAGE_SIZE) });
      if (cursor) qs.set("max_id", cursor);

      const data = await igFetch(
        `/api/v1/friendships/${userId}/${kind}/?${qs}`
      );

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

      if (page % CONFIG.SCAN_LONG_BREAK_EVERY === 0) {
        await waitFor(
          rand(CONFIG.SCAN_LONG_BREAK_MIN, CONFIG.SCAN_LONG_BREAK_MAX),
          `${kind}: pausa lunga`
        );
      } else {
        await waitFor(rand(CONFIG.SCAN_DELAY_MIN, CONFIG.SCAN_DELAY_MAX), "");
      }
    }

    store.del(ckKey);
    return users;
  }

  async function scan() {
    ABORT = false;
    const userId = getCookie("ds_user_id");
    if (!userId) {
      err("cookie ds_user_id assente. Sei loggato su instagram.com?");
      return;
    }

    ui.show();
    ui.status("scan in corso — sola lettura");
    const t0 = Date.now();

    try {
      const onProgress = (kind, n) => ui.status(`${kind}: ${n} scaricati`);

      // L'API attuale non espone "follows_viewer" sulla lista following, come
      // faceva il vecchio GraphQL. Servono entrambe le liste e una differenza
      // insiemistica. È il motivo per cui questo scan fa il doppio delle
      // richieste rispetto allo script del 2022 — non c'è alternativa.
      const following = await fetchList(userId, "following", onProgress);
      await waitFor(rand(3000, 6000), "");
      const followers = await fetchList(userId, "followers", onProgress);

      const followerIds = new Set(followers.map((u) => u.id));
      const followingIds = new Set(following.map((u) => u.id));

      const result = {
        scannedAt: new Date().toISOString(),
        counts: { following: following.length, followers: followers.length },
        // Li segui, non ti seguono.
        notFollowingBack: following.filter((u) => !followerIds.has(u.id)),
        // Ti seguono, non li segui.
        youDontFollowBack: followers.filter((u) => !followingIds.has(u.id)),
        // Reciproci.
        mutual: following.filter((u) => followerIds.has(u.id)),
      };

      store.set(STORE_KEY, result);
      ui.status(`scan completato in ${Math.round((Date.now() - t0) / 1000)}s`);
      report();
      return result;
    } catch (e) {
      handleError(e);
    }
  }

  /* ===========================================================================
   * OUTPUT
   * ========================================================================= */

  function loadResult() {
    const r = store.get(STORE_KEY, null);
    if (!r) {
      warn("nessuno scan salvato. Esegui prima IU.scan()");
      return null;
    }
    return r;
  }

  function isWhitelisted(u) {
    const wl = CONFIG.WHITELIST.map((s) => s.toLowerCase().replace(/^@/, ""));
    return wl.includes((u.username || "").toLowerCase());
  }

  function report() {
    const r = loadResult();
    if (!r) return;

    const targets = r.notFollowingBack.filter((u) => !isWhitelisted(u));
    const skipped = r.notFollowingBack.length - targets.length;

    console.group(
      `%c[IU] Risultati — scan del ${new Date(r.scannedAt).toLocaleString("it-IT")}`,
      "color:#4ea1ff;font-weight:bold"
    );
    log(`Segui:        ${r.counts.following}`);
    log(`Ti seguono:   ${r.counts.followers}`);
    log(`Reciproci:    ${r.mutual.length}`);
    log(`NON ti seguono: ${r.notFollowingBack.length}` + (skipped ? ` (${skipped} in whitelist)` : ""));
    log(`Non ricambi:  ${r.youDontFollowBack.length}`);
    console.table(
      targets.slice(0, 200).map((u) => ({
        username: u.username,
        nome: u.full_name,
        privato: u.is_private ? "sì" : "",
        verificato: u.is_verified ? "sì" : "",
        profilo: `${ORIGIN}/${u.username}/`,
      }))
    );
    if (targets.length > 200) log(`… e altri ${targets.length - 200}. Usa IU.csv().`);
    console.groupEnd();
    return targets;
  }

  function download(filename, content, mime) {
    const blob = new Blob([content], { type: mime });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    log(`scaricato: ${filename}`);
  }

  function csv() {
    const r = loadResult();
    if (!r) return;
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [
      ["categoria", "username", "nome", "privato", "verificato", "url"].join(","),
    ];
    const add = (cat, list) =>
      list.forEach((u) =>
        rows.push(
          [cat, u.username, u.full_name, u.is_private, u.is_verified, `${ORIGIN}/${u.username}/`]
            .map(esc)
            .join(",")
        )
      );
    add("non_ti_segue", r.notFollowingBack);
    add("non_ricambi", r.youDontFollowBack);
    add("reciproco", r.mutual);
    download(`instagram-unfollowers-${Date.now()}.csv`, rows.join("\n"), "text/csv");
  }

  function json() {
    const r = loadResult();
    if (!r) return;
    download(
      `instagram-unfollowers-${Date.now()}.json`,
      JSON.stringify(r, null, 2),
      "application/json"
    );
  }

  /**
   * Apre i profili in tab separate: fai unfollow a mano.
   * Questa è l'unica strada con rischio di blocco pari a zero, perché ogni
   * azione è un click tuo dentro l'interfaccia normale di Instagram.
   */
  function open(n = 5) {
    const targets = (loadResult()?.notFollowingBack || []).filter((u) => !isWhitelisted(u));
    const slice = targets.slice(0, Math.min(n, 10));
    if (!slice.length) return warn("niente da aprire");
    log(`apro ${slice.length} profili — fai unfollow manualmente in ogni tab`);
    slice.forEach((u) => window.open(`${ORIGIN}/${u.username}/`, "_blank"));
    log("quando hai finito, rilancia IU.scan() per aggiornare la lista");
  }

  /* ===========================================================================
   * UNFOLLOW — l'unica parte rischiosa
   * ========================================================================= */

  async function unfollowOne(user, csrf) {
    if (CONFIG.DRY_RUN) {
      log(`[DRY_RUN] simulo unfollow di @${user.username}`);
      return true;
    }
    const data = await igFetch(`/api/v1/friendships/destroy/${user.id}/`, {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "x-csrftoken": csrf,
      },
      body: "",
    });
    // Lo script originale non controllava MAI la risposta: continuava a
    // "unfolloware" anche quando Instagram rifiutava tutto. Qui verifichiamo.
    if (data.status !== "ok" && data.friendship_status === undefined) {
      throw new FatalError(
        `risposta inattesa all'unfollow di @${user.username}: ${JSON.stringify(data).slice(0, 200)}`
      );
    }
    return true;
  }

  async function unfollow(limit = CONFIG.MAX_UNFOLLOW_PER_SESSION) {
    ABORT = false;
    const r = loadResult();
    if (!r) return;

    const csrf = getCookie("csrftoken");
    if (!csrf) return err("cookie csrftoken assente. Ricarica la pagina.");

    let targets = r.notFollowingBack.filter((u) => !isWhitelisted(u));
    const cap = Math.min(limit, CONFIG.MAX_UNFOLLOW_PER_SESSION);
    targets = targets.slice(0, cap);

    if (!targets.length) return log("niente da fare");

    const blocked = budget.blockedReason();
    if (blocked) return warn(`fermo: ${blocked}. Riprova più tardi.`);

    if (CONFIG.DRY_RUN) {
      log(
        `%cDRY_RUN attivo: nessuna richiesta verrà inviata.`,
        "color:#5c5;font-weight:bold"
      );
      log(`Per agire davvero: modifica DRY_RUN: false in cima al file e reincolla.`);
    } else {
      const ok = confirm(
        `Stai per fare unfollow di ${targets.length} account REALI.\n\n` +
          `Durata stimata: ~${Math.round(
            (targets.length * ((CONFIG.UNFOLLOW_DELAY_MIN + CONFIG.UNFOLLOW_DELAY_MAX) / 2)) / 60000
          )} minuti.\n` +
          `Lascia questa tab aperta e non usare Instagram altrove nel frattempo.\n\n` +
          `Procedere?`
      );
      if (!ok) return log("annullato");
    }

    ui.show();
    let done = 0;

    try {
      for (const user of targets) {
        if (ABORT) throw new AbortError("interrotto dall'utente");

        const stop = budget.blockedReason();
        if (stop) {
          warn(`fermo a ${done}: ${stop}`);
          break;
        }

        await unfollowOne(user, csrf);
        if (!CONFIG.DRY_RUN) budget.record();
        done++;

        const c = budget.counts();
        ui.status(`unfollow ${done}/${targets.length} — ora:${c.lastHour} giorno:${c.lastDay}`);
        log(`✓ @${user.username} (${done}/${targets.length})`);

        if (done === targets.length) break;

        if (done % CONFIG.UNFOLLOW_LONG_BREAK_EVERY === 0) {
          await waitFor(
            rand(CONFIG.UNFOLLOW_LONG_BREAK_MIN, CONFIG.UNFOLLOW_LONG_BREAK_MAX),
            "pausa lunga anti-blocco"
          );
        } else {
          await waitFor(
            rand(CONFIG.UNFOLLOW_DELAY_MIN, CONFIG.UNFOLLOW_DELAY_MAX),
            "pausa"
          );
        }
      }
      ui.status(`completati ${done} unfollow`);
      log(`%cFatto: ${done} unfollow.`, "color:#5c5;font-weight:bold");
      if (!CONFIG.DRY_RUN) log("Rilancia IU.scan() per aggiornare la lista.");
    } catch (e) {
      handleError(e, done);
    }
  }

  /* ===========================================================================
   * ERRORI
   * ========================================================================= */

  function handleError(e, done) {
    ABORT = true;
    if (e instanceof AbortError) {
      ui.status("interrotto");
      log("interrotto." + (done != null ? ` Completati: ${done}` : ""));
      return;
    }
    ui.status("FERMATO — vedi console");
    err("STOP:", e.message);
    if (e instanceof FatalError) {
      err(
        "Lo script si è fermato di proposito invece di insistere. " +
          "Insistere è esattamente ciò che trasforma un rate limit in un blocco."
      );
    }
  }

  /* ===========================================================================
   * UI minima — un riquadro flottante. Non distrugge la pagina Instagram,
   * a differenza dello script originale che faceva document.body.innerHTML = ""
   * ========================================================================= */

  const ui = {
    el: null,
    show() {
      if (this.el) return;
      const d = document.createElement("div");
      d.style.cssText = [
        "position:fixed", "bottom:16px", "left:16px", "z-index:2147483647",
        "background:#111", "color:#eee", "padding:10px 14px", "border-radius:8px",
        "font:13px/1.4 system-ui,sans-serif", "border:1px solid #444",
        "max-width:320px", "box-shadow:0 4px 16px rgba(0,0,0,.5)",
      ].join(";");
      d.innerHTML =
        '<b style="color:#4ea1ff">IU</b> <span id="iu-msg">pronto</span>' +
        '<br><button id="iu-stop" style="margin-top:8px;background:#a33;color:#fff;border:0;padding:4px 10px;border-radius:4px;cursor:pointer">stop</button>';
      document.body.appendChild(d);
      d.querySelector("#iu-stop").onclick = () => {
        ABORT = true;
        this.status("interruzione…");
      };
      this.el = d;
    },
    status(msg) {
      if (!this.el) return;
      const s = this.el.querySelector("#iu-msg");
      if (s) s.textContent = msg;
    },
    hide() {
      this.el?.remove();
      this.el = null;
    },
  };

  /* ===========================================================================
   * API PUBBLICA
   * ========================================================================= */

  if (location.hostname !== "www.instagram.com") {
    alert("Esegui questo script su https://www.instagram.com/");
    return;
  }

  window.IU = {
    scan,
    report,
    csv,
    json,
    open,
    unfollow,
    budget: () => {
      const c = budget.counts();
      log(
        `ultima ora: ${c.lastHour}/${CONFIG.MAX_UNFOLLOW_PER_HOUR} — ` +
          `ultime 24h: ${c.lastDay}/${CONFIG.MAX_UNFOLLOW_PER_DAY}`
      );
      return c;
    },
    stop: () => {
      ABORT = true;
      log("interruzione richiesta");
    },
    reset: () => {
      [STORE_KEY, BUDGET_KEY, `${STORE_KEY}_ck_following`, `${STORE_KEY}_ck_followers`]
        .forEach(store.del);
      ui.hide();
      log("stato cancellato");
    },
    config: CONFIG,
  };

  console.log(
    "%cIU pronto.%c  IU.scan() per iniziare (sola lettura).\n" +
      "IU.unfollow() è in DRY_RUN: non invia nulla finché non lo disattivi.",
    "color:#4ea1ff;font-weight:bold;font-size:14px",
    "color:inherit"
  );
})();
