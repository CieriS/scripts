# devps

Which development processes are still running, for how long, what they use and whether they
expose ports. Read-only: it never stops anything; closing a process is left to `kill <PID>`.

```bash
uv run devps/devps.py    # or `devps`, the zsh function in the dotfiles repo
```

Standard library only. Needs `ps` and `lsof` (part of macOS); `docker` is optional: when it does
not answer, the container section says `OrbStack non attivo`.

## Sections

Each section is sorted by age, oldest first: what was forgotten is usually the oldest.

1. **Development processes**: PID, age, CPU, RAM, folder relative to `~/sc_workspace`, command, ports.
2. **Running containers**: name, image, uptime, CPU, RAM, published ports.
3. **Other TCP listeners**: anything listening that sections 1 and 2 do not cover.

Ports bound to anything other than `127.x` or `[::1]` are red: they are reachable from the network.
When a port is bound both locally and to the network, the network bind is the one shown. Red is
reserved for this. Age is yellow from one day on and green under one hour; the last lines give the
totals and how many ports are exposed.

## Selection rules

- **The working directory decides**, not the process name: name filters match `bundle` and
  `NodeService`. A process is selected when its directory is `~/sc_workspace` or below it.
- **Named exception: `playwright-mcp`** (`ORPHAN_PRONE_TOOLS`). It keeps running as an orphan after
  its session is closed, it recurs, and it can sit in `/` with no port, so no section would show it.
  This is one named exception, not a generic name filter. Only a whole command token matches, so a
  command that merely mentions the name does not count.
- **Excluded**: interactive shells with no script (a command they run appears on its own), `devps`
  with whatever launched it, and OrbStack's own process in section 1.
- **Grouping**: a selected process folds into its topmost selected ancestor, even across unselected
  wrappers, with CPU and RAM summed and the ancestor's age. A Claude session is therefore one line,
  including anything started from it, ports too. A child outside `~/sc_workspace` is not selected: it
  is not summed, and if it listens it appears in section 3.
- **OrbStack ports**: hidden from section 3 only if a container publishes exactly that address. A
  network bind no container declares stays visible.

## Limits

- No `sudo`: root's processes are not included (the output says so).
- Containers' own processes are visible only as OrbStack, so the total leaves OrbStack out: its
  memory already holds the containers'.
- CPU is the `ps` value, a decaying average rather than an instant reading.
- Container uptime comes from docker's rounded text (`Up About an hour`), so the order among
  containers of similar age is approximate.
