# scripts

Small personal automation scripts, grouped by area. Each script is self-contained: no
dependencies to install and nothing to build.

| Area | Folder | What is inside |
|---|---|---|
| Shell utilities | [`sh/`](sh/) | Bash helpers for the local filesystem |
| Instagram | [`instagram/`](instagram/) | Read-only browser-console script to see who does not follow back |

## Where to look

| You want to | Go to |
|---|---|
| Find which script does what | [Structure](#structure) |
| Run the shell helper | [Shell utilities](#shell-utilities) |
| Run the Instagram script | [`instagram/README.md`](instagram/README.md) |
| Check your changes or add a script | [Development](#development) |
| Understand why the repository is shaped this way | [Decisions](#decisions) |
| Know what is not covered yet | [Known limitations](#known-limitations) |

## Structure

```
.
├── sh/
│   └── existingFileWithString.sh   # list the files of a directory that contain a string
├── instagram/
│   ├── README.md                   # usage, commands, how to read the results
│   └── ig-who-unfollowed.js        # read-only scan, snapshots and diff between scans
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

`ig-who-unfollowed.js` is pasted in the browser console on `instagram.com` while logged in. It
only sends GET requests, so it cannot change the account. Usage, commands and how to read the
results are in [`instagram/README.md`](instagram/README.md).

## Development

```bash
./check.sh
```

Runs `shellcheck` on every tracked `*.sh`, `node --check` on every tracked `*.js` and the tests
in `tests/*.test.sh`. CI runs the same command on pushes to `development` and on pull requests.

Requirements: `bash`, `shellcheck`, `node`.

Changes go on a branch and reach `development`, the default branch, through a pull request.

Adding a script:

1. Put it in the folder of its area (create a new top-level folder for a new area).
2. Start the file with a comment that says what it does and how to run it.
3. Add a `tests/<name>.test.sh` for anything that can run outside a browser.
4. Add it to the structure above and document it in this README, or in the area's own
   `README.md` once the area has more than one script.
5. Never commit personal data: usernames, exports, tokens or cookies.

## Decisions

- **Grouped by area, not by language.** A folder is a topic (`instagram/`); `sh/` holds the
  generic shell helpers that belong to no topic yet.
- **One file per script, no build step.** The Instagram script is pasted whole into the
  browser console, so it must work alone: no modules, no bundling.
- **Read-only.** `ig-who-unfollowed.js` has no write request at all. An earlier version that
  could also unfollow (`ig-unfollowers.js`) was removed; unfollowing is done by hand from the
  profiles the script opens.
- **Own code only.** The third-party console tool these scripts replaced is not in the
  repository.
- **A single check command.** `check.sh` is what runs locally and in CI, so the two cannot
  drift apart. Tests are plain Bash with no framework, in line with the no-dependency rule.
- **GitHub Actions pinned by commit SHA**, updated by Dependabot.

## Known limitations

- The Instagram scripts are covered only by a syntax check: their logic lives inside a
  browser-only closure and has no automated tests.
- The CSV exports quote every value but do not neutralise values that start with `=`, `+`, `-`
  or `@`, which a spreadsheet may read as formulas.
- The scripts rely on Instagram's private web endpoints, which can change without notice.
- There is no licence file yet.
