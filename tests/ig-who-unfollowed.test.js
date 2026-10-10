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
