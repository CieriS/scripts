#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Show which development processes are running, for how long, what they use and what they expose.

Usage:
    uv run devps/devps.py

Read-only: it never stops anything. Three sections, oldest first: processes whose working
directory is under ~/sc_workspace (children folded into their selected ancestor), running
containers, other processes listening on TCP. Runs without sudo, so root's processes are not
included. Output messages are in Italian. See devps/README.md for the selection rules.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field

RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
MAX_RULE = 130

SHELLS = {"zsh", "bash", "sh", "fish", "dash"}

# Named exception to the working-directory rule, not a generic name filter: playwright-mcp keeps
# running as an orphan after its session is closed, recurs often, and may sit in "/" with no port,
# so no other section would ever show it.
ORPHAN_PRONE_TOOLS = ("playwright-mcp", "@playwright/mcp")

ORBSTACK_COMMAND = re.compile(r"^\S*/OrbStack\.app/")
UNITS_KB = {"B": 1 / 1024, "KIB": 1, "MIB": 1024, "GIB": 1024**2, "TIB": 1024**3,
            "KB": 1000 / 1024, "MB": 1000**2 / 1024, "GB": 1000**3 / 1024, "TB": 1000**4 / 1024}


@dataclass
class Proc:
    pid: int
    ppid: int
    age: int
    cpu: float
    rss_kb: int
    command: str


@dataclass
class Row:
    """One printed process line: a selected process with its selected descendants."""

    pid: int
    age: int
    cpu: float
    rss_kb: int
    command: str
    folder: str = "—"
    ports: list = field(default_factory=list)
    members: int = 1
    in_total: bool = True


@dataclass
class Container:
    name: str
    image: str
    status: str
    age: int
    ports: list
    cpu: float = 0.0
    rss_kb: int = 0


# --- parsing ---------------------------------------------------------------------------------


def parse_etime(text):
    """Seconds from the `[[dd-]hh:]mm:ss` format of ps."""
    days, _, clock = text.rpartition("-")
    seconds = 0
    for part in clock.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds + int(days or 0) * 86400


def parse_ps(text):
    """{pid: Proc} from `ps -o pid=,ppid=,etime=,pcpu=,rss=,command=`; malformed lines are skipped."""
    procs = {}
    for line in text.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            proc = Proc(int(parts[0]), int(parts[1]), parse_etime(parts[2]),
                        float(parts[3]), int(parts[4]), parts[5])
        except ValueError:
            continue
        procs[proc.pid] = proc
    return procs


def parse_lsof_cwd(text):
    """{pid: directory} from `lsof -d cwd -Fpn`."""
    cwds, pid = {}, None
    for line in text.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            cwds[pid] = line[1:]
    return cwds


def parse_lsof_listen(text):
    """{pid: [address]} from `lsof -iTCP -sTCP:LISTEN -nP -Fpn`."""
    listeners, pid = {}, None
    for line in text.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            listeners.setdefault(pid, []).append(line[1:])
    return listeners


def parse_up_status(status):
    """Approximate seconds from a docker status: only docker's rounded text is available here."""
    text = status.lower()
    if "less than" in text:
        return 0
    match = re.search(r"(\d+|about an?)\s+(second|minute|hour|day|week|month|year)", text)
    if not match:
        return 0
    amount = int(match[1]) if match[1].isdigit() else 1
    return amount * {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800,
                     "month": 2592000, "year": 31536000}[match[2]]


def parse_docker_ports(text):
    """Published host addresses from docker's Ports column; unpublished `9000/tcp` entries are skipped."""
    addresses = []
    for host, first, last in re.findall(r"(\S+?):(\d+)(?:-(\d+))?->", text):
        for port in range(int(first), int(last or first) + 1):
            addresses.append(f"{host}:{port}")
    return addresses


def parse_size_kb(text):
    """KiB from a docker size such as `9.41MiB`; unknown formats count as 0."""
    match = re.match(r"([\d.]+)\s*([A-Za-z]+)", text.strip())
    if not match or match[2].upper() not in UNITS_KB:
        return 0
    return round(float(match[1]) * UNITS_KB[match[2].upper()])


