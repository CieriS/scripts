# Instagram script

`ig-who-unfollowed.js` is a browser-console script that compares the accounts you follow with the accounts that follow you.
No extension, no dependencies, no server: it talks only to `www.instagram.com` using the
session of the tab it runs in.

## Usage

1. Open <https://www.instagram.com/> and log in.
2. Open the browser console (`Cmd+Opt+J` in Chrome on macOS).
3. Paste the whole file and press Enter.
4. Call the commands on the global `IU` object, starting with `IU.scan()`.

Comments and console messages are in Italian.

## How it works

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
