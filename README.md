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
| Know whether you may reuse the code | [Licence](#licence) |

## Structure

```
.
├── sh/
│   └── existingFileWithString.sh   # list the files of a directory that contain a string
├── instagram/
│   ├── README.md                   # usage, commands, how to read the results
│   └── ig-who-unfollowed.js        # read-only scan, snapshots and diff between scans
├── tests/
│   ├── existingFileWithString.test.sh   # Bash tests of the shell helper
│   ├── ig-who-unfollowed.test.js        # Node tests of the Instagram script
│   └── fake-browser.js                  # fake browser and fake Instagram used by the tests
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

Runs `shellcheck` on every tracked `*.sh`, `node --check` on every tracked `*.js`, the Bash
tests in `tests/*.test.sh` and the Node tests in `tests/*.test.js` (built-in `node --test`
runner, nothing to install). It only looks at tracked files: `git add` a new file first. CI
runs the same command on every push to `development` and on Dependabot's pull requests.

Requirements: `bash`, `shellcheck`, `node`.

Changes are made on a branch, checked with `./check.sh` and merged locally into `development`,
the default branch, with a fast-forward. Pull requests are used only by Dependabot.

Adding a script:

1. Put it in the folder of its area (create a new top-level folder for a new area).
2. Start the file with a comment that says what it does and how to run it.
3. Add tests: `tests/<name>.test.sh` for a shell script, `tests/<name>.test.js` for a browser
   script (see `tests/fake-browser.js` for how to run one under Node).
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
  drift apart. Tests are plain Bash or Node's built-in runner, in line with the no-dependency
  rule.
- **Browser scripts are tested as they are.** The tests load the real file into a fake page
  with fake Instagram endpoints and call its public commands, so the script needs no exports
  or test hooks.
- **Local fast-forward merges instead of pull requests**, so every commit on `development`
  carries the author's own identity.
- **GitHub Actions pinned by commit SHA**, updated by Dependabot.

## Known limitations

- The Instagram script is tested against fake endpoints only. It relies on Instagram's private
  web endpoints, which can change without notice and cannot be exercised in CI.
- Its completeness check depends on the profile counters: when Instagram does not return them,
  a truncated list cannot be detected (the scan says so).

## Licence

No licence is granted: the code is public to be read, not to be reused.