def parse_containers(ps_text, stats_text):
    """Containers from `docker ps` and `docker stats` in JSON lines; stats may be empty."""
    stats = {}
    for line in stats_text.splitlines():
        entry = json.loads(line)
        stats[entry["Name"]] = entry
    containers = []
    for line in ps_text.splitlines():
        entry = json.loads(line)
        usage = stats.get(entry["Names"], {})
        containers.append(Container(
            entry["Names"], entry["Image"], entry["Status"], parse_up_status(entry["Status"]),
            parse_docker_ports(entry.get("Ports", "")),
            float(usage.get("CPUPerc", "0%").rstrip("%") or 0),
            parse_size_kb(usage.get("MemUsage", "").split("/")[0]),
        ))
    return containers


# --- selection -------------------------------------------------------------------------------


def is_exposed(address):
    """Everything not bound to loopback is reachable from the network."""
    return not re.match(r"(127\.|\[::1\]:)", address)


def split_address(address):
    host, _, port = address.rpartition(":")
    return host, port


def merge_ports(addresses):
    """One address per port, keeping the exposed one so that a local bind never hides a network one."""
    by_port = {}
    for address in addresses:
        _, port = split_address(address)
        if port not in by_port or (is_exposed(address) and not is_exposed(by_port[port])):
            by_port[port] = address
    return sorted(by_port.values(), key=lambda a: int(split_address(a)[1]))


def is_under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def executable(command):
    return command.split()[0].rsplit("/", 1)[-1].lstrip("-")


def is_interactive_shell(command):
    """A shell with no script and no -c: whatever it is running shows up as its own process."""
    tokens = command.split()
    return executable(command) in SHELLS and all(t.startswith("-") and "c" not in t for t in tokens[1:])


def is_orphan_prone(command):
    """Whole-token match: a shell command or a config that merely mentions the name does not count."""
    if executable(command) in SHELLS:
        return False
    for token in command.split():
        if token.rsplit("/", 1)[-1] == ORPHAN_PRONE_TOOLS[0] or token.startswith(ORPHAN_PRONE_TOOLS[1]):
            return True
    return False


def is_orbstack(command):
    return bool(ORBSTACK_COMMAND.match(command))


def launcher_chain(procs, pid):
    """devps itself and whatever started it (python, uv, shells), up to the first other process."""
    chain = set()
    while pid in procs:
        name = executable(procs[pid].command).lower()
        if chain and name not in SHELLS and name != "uv" and not name.startswith("python"):
            break
        chain.add(pid)
        pid = procs[pid].ppid
    return chain


def select(procs, cwds, root, excluded=frozenset()):
    """Pids of the development processes: the working directory decides, plus the named exception."""
    selected = set()
    for pid, proc in procs.items():
        if pid in excluded or is_interactive_shell(proc.command) or is_orbstack(proc.command):
            continue
        if is_under(cwds.get(pid, ""), root) or is_orphan_prone(proc.command):
            selected.add(pid)
    return selected


def group_head(pid, procs, selected):
    """Topmost selected ancestor, looking through unselected ones (a wrapper may sit in between)."""
    head, seen = pid, set()
    while pid in procs and pid not in seen:
        seen.add(pid)
        if pid in selected:
            head = pid
        pid = procs[pid].ppid
    return head


