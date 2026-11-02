"""Tests of devps/devps.py on invented ps, lsof and docker output: the real system is never read."""

import importlib.util
import json
import pathlib
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "devps", pathlib.Path(__file__).parent.parent / "devps" / "devps.py"
)
dp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp)

ROOT = "/home/u/sc_workspace"


def proc(pid, ppid=1, command="node server.js", age=100, cpu=1.0, rss=1024):
    return dp.Proc(pid, ppid, age, cpu, rss, command)


def table(*procs):
    return {p.pid: p for p in procs}


class ParsingTests(unittest.TestCase):
    def test_etime_formats(self):
        self.assertEqual(dp.parse_etime("00:05"), 5)
        self.assertEqual(dp.parse_etime("02:06"), 126)
        self.assertEqual(dp.parse_etime("04:18:52"), 15532)
        self.assertEqual(dp.parse_etime("01-01:25:54"), 91554)

    def test_ps_keeps_spaces_in_command_and_skips_malformed_lines(self):
        procs = dp.parse_ps("  10 1 00:05 0.5 2048 /Apps/My App/run --x\nnot a line\nx 1 00:05 0.5 1 cmd\n")
        self.assertEqual(list(procs), [10])
        self.assertEqual(procs[10].command, "/Apps/My App/run --x")
        self.assertEqual(procs[10].rss_kb, 2048)

    def test_lsof_field_output(self):
        self.assertEqual(dp.parse_lsof_cwd("p1\nfcwd\nn/\np2\nfcwd\nn/a b\n"), {1: "/", 2: "/a b"})
        listen = "p5\nf10\nn*:80\nf11\nn127.0.0.1:81\np6\nf1\nn[::1]:82\n"
        self.assertEqual(dp.parse_lsof_listen(listen), {5: ["*:80", "127.0.0.1:81"], 6: ["[::1]:82"]})

    def test_docker_status(self):
        self.assertEqual(dp.parse_up_status("Up 3 minutes (healthy)"), 180)
        self.assertEqual(dp.parse_up_status("Up About an hour"), 3600)
        self.assertEqual(dp.parse_up_status("Up Less than a second"), 0)
        self.assertEqual(dp.parse_up_status("Restarting"), 0)

    def test_docker_ports_skip_unpublished_and_expand_ranges(self):
        text = "127.0.0.1:3000->80/tcp, 9000/tcp, 0.0.0.0:81-82->81-82/tcp, [::]:5432->5432/tcp"
        self.assertEqual(dp.parse_docker_ports(text),
                         ["127.0.0.1:3000", "0.0.0.0:81", "0.0.0.0:82", "[::]:5432"])
        self.assertEqual(dp.parse_docker_ports(""), [])

    def test_sizes(self):
        self.assertEqual(dp.parse_size_kb("1MiB"), 1024)
        self.assertEqual(dp.parse_size_kb("1GiB "), 1024**2)
        self.assertEqual(dp.parse_size_kb("?"), 0)

    def test_containers_without_stats_still_listed(self):
        ps = json.dumps({"Names": "db", "Image": "pg", "Status": "Up 2 hours", "Ports": "127.0.0.1:1->1/tcp"})
        [box] = dp.parse_containers(ps, "")
        self.assertEqual((box.name, box.age, box.cpu, box.rss_kb), ("db", 7200, 0.0, 0))

    def test_containers_with_stats(self):
        ps = json.dumps({"Names": "db", "Image": "pg", "Status": "Up 2 hours"})
        stats = json.dumps({"Name": "db", "CPUPerc": "1.50%", "MemUsage": "2MiB / 8GiB"})
        [box] = dp.parse_containers(ps, stats)
        self.assertEqual((box.cpu, box.rss_kb), (1.5, 2048))


class ExposureTests(unittest.TestCase):
    def test_only_loopback_is_local(self):
        for address in ("127.0.0.1:1", "127.9.9.9:1", "[::1]:1"):
            self.assertFalse(dp.is_exposed(address), address)
        for address in ("*:1", "[::]:1", "0.0.0.0:1", "192.0.2.5:1", "1270.0.0.1:1"):
            self.assertTrue(dp.is_exposed(address), address)

    def test_merge_keeps_the_exposed_bind_in_either_order(self):
        self.assertEqual(dp.merge_ports(["127.0.0.1:5", "[::]:5"]), ["[::]:5"])
        self.assertEqual(dp.merge_ports(["*:5", "127.0.0.1:5"]), ["*:5"])
        self.assertEqual(dp.merge_ports(["*:9", "*:9", "[::1]:10"]), ["*:9", "[::1]:10"])

    def test_format_marks_only_exposed_ports(self):
        out = dp.format_ports(["*:1", "127.0.0.1:2"])
        self.assertIn(f"{dp.RED}*:1{dp.RESET}", out)
        self.assertNotIn(f"{dp.RED}127.0.0.1:2", out)
        self.assertEqual(dp.format_ports([]), "—")


