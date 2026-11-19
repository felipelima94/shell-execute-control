import stat
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from uuid import uuid4

from app import Dashboard
from runtime import Command, Registry, STOP_TIMEOUT, Supervisor


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("Timed out waiting for the expected state")


class PanelTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config = self.directory / "config"
        self.state = self.directory / "state"
        self.registry = Registry(self.config, self.state)
        self.addCleanup(self.registry.close)

    def command(self, script, name="API de teste"):
        return Command(uuid4().hex, name, str(self.directory), script)

    def cleanup_execution(self, supervisor):
        supervisor.stop_all()
        wait_until(lambda: self.finished(supervisor))

    def finished(self, supervisor):
        supervisor.poll()
        return not supervisor.active

    def read_log(self, command):
        log = self.registry.log(command.id)
        return log.read_text() if log.exists() else ""

    def test_script_and_last_log_survive_reopening(self):
        command = self.command(
            'DB_USER="API" \\\n'
            'DB_URL="jdbc:oracle:thin:@(DESCRIPTION=(CONNECT_DATA=(SID=TEST)))" \\\n'
            "bash -c 'printf \"%s\\n\" \"$DB_USER\" \"$DB_URL\"; pwd; echo erro >&2; exit 7'"
        )
        commands = {command.id: command}
        self.registry.save(commands)
        supervisor = Supervisor(self.registry)
        self.addCleanup(self.cleanup_execution, supervisor)
        supervisor.play(command)
        wait_until(lambda: self.finished(supervisor))
        self.assertEqual(supervisor.status[command.id], "Falhou (código 7)")
        expected = (
            "API\njdbc:oracle:thin:@(DESCRIPTION=(CONNECT_DATA=(SID=TEST)))\n"
            f"{self.directory}\nerro\n"
        )
        self.assertEqual(self.read_log(command), expected)
        self.assertEqual(supervisor.poll(), [])
        self.registry.close()
        self.registry = Registry(self.config, self.state)
        self.addCleanup(self.registry.close)
        self.assertEqual(self.registry.load(), commands)
        self.assertEqual(Supervisor(self.registry).status, {})
        self.assertEqual(self.read_log(command), expected)
        for path in (self.registry.config, self.registry.log(command.id)):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        supervisor = Supervisor(self.registry)
        self.addCleanup(self.cleanup_execution, supervisor)
        supervisor.play(Command(command.id, command.name, "", "pwd"))
        wait_until(lambda: self.finished(supervisor))
        self.assertEqual(self.read_log(command), f"{Path.home()}\n")

    def test_stop_isolated_group_with_term_resistant_child(self):
        stubborn = self.command(
            "bash -c 'trap \"\" TERM; echo ready; exec sleep 60' & wait"
        )
        other = self.command("echo running; exec sleep 60", "Outra API")
        supervisor = Supervisor(self.registry)
        self.addCleanup(self.cleanup_execution, supervisor)
        supervisor.play(stubborn)
        supervisor.play(other)
        wait_until(lambda: "ready" in self.read_log(stubborn) and "running" in self.read_log(other))
        with self.assertRaises(ValueError):
            supervisor.play(stubborn)
        supervisor.stop(stubborn.id)

        def stopped():
            supervisor.poll()
            return stubborn.id not in supervisor.active

        wait_until(stopped, timeout=STOP_TIMEOUT + 2)
        self.assertEqual(supervisor.status[stubborn.id], "Parado (sinal 15)")
        self.assertIsNone(supervisor.active[other.id].process.poll())

    def test_window_closes_running_groups_and_refreshes_log(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        root_is_open = True

        def on_destroy(event):
            nonlocal root_is_open
            if event.widget == root:
                root_is_open = False

        root.bind("<Destroy>", on_destroy)
        self.addCleanup(lambda: root.destroy() if root_is_open else None)
        dashboard = Dashboard(root, self.registry, {})
        self.addCleanup(self.cleanup_execution, dashboard.supervisor)

        dashboard.add.invoke()
        editor = next(child for child in root.winfo_children() if isinstance(child, tk.Toplevel))
        editor.name.set("API resistente")
        editor.script.insert("1.0", "trap '' TERM; echo pronto; exec sleep 60")
        editor.submit()
        first, = dashboard.commands.values()
        second = self.command("trap '' TERM; echo outro; exec sleep 60", "Outra API")
        dashboard.save(second, root)
        for command in (first, second):
            buttons = dashboard.rows[command.id][1]
            buttons["Play"].invoke()
            self.assertTrue(buttons["Play"].instate(["disabled"]))
            self.assertTrue(buttons["Editar"].instate(["disabled"]))
        dashboard.rows[first.id][1]["Ver log"].invoke()
        viewer = dashboard.log_windows[first.id]

        def log_visible():
            root.update()
            return viewer.output.get("1.0", "end-1c") == "pronto\n"

        wait_until(log_visible)
        wait_until(lambda: "outro" in self.read_log(second))
        close_request = root.protocol("WM_DELETE_WINDOW")
        started = time.monotonic()
        root.tk.call(close_request)

        def closed():
            if root_is_open:
                root.update()
            return not root_is_open

        wait_until(closed, timeout=STOP_TIMEOUT + 2)
        self.assertLess(time.monotonic() - started, STOP_TIMEOUT + 2)
        self.assertEqual(dashboard.supervisor.active, {})
        self.assertEqual(self.registry.load(), {first.id: first, second.id: second})
        self.assertEqual(self.read_log(first), "pronto\n")


if __name__ == "__main__":
    unittest.main()
