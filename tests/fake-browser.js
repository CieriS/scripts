"use strict";
// Runs instagram/ig-who-unfollowed.js under Node against a fake of the browser
// APIs it touches and a fake of the Instagram endpoints it reads.
// Used by tests/*.test.js; all accounts are synthetic.

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "instagram", "ig-who-unfollowed.js"),
  "utf8"
);

/** A user as the friendships endpoints return it. */
const user = (pk, username = `user${pk}`, extra = {}) => ({
  pk,
  username,
  full_name: "",
  is_private: false,
  is_verified: false,
  ...extra,
});

/** Paginated following/followers lists. */
class FakeInstagram {
  constructor({ following = [], followers = [], pageSize = 2 } = {}) {
    this.following = following;
    this.followers = followers;
    this.pageSize = pageSize;
    this.hidden = new Set();
    // Optional (url) => response, checked before the built-in routes.
    this.override = null;
  }

  respond(url) {
    const forced = this.override?.(url);
    if (forced) return forced;

    const show = url.pathname.match(/^\/api\/v1\/friendships\/show\/(\d+)\/$/);
    if (show) {
      const has = (list) => list.some((u) => String(u.pk) === show[1]);
      return { status: 200, body: { following: has(this.following), followed_by: has(this.followers), status: "ok" } };
    }

    const list = url.pathname.match(/^\/api\/v1\/friendships\/\d+\/(following|followers)\/$/);
    if (list) {
      // hidden: accounts the list omits although the relationship exists (seen live).
      const all = this[list[1]].filter((u) => !this.hidden.has(u.pk));
      const start = Number(url.searchParams.get("max_id") || 0);
      const end = start + this.pageSize;
      return {
        status: 200,
        body: {
          users: all.slice(start, end),
          next_max_id: end < all.length ? String(end) : null,
          status: "ok",
        },
      };
    }

    return { status: 404, body: {} };
  }
}

/**
 * Loads the script in a fresh fake page. `storage` is a Map shared between
 * pages to simulate a reload of the same browser profile.
 */
function loadScript({
  instagram = new FakeInstagram(),
  userId = "100",
  storage = new Map(),
  hostname = "www.instagram.com",
  popups = Infinity,
  quota = Infinity,
} = {}) {
  const page = { storage, requests: [], downloads: [], opened: [], messages: [], tables: [], alerts: [] };
  let popupsLeft = popups;

  const record = (level) => (...args) => {
    page.messages.push({ level, text: args.map(String).join(" ") });
  };

  const element = () => ({
    style: {},
    innerHTML: "",
    querySelector: () => ({}),
    remove() {},
    click() {
      page.downloads.push({ name: this.download, text: this.href.text, type: this.href.type });
    },
  });

  const context = {
    console: {
      log: record("log"),
      warn: record("warn"),
      error: record("error"),
      group: record("log"),
      groupEnd() {},
      table: (rows) => page.tables.push(JSON.parse(JSON.stringify(rows))),
    },
    document: {
      cookie: userId ? `csrftoken=x; ds_user_id=${userId}; other=y` : "other=y",
      createElement: element,
      body: { appendChild() {} },
    },
    location: { hostname },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem(k, v) {
        if (String(v).length > quota) throw new Error("QuotaExceededError");
        storage.set(k, String(v));
      },
      removeItem: (k) => storage.delete(k),
    },
    fetch: async (url, init) => {
      page.requests.push({ url, init });
      const { status, body } = instagram.respond(new URL(url));
      return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: () => null },
        text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
      };
    },
    URL: { createObjectURL: (blob) => blob, revokeObjectURL() {} },
    Blob: class {
      constructor(parts, options) {
        this.text = parts.join("");
        this.type = options.type;
      }
    },
    alert: (m) => page.alerts.push(m),
    open: (url) => {
      page.opened.push(url);
      return popupsLeft-- > 0 ? {} : null;
    },
    // Long timers (the script revokes download URLs after 5s) must not keep the test run alive.
    setTimeout: (fn, ms) => {
      const timer = setTimeout(fn, ms);
      if (ms >= 1000) timer.unref();
      return timer;
    },
    clearTimeout,
    AbortController,
    URLSearchParams,
  };
  context.window = context;

  vm.createContext(context);
  vm.runInContext(SOURCE, context, { filename: "ig-who-unfollowed.js" });

  page.IU = context.IU;
  if (page.IU) {
    // No waiting between requests: the pauses are not what the tests are about.
    for (const key of Object.keys(page.IU.config))
      if (/DELAY|BREAK_M|GAP|BACKOFF/.test(key)) page.IU.config[key] = 0;
  }
  page.said = (level, pattern) =>
    page.messages.some((m) => m.level === level && pattern.test(m.text));
  return page;
}

/** Usernames of a list produced inside the fake page, as a plain array. */
const names = (list) => Array.from(list, (u) => u.username);

module.exports = { FakeInstagram, loadScript, names, user, SOURCE };
