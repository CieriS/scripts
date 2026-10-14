"use strict";
// Tests for instagram/ig-who-unfollowed.js, run with `node --test tests/`.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { FakeInstagram, loadScript, names, user, SOURCE } = require("./fake-browser.js");

/** Parses the CSV export back into rows of cells (quoted or bare, "" as escaped quote). */
function parseCsv(text) {
  const rows = [];
  for (const line of text.replace(/^\uFEFF/, "").split("\r\n")) {
    const cells = [];
    const cell = /"((?:[^"]|"")*)"|([^,]*)/y;
    let at = 0;
    while (at <= line.length) {
      cell.lastIndex = at;
      const m = cell.exec(line);
      cells.push(m[1] !== undefined ? m[1].replace(/""/g, '"') : m[2]);
      at = cell.lastIndex + 1; // skip the comma
    }
    rows.push(cells);
  }
  return rows;
}

test("csv: values a spreadsheet would run as formulas are turned into text", async () => {
  const instagram = new FakeInstagram({
    following: [
      user(1, "plain", { full_name: "Mario Rossi" }),
      user(2, "formula", { full_name: '=HYPERLINK("http://example.invalid","x")' }),
      user(3, "plus", { full_name: "+39 000" }),
      user(4, "minus", { full_name: "-1+1" }),
      user(5, "at", { full_name: "@SUM(A1)" }),
      user(6, "quote", { full_name: 'say "hi", ok' }),
    ],
    followers: [],
  });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.csv();

  const [file] = page.downloads;
  assert.match(file.name, /^ig-\d+\.csv$/);
  assert.equal(file.type, "text/csv;charset=utf-8");
  assert.ok(file.text.startsWith("﻿"), "starts with a BOM so Excel reads it as UTF-8");

  const rows = parseCsv(file.text);
  const fullName = Object.fromEntries(rows.slice(1).map((r) => [r[1], r[2]]));
  assert.equal(fullName.plain, "Mario Rossi");
  assert.equal(fullName.formula, `'=HYPERLINK("http://example.invalid","x")`);
  assert.equal(fullName.plus, "'+39 000");
  assert.equal(fullName.minus, "'-1+1");
  assert.equal(fullName.at, "'@SUM(A1)");
  assert.equal(fullName.quote, 'say "hi", ok');
});

test("csv: one header and one row per account, grouped by category", async () => {
  const instagram = new FakeInstagram({
    following: [user(1), user(2)],
    followers: [user(2), user(3)],
  });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.csv();

  const rows = parseCsv(page.downloads[0].text);
  assert.deepEqual(rows[0], ["categoria", "username", "nome", "privato", "verificato", "url"]);
  assert.deepEqual(
    rows.slice(1).map((r) => [r[0], r[1], r[5]]),
    [
      ["non_ti_segue", "user1", "https://www.instagram.com/user1/"],
      ["non_ricambi", "user3", "https://www.instagram.com/user3/"],
      ["reciproco", "user2", "https://www.instagram.com/user2/"],
    ]
  );
});

test("csv: nothing is downloaded before a scan", () => {
  const page = loadScript();
  page.IU.csv();
  assert.equal(page.downloads.length, 0);
  assert.ok(page.said("warn", /IU\.scan\(\)/));
});

test("the script is read-only: no request carries a method or a body", async () => {
  const instagram = new FakeInstagram({ following: [user(1), user(2), user(3)], followers: [user(1)] });
  const page = loadScript({ instagram });
  await page.IU.scan();

  assert.ok(page.requests.length > 0);
  for (const { url, init } of page.requests) {
    assert.ok(url.startsWith("https://www.instagram.com/api/v1/"), url);
    assert.equal(init.method, undefined);
    assert.equal(init.body, undefined);
  }
  // The only "method:" in the file is the header comment telling the reader to search for it.
  assert.equal(SOURCE.split("method:").length - 1, 1, "the source never sets a request method");
});

/* ---------------------------------------------------------------- scan --- */

const range = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => user(from + i));

