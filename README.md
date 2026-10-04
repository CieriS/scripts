# scripts

Small personal automation scripts, grouped by area. Each script is self-contained: no
dependencies to install and nothing to build.

| Area | Folder | What is inside |
|---|---|---|
| Shell utilities | [`sh/`](sh/) | Bash helpers for the local filesystem |
| Instagram | [`instagram/`](instagram/) | Browser-console scripts to see who does not follow back |

## Structure

```
.
├── sh/
│   └── existingFileWithString.sh   # list the files of a directory that contain a string
├── instagram/
│   ├── README.md                   # usage, safety notes, version history
│   ├── ig-who-unfollowed.js        # v2, current: read-only
│   └── ig-unfollowers.js           # v1: scan + optional rate-limited unfollow
├── tests/
│   └── existingFileWithString.test.sh
├── check.sh                        # lint, syntax check and tests (also run by CI)
└── .github/                        # CI workflow and Dependabot
```

## Shell utilities

### `sh/existingFileWithString.sh`

Lists the files directly inside a directory (not recursive) that contain a given string and
prints how many files were scanned and how many matched. The string is matched literally, not
as a regular expression. Backslashes in the directory path are converted to `/`, so
Windows-style paths work from Git Bash. Output messages are in Italian.

```bash
bash sh/existingFileWithString.sh "TODO" /path/to/directory
```

| Exit code | Meaning |
|---|---|
| `0` | Scan completed, with or without matches |
| `1` | Directory not found |
| `2` | Wrong arguments: both are required and the string cannot be empty |

## Instagram

Scripts to paste in the browser console on `instagram.com` while logged in. Use
`ig-who-unfollowed.js` unless you have a reason not to: it only sends GET requests. Details,
commands and the history of the two versions are in [`instagram/README.md`](instagram/README.md).

## Development

```bash
./check.sh
```

Runs `shellcheck` on every tracked `*.sh`, `node --check` on every tracked `*.js` and the tests
in `tests/*.test.sh`. CI runs the same command on pushes to `development` and on pull requests.

Requirements: `bash`, `shellcheck`, `node`.

Adding a script:

1. Put it in the folder of its area (create a new top-level folder for a new area).
2. Start the file with a comment that says what it does and how to run it.
3. Add a `tests/<name>.test.sh` for anything that can run outside a browser.
4. Add it to the structure above and document it in this README, or in the area's own
   `README.md` once the area has more than one script.
5. Never commit personal data: usernames, exports, tokens or cookies.