class SelectionTests(unittest.TestCase):
    def test_working_directory_decides_not_the_name(self):
        procs = table(proc(1, 0, "/x/bundle"), proc(2, 0, "/x/NodeService"), proc(3, 0, "/x/anything"))
        cwds = {1: "/", 2: "/home/u", 3: f"{ROOT}/repositories/a"}
        self.assertEqual(dp.select(procs, cwds, ROOT), {3})

    def test_sibling_directory_with_same_prefix_is_outside(self):
        procs = table(proc(1, 0))
        self.assertEqual(dp.select(procs, {1: ROOT + "_old/a"}, ROOT), set())
        self.assertEqual(dp.select(procs, {1: ROOT}, ROOT), {1})

    def test_interactive_shells_are_excluded_but_shell_work_is_not(self):
        procs = table(proc(1, 0, "/bin/zsh -l"), proc(2, 0, "-zsh"), proc(3, 0, "bash run.sh"),
                      proc(4, 0, "/bin/zsh -c source x"), proc(5, 0, "/bin/zsh -il"))
        cwds = {pid: ROOT for pid in procs}
        self.assertEqual(dp.select(procs, cwds, ROOT), {3, 4})

    def test_orphan_prone_tool_selected_outside_the_workspace(self):
        procs = table(proc(1, 0, "node /x/_npx/node_modules/.bin/playwright-mcp"),
                      proc(2, 0, "npm exec @playwright/mcp@latest"),
                      proc(3, 0, "node other.js"))
        self.assertEqual(dp.select(procs, {pid: "/" for pid in procs}, ROOT), {1, 2})

    def test_mentioning_the_tool_is_not_enough(self):
        for command in ("/bin/zsh -c grep playwright-mcp x", "claude --config {playwright-mcp}", "grep my-playwright-mcp-x"):
            self.assertFalse(dp.is_orphan_prone(command), command)

    def test_orbstack_never_in_section_one(self):
        procs = table(proc(1, 0, "/Applications/OrbStack.app/Contents/MacOS/OrbStack Helper x"))
        self.assertEqual(dp.select(procs, {1: ROOT}, ROOT), set())

    def test_launcher_chain_stops_at_the_first_other_process(self):
        procs = table(proc(1, 0, "claude --x"), proc(2, 1, "/bin/zsh -c devps"),
                      proc(3, 2, "uv run devps.py"), proc(4, 3, "/x/Python devps.py"))
        self.assertEqual(dp.launcher_chain(procs, 4), {2, 3, 4})

    def test_launchers_are_excluded_from_selection(self):
        procs = table(proc(3, 0, "uv run devps.py"), proc(4, 3, "python3 devps.py"))
        self.assertEqual(dp.select(procs, {3: ROOT, 4: ROOT}, ROOT, dp.launcher_chain(procs, 4)), set())