test("scan: splits accounts into not following back, not followed back and mutual", async () => {
  const instagram = new FakeInstagram({
    following: [user(1), user(2), user(3), user(4), user(5)],
    followers: [user(4), user(5), user(6)],
  });
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.deepEqual(names(result.notFollowingBack), ["user1", "user2", "user3"]);
  assert.deepEqual(names(result.youDontFollowBack), ["user6"]);
  assert.deepEqual(names(result.mutual), ["user4", "user5"]);
  assert.deepEqual({ ...result.counts }, { following: 5, followers: 3 });
  assert.equal(result.partial, false);
});

test("scan: an account repeated across pages is counted once", async () => {
  const instagram = new FakeInstagram({
    following: [user(1), user(2), user(2), user(3), user(1)],
    followers: [user(3), user(3), user(3)],
  });
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.deepEqual({ ...result.counts }, { following: 3, followers: 1 });
  assert.deepEqual(names(result.notFollowingBack), ["user1", "user2"]);
  assert.equal(result.partial, false);
});

test("scan: empty lists give empty results", async () => {
  const page = loadScript({ instagram: new FakeInstagram() });
  const result = await page.IU.scan();

  assert.deepEqual({ ...result.counts }, { following: 0, followers: 0 });
  assert.equal(result.notFollowingBack.length, 0);
});

test("scan: a followers list shorter than the profile counter is flagged as incomplete", async () => {
  const instagram = new FakeInstagram({ following: range(1, 40), followers: range(1, 40) });
  // Instagram stops after the first page although the profile says 40 followers.
  instagram.override = (url) =>
    url.pathname.includes("/followers/")
      ? { status: 200, body: { users: range(1, 10), next_max_id: null } }
      : null;
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.equal(result.partial, true);
  assert.deepEqual({ ...result.expected }, { following: 40, followers: 40 });
  assert.ok(page.said("warn", /liste incomplete: letti 40\/40 seguiti e 10\/40 follower/));
});

test("scan: a small gap from the profile counter is tolerated", async () => {
  // Counters include deactivated accounts, lists do not.
  const instagram = new FakeInstagram({ following: range(1, 200), followers: range(1, 200) });
  instagram.profile = { follower_count: 203, following_count: 204 };
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.equal(result.partial, false);
  assert.ok(!page.said("warn", /liste incomplete/));
});

test("scan: without profile counters it completes and says it could not verify", async () => {
  const instagram = new FakeInstagram({ following: [user(1)], followers: [user(1)] });
  instagram.profile = null;
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.equal(result.partial, false);
  assert.equal(result.expected, null);
  assert.deepEqual(names(result.mutual), ["user1"]);
  assert.ok(page.said("warn", /non posso verificare/));
});

test("scan: a block signal stops everything and saves no snapshot", async () => {
  const instagram = new FakeInstagram({ following: range(1, 6), followers: range(1, 6) });
  instagram.override = (url) =>
    url.searchParams.get("max_id") === "2"
      ? { status: 400, body: { message: "feedback_required", status: "fail" } }
      : null;
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.equal(result, undefined);
  assert.ok(page.said("error", /feedback_required/));
  assert.equal(page.storage.has("__iu_snaps_v2"), false);
  const requestsAfterStop = page.requests.length;
  assert.equal(requestsAfterStop, 3, "profile, first page, blocked page: nothing after the block");
});

test("scan: an expired session is reported and saves no snapshot", async () => {
  const instagram = new FakeInstagram({ following: [user(1)], followers: [] });
  instagram.override = () => ({ status: 401, body: {} });
  const page = loadScript({ instagram });

  assert.equal(await page.IU.scan(), undefined);
  assert.ok(page.said("error", /sessione scaduta/));
  assert.equal(page.storage.has("__iu_snaps_v2"), false);
});

test("scan: refuses to start without a logged-in user", async () => {
  const page = loadScript({ userId: null });

  assert.equal(await page.IU.scan(), undefined);
  assert.equal(page.requests.length, 0);
  assert.ok(page.said("error", /ds_user_id/));
});

test("scan: a second scan while one is running is refused", async () => {
  const instagram = new FakeInstagram({ following: range(1, 6), followers: range(1, 6) });
  const page = loadScript({ instagram });
  const first = page.IU.scan();
  const second = await page.IU.scan();
  const result = await first;

  assert.equal(second, undefined);
  assert.ok(page.said("warn", /già in corso/));
  assert.deepEqual({ ...result.counts }, { following: 6, followers: 6 });
});

