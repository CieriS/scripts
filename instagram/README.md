# Instagram script

`ig-who-unfollowed.js` is a browser-console script that compares the accounts you follow with
the accounts that follow you, and tells who stopped following between two scans. no dependencies, no server: it talks only to `www.instagram.com` using the session of the tab
it runs in.

## Usage

1. Open <https://www.instagram.com/> and log in.
2. Open the browser console (`Cmd+Opt+J` in Chrome on macOS).
3. Paste the whole file and press Enter.
4. Call the commands on the global `IU` object, starting with `IU.scan()`.

Comments and console messages are in Italian.

## Commands

| Command | What it does |
|---|---|
| `IU.scan()` | Read following and followers, save a snapshot, print the report |
| `IU.scan({ follower: N, seguiti: N })` | Same, and check the lists against the numbers shown on your profile |
| `IU.diff()` | Who unfollowed since the previous scan; `IU.diff(-3, -1)` compares other snapshots |
| `IU.report()` | Who does not follow back, from the last scan |
| `IU.html()` | Clickable HTML report |
| `IU.csv()` / `IU.json()` | Export the results |
| `IU.open(n)` | Open the next `n` profiles (10 at most) in tabs, to act by hand |
| `IU.keep(...users)` / `IU.unkeep(...users)` | Add to / remove from the "keep anyway" list, hidden from reports |
| `IU.keeplist()` | Show the keep list |
| `IU.history()` | List the saved snapshots and whether each one is complete |
| `IU.stop()` | Interrupt a running scan |
| `IU.reset()` | Delete all saved state |

## How it works

- **Read-only by construction.** The file contains no write request: every call is a GET.
- **Snapshots.** Each scan is saved in the tab's `localStorage` as a compact list of account ids
  and usernames, eight per account at most. `IU.diff()` compares two of them, so it needs at
  least two scans; one a week is enough.
- **Per account.** Snapshots are tagged with the logged-in account. Switching account in the
  same browser does not mix them.
- **After a reload** the last snapshot is loaded back, so `report()`, `html()`, `csv()` and
  `open()` work without a new scan. Full names and the private/verified flags are not stored
  and stay empty until the next scan.
- **Interrupted scans** resume from where they stopped only within 30 minutes; after that they
  start again, because older partial data would be mixed with new data.

## Reading the results

| In `IU.diff()` | Meaning |
|---|---|
| Ti hanno tolto il follow | Was among your followers, no longer is |
| Spariti | Gone from your followers *and* from the accounts you follow: usually a closed account or a block |
| Nuovi follower | Follows you now, did not at the previous scan |
| Hai smesso / iniziato a seguire | Your own changes |

Things the script cannot know:

- An account you did not follow that closes or blocks you looks the same as an unfollow.
- An account you unfollowed that also unfollowed you is listed under "spariti".

### Accounts Instagram leaves out

Instagram's lists are not stable: two scans a few hours apart can each leave out a different
handful of accounts that are still there (seen live with a 585-account following list, where
the counter matched both times). Compared naively, those gaps look like unfollows and new
follows. Two checks prevent that:

- **After each scan**, every account that was in the previous snapshot and is now missing is
  checked one by one with the same read-only request the profile page makes. If the
  relationship still exists the account is put back. With more than 30 missing accounts the
  check is skipped (that is a truncated list, not a few gaps) and the comparison is flagged.
  If Instagram refuses a check, the scan stops checking, completes, and says how many accounts
  are unverified.
- **In `IU.diff()`**, an account that is "new" but already appeared in an older snapshot was
  only missing from the previous one: it is counted as reappeared and not listed as new.

### Snapshots from an older version

Snapshots saved before v1.0.1 carry no version mark. The version that wrote most of them could
store mixed or truncated lists, so a comparison with one of them can list long-standing
followers as "nuovi follower" and old follows as "hai iniziato a seguire". `IU.diff()` and the
HTML report say so, and `IU.history()` shows the version of each snapshot. From the second scan
made with the current version on, the default comparison uses only current snapshots.

### Incomplete lists

Instagram sometimes returns a list shorter than the real one. Every missing account would look
like someone who does not follow you and, at the next scan, like an unfollow. To catch this,
pass the numbers shown on your profile:

```js
IU.scan({ follower: 1222, seguiti: 585 })
```

Either number can be left out. A gap of 2%, or 3 accounts, is tolerated: counters include
deactivated accounts, and the profile page may have been loaded a little before the scan. When
a list is too short:

- the scan and `report()` print "liste incomplete" with the numbers read and expected;
- the snapshot is marked, `history()` shows it as not complete, and any `diff()` that uses it
  warns that it may contain false unfollows.

Wait a few hours and scan again. The script does not ask Instagram for these numbers: the
endpoint that returns them answers HTTP 429 to the web session every time.

## Other notes

- `IU.open(n)` needs pop-ups allowed for `instagram.com`: blocked tabs are counted and opened
  on the next call.
- The CSV is UTF-8 with a BOM; values starting with `=`, `+`, `-` or `@` get a leading
  apostrophe so that a spreadsheet does not run them as formulas.
- Snapshots and the keep list live in the browser profile: they are lost if site data is
  cleared. `IU.json()` is the way to keep a copy.
- If Instagram answers with a block or verification signal the scan stops at once. Do not
  retry for a few hours.
