# Instagram scripts

Browser-console scripts that compare the accounts you follow with the accounts that follow you.
No extension, no dependencies, no server: each file talks only to `www.instagram.com` using the
session of the tab it runs in.

## Which one to use

| File | Version | Writes to the account | Status |
|---|---|---|---|
| [`ig-who-unfollowed.js`](ig-who-unfollowed.js) | v2 | Never: GET requests only | **Current** |
| [`ig-unfollowers.js`](ig-unfollowers.js) | v1 | Only `IU.unfollow()`, dry-run by default | Kept for the unfollow feature |

## Usage

1. Open <https://www.instagram.com/> and log in.
2. Open the browser console (`Cmd+Opt+J` in Chrome on macOS).
3. Paste the whole file and press Enter.
4. Call the commands on the global `IU` object, starting with `IU.scan()`.

Both scripts define the same `IU` global: load one per tab. Comments and console messages are
in Italian.

## `ig-who-unfollowed.js` (v2)

Read-only by construction: the file contains no write request. Each scan is saved as a compact
snapshot in the tab's `localStorage`, so the next scan can tell who stopped following.

| Command | What it does |
|---|---|
| `IU.scan()` | Scan followers and following, save a snapshot |
| `IU.diff()` | Who unfollowed since the previous scan |
| `IU.report()` | Who does not follow back right now |
| `IU.html()` | Clickable HTML report |
| `IU.csv()` / `IU.json()` | Export the results |
| `IU.open(n)` | Open the first `n` profiles in tabs, to act by hand |
| `IU.keep(...users)` / `IU.unkeep(...users)` | Add to / remove from the "keep anyway" list, hidden from reports |
| `IU.keeplist()` | Show the keep list |
| `IU.history()` | List saved snapshots |
| `IU.stop()` | Interrupt a running scan |
| `IU.reset()` | Delete all saved state |

`IU.diff()` compares with the previous scan, so it needs at least two of them. The snapshots
and the keep list live in the browser profile: they are lost if site data is cleared.

## `ig-unfollowers.js` (v1)

Same read-only scan, without snapshots, plus an optional unfollow.

| Command | What it does |
|---|---|
| `IU.scan()` | Read-only scan |
| `IU.report()` | Results of the last scan |
| `IU.csv()` / `IU.json()` | Export the results |
| `IU.open(n)` | Open the first `n` profiles in tabs |
| `IU.budget()` | Remaining margin against the configured limits |
| `IU.unfollow()` | Unfollow the accounts that do not follow back |
| `IU.stop()` | Interrupt the running operation |
| `IU.reset()` | Delete the saved scan and counters |

`IU.unfollow()` is the only command that changes the account, and the only one with a real risk
of an action block. Settings are in the `CONFIG` object at the top of the file:

- `DRY_RUN` is `true` by default: it simulates without sending anything.
- `WHITELIST` holds usernames that are never unfollowed. Fill it locally and do not commit it.
- Limits are 15 per hour, 80 per day and 20 per session, with 45-90 s between unfollows and a
  5-10 min break every 8.

## Version history

1. **v1** `ig-unfollowers.js`: single readable file with rate limits, dry-run, whitelist and
   exports.
2. **v2** `ig-who-unfollowed.js`: unfollow removed entirely; adds snapshots, `diff()`, the HTML
   report and the keep list.