test("the script only installs itself on www.instagram.com", () => {
  const page = loadScript({ hostname: "example.invalid" });

  assert.equal(page.IU, undefined);
  assert.equal(page.alerts.length, 1);
});

/* ---------------------------------------------------------- checkpoint --- */

const CK_FOLLOWERS = "__iu_ck_v2_followers";
const savedUser = (id) => ({ id: String(id), username: `user${id}`, full_name: "", is_private: false, is_verified: false });
const checkpoint = (fields) =>
  new Map([[CK_FOLLOWERS, JSON.stringify({ uid: "100", t: Date.now(), users: [savedUser(1), savedUser(2)], cursor: "2", ...fields })]]);

test("checkpoint: a scan interrupted moments ago resumes from where it stopped", async () => {
  const instagram = new FakeInstagram({ following: [], followers: range(1, 5) });
  const page = loadScript({ instagram, storage: checkpoint({}) });
  const result = await page.IU.scan();

  const followerRequests = page.requests.filter((r) => r.url.includes("/followers/"));
  assert.ok(followerRequests[0].url.includes("max_id=2"), "first request continues from the saved cursor");
  assert.equal(result.counts.followers, 5);
  assert.equal(page.storage.has(CK_FOLLOWERS), false, "checkpoint removed once the list is complete");
});

test("checkpoint: an old interrupted scan is discarded, not mixed with new data", async () => {
  // user1 and user2 followed at the time of the old scan and no longer do.
  const instagram = new FakeInstagram({ following: [user(1)], followers: [user(7), user(8), user(9)] });
  const stale = checkpoint({ t: Date.now() - 2 * 60 * 60 * 1000 });
  const page = loadScript({ instagram, storage: stale });
  const result = await page.IU.scan();

  assert.deepEqual(names(result.youDontFollowBack), ["user7", "user8", "user9"]);
  assert.deepEqual(names(result.notFollowingBack), ["user1"]);
});

test("checkpoint: one written by another account is discarded", async () => {
  const instagram = new FakeInstagram({ following: [], followers: [user(7), user(8), user(9)] });
  const page = loadScript({ instagram, storage: checkpoint({ uid: "999" }) });
  const result = await page.IU.scan();

  assert.deepEqual(names(result.youDontFollowBack), ["user7", "user8", "user9"]);
});

test("checkpoint: one in the old format, without account and time, is discarded", async () => {
  const instagram = new FakeInstagram({ following: [], followers: [user(7), user(8), user(9)] });
  const legacy = new Map([[CK_FOLLOWERS, JSON.stringify({ users: [savedUser(1)], cursor: "2" })]]);
  const page = loadScript({ instagram, storage: legacy });
  const result = await page.IU.scan();

  assert.deepEqual(names(result.youDontFollowBack), ["user7", "user8", "user9"]);
});

/* ---------------------------------------------------------------- diff --- */

/** Runs two scans on the same browser profile, changing the lists in between. */
async function twoScans(before, after, options = {}) {
  const instagram = new FakeInstagram(before);
  const page = loadScript({ instagram, ...options });
  await page.IU.scan();
  Object.assign(instagram, after);
  await page.IU.scan();
  return page;
}

test("diff: tells who unfollowed, who vanished and who is new", async () => {
  const page = await twoScans(
    {
      following: [user(1), user(2), user(3), user(4)],
      followers: [user(1), user(2), user(3), user(5)],
    },
    {
      // user1 stopped following; user2 is gone from both lists; user5 was never
      // followed and left; user6 is new; I unfollowed user4 and followed user7.
      following: [user(1), user(3), user(7)],
      followers: [user(3, "user3_renamed"), user(6)],
    }
  );
  const d = page.IU.diff();

  assert.deepEqual(names(d.unfollowedYou).sort(), ["user1", "user5"]);
  assert.deepEqual(names(d.vanished), ["user2"]);
  assert.deepEqual(names(d.newFollowers), ["user6"]);
  assert.deepEqual(names(d.youUnfollowed), ["user4"]);
  assert.deepEqual(names(d.youFollowed), ["user7"]);
  assert.deepEqual({ ...d.delta }, { followers: -2, following: -1 });
  assert.equal(d.unreliable, false);
});