def abbreviate(command, width=38):
    """Executable names without paths; a `wrapper -- real command` shows the real command."""
    text = command.replace("Application Support", "ApplicationSupport")  # paths with a known space
    if " -- " in text and executable(text) == "disclaimer":
        text = text.split(" -- ", 1)[1]
    text = " ".join(token if token.startswith("@") or token.endswith("/") else token.rsplit("/", 1)[-1]
                    for token in text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def relative_folder(cwd, root):
    if not cwd or not is_under(cwd, root):
        return "—"
    return cwd[len(root):].lstrip("/") or "."


def build_groups(procs, selected, cwds, listeners, root):
    """One Row per group, oldest first, with summed consumption and merged ports."""
    members = {}
    for pid in selected:
        members.setdefault(group_head(pid, procs, selected), []).append(pid)
    rows = []
    for head, pids in members.items():
        addresses = [address for pid in pids for address in listeners.get(pid, [])]
        rows.append(Row(head, procs[head].age,
                        sum(procs[pid].cpu for pid in pids),
                        sum(procs[pid].rss_kb for pid in pids),
                        abbreviate(procs[head].command),
                        relative_folder(cwds.get(head), root),
                        merge_ports(addresses), len(pids)))
    return sorted(rows, key=lambda row: -row.age)


def wildcard(address):
    """lsof shows `*:5432` where docker shows `0.0.0.0:5432` or `[::]:5432`."""
    host, port = split_address(address)
    return f"*:{port}" if host in ("*", "0.0.0.0", "[::]") else address


def other_listeners(procs, listeners, grouped, containers):
    """Listeners outside section 1. An OrbStack port is hidden only if a container publishes exactly
    that address: a network bind that no container declares must stay visible."""
    published = {wildcard(address) for container in containers for address in container.ports}
    rows = []
    for pid, addresses in listeners.items():
        if pid in grouped or pid not in procs:
            continue
        proc = procs[pid]
        orbstack = is_orbstack(proc.command)
        if orbstack:
            addresses = [a for a in addresses if wildcard(a) not in published]
        if addresses:
            # OrbStack's memory already holds the containers': summing it would count them twice
            rows.append(Row(pid, proc.age, proc.cpu, proc.rss_kb, abbreviate(proc.command),
                            ports=merge_ports(addresses), in_total=not orbstack))
    return sorted(rows, key=lambda row: -row.age)


# --- output ----------------------------------------------------------------------------------


def format_age(seconds):
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}g {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else f"{secs}s"


def format_ram(kb):
    return f"{kb / 1024**2:.1f} GB" if kb >= 1024**2 else f"{kb / 1024:.0f} MB"


def paint(text, color, width=0):
    """Pad before coloring: escape codes must not count in the column width. Negative width: right-align."""
    pad = " " * max(0, abs(width) - len(text))
    return f"{pad if width < 0 else ''}{color}{text}{RESET if color else ''}{pad if width > 0 else ''}"


def format_ports(addresses):
    """Network binds in red, local ones dim; the whole address is shown, since the bind is the point."""
    return " ".join(paint(a, RED if is_exposed(a) else DIM) for a in addresses) or paint("—", DIM)


def age_cell(seconds, width):
    """Yellow from one day on: what was forgotten must stand out. Red stays reserved for exposure."""
    color = YELLOW if seconds >= 86400 else GREEN if seconds < 3600 else ""
    return paint(format_age(seconds), color, width)


def usage_cells(cpu, kb):
    cpu_color = YELLOW if cpu >= 25 else DIM if cpu < 1 else ""
    ram_color = YELLOW if kb >= 1024**2 else ""
    return paint(f"{cpu:.1f}", cpu_color, -6), paint(format_ram(kb), ram_color, -8)


def folder_cell(folder, width):
    """The project name stands out from the path leading to it."""
    parent, slash, name = folder.rpartition("/")
    pad = " " * max(0, width - len(folder))
    return f"{paint(parent + slash, DIM) if slash else ''}{paint(name, CYAN)}{pad}"


def command_cell(row, width):
    extra = f" +{row.members - 1}" if row.members > 1 else ""
    pad = " " * max(0, width - len(row.command) - len(extra))
    return f"{row.command}{paint(extra, DIM) if extra else ''}{pad}"


def section_title(icon, title, count, rule):
    return [f"{BOLD}{icon} {title}{RESET} {DIM}({count}){RESET}", paint("─" * rule, DIM)]


def process_lines(rows, with_folder):
    head = f"{'PID':>7}  {'ETÀ':>7}  {'CPU%':>6}  {'RAM':>8}  "
    head += f"{'CARTELLA':<32}  " if with_folder else ""
    lines = [paint(f"{head}{'COMANDO':<42}  PORTE", DIM)]
    for row in rows:
        cpu, ram = usage_cells(row.cpu, row.rss_kb)
        line = f"{row.pid:>7}  {age_cell(row.age, -7)}  {cpu}  {ram}  "
        line += f"{folder_cell(row.folder, 32)}  " if with_folder else ""
        lines.append(f"{line}{command_cell(row, 42)}  {format_ports(row.ports)}")
    return lines


def container_lines(containers):
    if containers is None:
        return [paint("OrbStack non attivo", YELLOW)]
    if not containers:
        return [paint("Nessun container in esecuzione", DIM)]
    lines = [paint(f"{'NOME':<26}  {'IMMAGINE':<30}  {'SU DA':>8}  {'CPU%':>6}  {'RAM':>8}  PORTE", DIM)]
    for c in sorted(containers, key=lambda c: -c.age):
        cpu, ram = usage_cells(c.cpu, c.rss_kb)
        lines.append(f"{paint(c.name, CYAN, 26)}  {paint(c.image[:30], DIM, 30)}  {age_cell(c.age, -8)}  "
                     f"{cpu}  {ram}  {format_ports(merge_ports(c.ports))}")
    return lines


def exposed_count(rows, containers):
    ports = [a for row in rows for a in row.ports] + [a for c in containers for a in merge_ports(c.ports)]
    return sum(is_exposed(a) for a in ports)


def render(groups, containers, others, rule=MAX_RULE):
    counted = groups + [row for row in others if row.in_total]
    boxes = containers or []
    processes = sum(row.members for row in counted)
    cpu = sum(row.cpu for row in counted) + sum(c.cpu for c in boxes)
    ram = sum(row.rss_kb for row in counted) + sum(c.rss_kb for c in boxes)
    exposed = exposed_count(groups + others, boxes)
    lines = [""] + section_title("🔧", "Processi di sviluppo", len(groups), rule)
    lines += process_lines(groups, True) if groups else [paint("Nessuno", DIM)]
    lines += [""] + section_title("🐳", "Container in esecuzione", len(boxes), rule) + container_lines(containers)
    lines += [""] + section_title("🔌", "Altri processi in ascolto su TCP", len(others), rule)
    lines += process_lines(others, False) if others else [paint("Nessuno", DIM)]
    lines += ["", paint("─" * rule, DIM),
              f"{BOLD}Totale:{RESET} {processes} processi, {len(boxes)} container, "
              f"CPU {cpu:.1f}%, RAM {format_ram(ram)}",
              paint(f"⚠ {exposed} porte esposte alla rete", RED) if exposed
              else paint("✓ Nessuna porta esposta alla rete", GREEN),
              paint("Processi di root non inclusi (niente sudo). OrbStack è escluso dal totale: "
                    "contiene già i container.", DIM),
              paint("Per chiudere a mano: kill <PID>", DIM), ""]
    return "\n".join(lines)


# --- system ----------------------------------------------------------------------------------


def run(command, timeout=15):
    """Stdout of a read-only command, or None if it fails or does not answer."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def read_containers():
    listing = run(["docker", "ps", "--format", "{{json .}}"], timeout=5)
    if listing is None:
        return None
    return parse_containers(listing, run(["docker", "stats", "--no-stream", "--format", "{{json .}}"]) or "")


def main():
    user = os.environ.get("USER", "")
    root = str(pathlib.Path.home() / "sc_workspace")
    # not run(): lsof exits 1 when some file cannot be read, even with a usable output
    cwds = parse_lsof_cwd(subprocess.run(["lsof", "-a", "-d", "cwd", "-u", user, "-nP", "-Fpn"],
                                         capture_output=True, text=True).stdout)
    listeners = parse_lsof_listen(subprocess.run(["lsof", "-iTCP", "-sTCP:LISTEN", "-nP", "-Fpn"],
                                                 capture_output=True, text=True).stdout)
    procs = parse_ps(run(["ps", "-ww", "-x", "-U", user, "-o", "pid=,ppid=,etime=,pcpu=,rss=,command="]) or "")
    selected = select(procs, cwds, root, launcher_chain(procs, os.getpid()))
    groups = build_groups(procs, selected, cwds, listeners, root)
    containers = read_containers()
    others = other_listeners(procs, listeners, selected, containers or [])
    print(render(groups, containers, others, min(shutil.get_terminal_size().columns, MAX_RULE)))


if __name__ == "__main__":
    main()