class GroupingTests(unittest.TestCase):
    def test_session_collapses_with_summed_usage_and_head_age(self):
        procs = table(proc(10, 1, "/x/claude --a", age=500, cpu=1.0, rss=1000),
                      proc(11, 10, "npm exec @playwright/mcp@latest", age=400, cpu=2.0, rss=2000),
                      proc(12, 11, "node /x/playwright-mcp", age=300, cpu=3.0, rss=3000))
        cwds = {10: f"{ROOT}/repositories/a", 11: f"{ROOT}/repositories/a", 12: "/"}
        [row] = dp.build_groups(procs, {10, 11, 12}, cwds, {}, ROOT)
        self.assertEqual((row.pid, row.age, row.cpu, row.rss_kb, row.members), (10, 500, 6.0, 6000, 3))
        self.assertEqual(row.folder, "repositories/a")

    def test_grouping_looks_through_an_unselected_wrapper(self):
        procs = table(proc(10, 1), proc(11, 10, "wrapper"), proc(12, 11))
        [row] = dp.build_groups(procs, {10, 12}, {}, {}, ROOT)
        self.assertEqual((row.pid, row.members), (10, 2))

    def test_unselected_child_is_not_summed(self):
        procs = table(proc(10, 1, rss=1), proc(11, 10, rss=99))
        [row] = dp.build_groups(procs, {10}, {}, {}, ROOT)
        self.assertEqual(row.rss_kb, 1)

    def test_parent_cycle_does_not_hang(self):
        procs = table(proc(10, 11), proc(11, 10))
        rows = dp.build_groups(procs, {10, 11}, {}, {}, ROOT)
        self.assertEqual(sum(row.members for row in rows), 2)

    def test_ports_merged_across_the_group_and_oldest_first(self):
        procs = table(proc(10, 1, age=5), proc(11, 10, age=5), proc(20, 1, age=50))
        listeners = {10: ["127.0.0.1:3"], 11: ["*:3"]}
        rows = dp.build_groups(procs, {10, 11, 20}, {}, listeners, ROOT)
        self.assertEqual([row.pid for row in rows], [20, 10])
        self.assertEqual(rows[1].ports, ["*:3"])

    def test_abbreviate(self):
        self.assertEqual(dp.abbreviate("/usr/bin/python3 -m http.server 8765"), "python3 -m http.server 8765")
        wrapped = "/A/disclaimer --pgroup -- /home/u/Library/Application Support/c/claude --x"
        self.assertEqual(dp.abbreviate(wrapped), "claude --x")
        self.assertEqual(dp.abbreviate("npm exec @playwright/mcp@latest"), "npm exec @playwright/mcp@latest")
        self.assertEqual(len(dp.abbreviate("x" * 100)), 38)


class OtherListenersTests(unittest.TestCase):
    ORB = "/Applications/OrbStack.app/Contents/MacOS/OrbStack Helper x"

    def box(self, *ports):
        return dp.Container("c", "i", "Up 1 second", 1, list(ports))

    def test_process_in_section_one_is_not_repeated(self):
        procs = table(proc(1, 0), proc(2, 0, age=9))
        rows = dp.other_listeners(procs, {1: ["*:1"], 2: ["*:2"]}, {1}, [])
        self.assertEqual([row.pid for row in rows], [2])

    def test_orbstack_port_published_by_container_is_hidden(self):
        procs = table(proc(1, 0, self.ORB))
        rows = dp.other_listeners(procs, {1: ["127.0.0.1:5"]}, set(), [self.box("127.0.0.1:5")])
        self.assertEqual(rows, [])

    def test_wildcard_forms_match_between_lsof_and_docker(self):
        procs = table(proc(1, 0, self.ORB))
        rows = dp.other_listeners(procs, {1: ["*:5"]}, set(), [self.box("0.0.0.0:5", "[::]:5")])
        self.assertEqual(rows, [])

    def test_network_bind_no_container_declares_stays_visible(self):
        procs = table(proc(1, 0, self.ORB))
        rows = dp.other_listeners(procs, {1: ["127.0.0.1:5", "*:5"]}, set(), [self.box("127.0.0.1:5")])
        self.assertEqual(rows[0].ports, ["*:5"])
        self.assertFalse(rows[0].in_total)

    def test_vanished_process_is_skipped(self):
        self.assertEqual(dp.other_listeners({}, {9: ["*:1"]}, set(), []), [])


class RenderTests(unittest.TestCase):
    def test_orbstack_down(self):
        out = dp.render([], None, [])
        self.assertIn("OrbStack non attivo", out)
        self.assertIn("0 processi, 0 container", out)

    def test_total_counts_members_and_skips_orbstack(self):
        group = dp.Row(1, 9, 1.0, 1024, "a", members=3)
        orb = dp.Row(2, 9, 50.0, 1024**2, "orb", in_total=False)
        box = dp.Container("c", "i", "Up 1 second", 1, [], 2.0, 1024)
        out = dp.render([group], [box], [orb])
        self.assertIn("3 processi, 1 container, CPU 3.0%, RAM 2 MB", out)
        self.assertIn("root non inclusi", out)
        self.assertIn("kill <PID>", out)

    def test_ages(self):
        self.assertEqual([dp.format_age(s) for s in (5, 120, 3700, 90000)], ["5s", "2m", "1h 1m", "1g 1h"])


class SystemTests(unittest.TestCase):
    def test_docker_not_answering_means_none(self):
        with mock.patch.object(dp, "run", return_value=None):
            self.assertIsNone(dp.read_containers())

    def test_run_handles_missing_command_and_failure(self):
        self.assertIsNone(dp.run(["/nonexistent/devps-test"]))
        self.assertIsNone(dp.run(["false"]))


if __name__ == "__main__":
    unittest.main()