test("diff: nothing changed means empty lists", async () => {
  const lists = { following: [user(1)], followers: [user(1), user(2)] };
  const page = await twoScans(lists, lists);
  const d = page.IU.diff();

  assert.equal(d.unfollowedYou.length + d.vanished.length + d.newFollowers.length, 0);
  assert.ok(page.said("log", /Nessuno ti ha tolto il follow/));
});

test("diff: needs two scans", async () => {
  const page = loadScript({ instagram: new FakeInstagram({ following: [user(1)], followers: [] }) });
  await page.IU.scan();

  assert.equal(page.IU.diff(), null);
  assert.ok(page.said("warn", /almeno 2 scan/));
});

test("diff: a comparison with an incomplete scan is marked as unreliable", async () => {
  const instagram = new FakeInstagram({ following: range(1, 40), followers: range(1, 40) });
  const page = loadScript({ instagram });
  await page.IU.scan();
  instagram.override = (url) =>
    url.pathname.includes("/followers/")
      ? { status: 200, body: { users: range(1, 10), next_max_id: null } }
      : null;
  await page.IU.scan();
  const d = page.IU.diff();

  assert.equal(d.unreliable, true);
  assert.equal(d.unfollowedYou.length, 30, "the missing accounts look like unfollows: hence the warning");
  assert.ok(page.said("warn", /uno dei due scan era incompleto/));
});

test("diff: snapshots of another account in the same browser are not compared", async () => {
  const storage = new Map();
  const first = loadScript({
    instagram: new FakeInstagram({ following: [user(1)], followers: [user(1), user(2)] }),
    userId: "100",
    storage,
  });
  await first.IU.scan();

  const second = loadScript({
    instagram: new FakeInstagram({ following: [user(5)], followers: [user(5)] }),
    userId: "200",
    storage,
  });
  await second.IU.scan();

  assert.equal(second.IU.diff(), null, "one scan per account: nothing to compare");
  assert.equal(JSON.parse(storage.get("__iu_snaps_v2")).length, 2, "both snapshots are kept");
});

test("diff: snapshots saved by the previous version of the script still count", async () => {
  const legacy = new Map([[
    "__iu_snaps_v2",
    JSON.stringify([{ t: "2026-01-01T10:00:00.000Z", fg: [], fr: [["1", "user1"], ["2", "user2"]] }]),
  ]]);
  const page = loadScript({
    instagram: new FakeInstagram({ following: [], followers: [user(1)] }),
    storage: legacy,
  });
  await page.IU.scan();
  const d = page.IU.diff();

  assert.deepEqual(names(d.unfollowedYou), ["user2"]);
});

test("diff: indices can be given in any order and invalid ones are rejected", async () => {
  const page = await twoScans(
    { following: [], followers: [user(1), user(2)] },
    { following: [], followers: [user(1)] }
  );
  // Snapshots written in the same millisecond would be indistinguishable by time.
  const snaps = JSON.parse(page.storage.get("__iu_snaps_v2"));
  snaps[0].t = "2026-01-01T10:00:00.000Z";
  page.storage.set("__iu_snaps_v2", JSON.stringify(snaps));

  assert.deepEqual(names(page.IU.diff(-1, -2).unfollowedYou), ["user2"], "newest first is reordered");
  assert.equal(page.IU.diff(-1, -1), null);
  assert.equal(page.IU.diff(-9, -1), null);
  assert.ok(page.said("warn", /indici snapshot non validi/));
});

/* ----------------------------------------------------------- snapshots --- */

test("snapshots: only the most recent ones of the account are kept", async () => {
  const instagram = new FakeInstagram({ following: [], followers: [user(1)] });
  const page = loadScript({ instagram });
  page.IU.config.MAX_HISTORY = 3;
  for (let i = 0; i < 5; i++) await page.IU.scan();

  assert.equal(JSON.parse(page.storage.get("__iu_snaps_v2")).length, 3);
  assert.equal(page.IU.history().length, 3);
});

test("snapshots: when the browser storage is full the scan says the snapshot was not saved", async () => {
  const instagram = new FakeInstagram({ following: [], followers: range(1, 30) });
  const page = loadScript({ instagram, quota: 200 });
  const result = await page.IU.scan();

  assert.equal(result.counts.followers, 30, "the results are still available in memory");
  assert.ok(page.said("warn", /snapshot NON è stato salvato/));
  assert.equal(page.storage.has("__iu_snaps_v2"), false);
});

/* ---------------------------------------------------------- keep list --- */

test("keep: kept accounts disappear from the report, whatever the spelling", async () => {
  const instagram = new FakeInstagram({ following: [user(1, "Alice"), user(2, "bob"), user(3, "carol")], followers: [] });
  const page = loadScript({ instagram });
  await page.IU.scan();

  assert.deepEqual(Array.from(page.IU.keep("@ALICE", ["bob "])), ["alice", "bob"]);
  assert.deepEqual(names(page.IU.report()), ["carol"]);
  assert.deepEqual(Array.from(page.IU.unkeep("Bob")), ["alice"]);
  assert.deepEqual(names(page.IU.report()), ["bob", "carol"]);
  assert.deepEqual(Array.from(page.IU.keeplist()), ["alice"]);
});

/* ---------------------------------------------------------------- html --- */

test("html: profile names cannot inject markup into the report", async () => {
  const instagram = new FakeInstagram({
    following: [user(1, "mallory", { full_name: '<img src=x onerror="alert(1)">' })],
    followers: [],
  });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.html();

  const [file] = page.downloads;
  assert.equal(file.type, "text/html");
  assert.ok(!file.text.includes("<img"));
  assert.ok(file.text.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
  assert.ok(file.text.includes('href="https://www.instagram.com/mallory/"'));
});

test("html: an incomplete scan is stated in the report", async () => {
  const instagram = new FakeInstagram({ following: range(1, 40), followers: range(1, 40) });
  instagram.override = (url) =>
    url.pathname.includes("/followers/")
      ? { status: 200, body: { users: range(1, 10), next_max_id: null } }
      : null;
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.html();

  assert.ok(page.downloads[0].text.includes("Liste incomplete"));
});

test("json: exports the result together with the last comparison", async () => {
  const page = await twoScans(
    { following: [], followers: [user(1), user(2)] },
    { following: [], followers: [user(1)] }
  );
  const messagesBefore = page.messages.length;
  page.IU.json();

  const exported = JSON.parse(page.downloads[0].text);
  assert.deepEqual(exported.diff.unfollowedYou.map((u) => u.username), ["user2"]);
  assert.equal(exported.counts.followers, 1);
  assert.equal(page.messages.length, messagesBefore + 1, "only the 'downloaded' line is printed");
});

/* ---------------------------------------------------------------- open --- */

const profile = (url) => url.split("/")[3];

test("open: each call continues with the next profiles instead of reopening the same", async () => {
  const instagram = new FakeInstagram({ following: range(1, 5), followers: [] });
  const page = loadScript({ instagram });
  await page.IU.scan();

  page.IU.open(2);
  page.IU.open(2);
  page.IU.open(2);
  assert.deepEqual(page.opened.map(profile), ["user1", "user2", "user3", "user4", "user5"]);

  page.IU.open(2);
  assert.equal(page.opened.length, 5);
  assert.ok(page.said("warn", /già aperti tutti/));
});

test("open: a new scan starts again from the top of the list", async () => {
  const instagram = new FakeInstagram({ following: range(1, 3), followers: [] });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.open(1);
  await page.IU.scan();
  page.IU.open(1);

  assert.deepEqual(page.opened.map(profile), ["user1", "user1"]);
});

test("open: tabs blocked by the browser are reported and retried next time", async () => {
  const instagram = new FakeInstagram({ following: range(1, 3), followers: [] });
  const page = loadScript({ instagram, popups: 1 });
  await page.IU.scan();

  page.IU.open(3);
  assert.ok(page.said("warn", /ha bloccato 2 schede/));
  assert.ok(page.said("log", /aperti 1 profili, ne restano 2/));

  page.IU.open(1);
  assert.equal(profile(page.opened.at(-1)), "user2", "the blocked profile is tried again");
});

test("open: skips kept accounts and never opens more than ten tabs", async () => {
  const instagram = new FakeInstagram({ following: range(1, 15), followers: [] });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.keep("user1");
  page.IU.open(50);

  assert.equal(page.opened.length, 10);
  assert.equal(profile(page.opened[0]), "user2");
});

test("open: nothing to open is said, not silently ignored", async () => {
  const instagram = new FakeInstagram({ following: [user(1)], followers: [user(1)] });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.open();

  assert.equal(page.opened.length, 0);
  assert.ok(page.said("warn", /niente da aprire/));
});

/* -------------------------------------------------------------- reload --- */

test("reload: the last scan is available again without new requests", async () => {
  const instagram = new FakeInstagram({
    following: [user(1, "alice", { full_name: "Alice" }), user(2)],
    followers: [user(2), user(3)],
  });
  const before = loadScript({ instagram });
  await before.IU.scan();

  const after = loadScript({ instagram, storage: before.storage });
  assert.deepEqual(names(after.IU.report()), ["alice"]);
  assert.equal(after.IU.last.restored, true);
  assert.deepEqual(names(after.IU.last.youDontFollowBack), ["user3"]);
  assert.equal(after.requests.length, 0);
  assert.ok(after.said("log", /ricostruita dall'ultimo snapshot/));

  after.IU.csv();
  assert.ok(after.downloads[0].text.includes('"non_ti_segue","alice","","","","https://www.instagram.com/alice/"'));
});

test("reload: an incomplete scan is still flagged after being restored", async () => {
  const instagram = new FakeInstagram({ following: range(1, 40), followers: range(1, 40) });
  instagram.override = (url) =>
    url.pathname.includes("/followers/")
      ? { status: 200, body: { users: range(1, 10), next_max_id: null } }
      : null;
  const before = loadScript({ instagram });
  await before.IU.scan();

  const after = loadScript({ instagram, storage: before.storage });
  after.IU.report();
  assert.equal(after.IU.last.partial, true);
  assert.ok(after.said("warn", /liste incomplete/));
});

test("reload: another account does not see the previous one's scan", async () => {
  const instagram = new FakeInstagram({ following: [user(1)], followers: [] });
  const before = loadScript({ instagram, userId: "100" });
  await before.IU.scan();

  const after = loadScript({ instagram, userId: "200", storage: before.storage });
  assert.equal(after.IU.last, null);
  assert.equal(after.IU.report(), undefined);
  assert.ok(after.said("warn", /IU\.scan\(\)/));
});

test("reset: clears snapshots, keep list and the restored result", async () => {
  const instagram = new FakeInstagram({ following: [user(1)], followers: [] });
  const page = loadScript({ instagram });
  await page.IU.scan();
  page.IU.keep("user1");
  page.IU.reset();

  assert.equal(page.IU.last, null);
  assert.equal(page.storage.size, 0);
});

test("scan: a rate-limited profile counter is tried once and does not stop the scan", async () => {
  const instagram = new FakeInstagram({ following: [user(1), user(2)], followers: [user(1)] });
  instagram.override = (url) => (url.pathname.endsWith("/info/") ? { status: 429, body: {} } : null);
  const page = loadScript({ instagram });
  const result = await page.IU.scan();

  assert.deepEqual(names(result.notFollowingBack), ["user2"]);
  assert.equal(page.requests.filter((r) => r.url.endsWith("/info/")).length, 1, "no retries");
  assert.ok(page.said("warn", /non posso verificare/));
  assert.ok(!page.said("warn", /HTTP 429/));
});

test("scan: a block signal on the profile counter still stops everything", async () => {
  const instagram = new FakeInstagram({ following: [user(1)], followers: [] });
  instagram.override = (url) =>
    url.pathname.endsWith("/info/") ? { status: 400, body: { message: "challenge_required" } } : null;
  const page = loadScript({ instagram });

  assert.equal(await page.IU.scan(), undefined);
  assert.equal(page.requests.length, 1);
  assert.ok(page.said("error", /challenge_required/));
});
