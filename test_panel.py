import stat
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def order_handle(self, dashboard, row):
        handle, = dashboard.list.grid_slaves(row=row, column=0)
        return handle

    def drag_to_row(self, dashboard, source, target, after):
        dashboard.root.update_idletasks()
        handle = self.order_handle(dashboard, source)
        _, top, _, height = dashboard.list.grid_bbox(0, target, 3, target)
        x = handle.winfo_rootx() + handle.winfo_width() // 2
        y = dashboard.list.winfo_rooty() + top + height * (3 if after else 1) // 4
        handle.event_generate("<ButtonPress-1>", x=handle.winfo_width() // 2, y=handle.winfo_height() // 2)
        handle.event_generate("<B1-Motion>", rootx=x, rooty=y)
        handle.event_generate("<ButtonRelease-1>", rootx=x, rooty=y)

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
        root.deiconify()
        root.update()
        executions = dashboard.supervisor.active.copy()
        self.drag_to_row(dashboard, source=2, target=1, after=False)
        self.assertEqual(list(dashboard.rows), [second.id, first.id])
        self.assertEqual(list(self.registry.load()), [second.id, first.id])
        self.assertEqual(dashboard.supervisor.active, executions)
        self.drag_to_row(dashboard, source=1, target=2, after=True)
        self.assertEqual(list(dashboard.rows), [first.id, second.id])
        self.assertEqual(list(self.registry.load()), [first.id, second.id])
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

    def test_drag_scrolls_and_only_saves_a_valid_drop(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))

        def close_root():
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(callback)
            root.destroy()

        self.addCleanup(close_root)
        commands = [self.command("true", f"API {index}") for index in range(24)]
        original_order = [command.id for command in commands]
        self.registry.save({command.id: command for command in commands})
        dashboard = Dashboard(root, self.registry, self.registry.load())
        root.geometry("690x310")
        root.update()
        headings = [dashboard.list.grid_slaves(row=0, column=column)[0].cget("text")
                    for column in range(3)]
        self.assertEqual(headings, ["Ordem", "Comando", "Estado"])
        handle = self.order_handle(dashboard, 1)
        x = handle.winfo_rootx() + handle.winfo_width() // 2
        y = handle.winfo_rooty() + handle.winfo_height() // 2
        _, top, _, height = dashboard.list.grid_bbox(0, 2, 3, 2)
        after_second = dashboard.list.winfo_rooty() + top + 3 * height // 4

        with patch.object(self.registry, "save", wraps=self.registry.save) as save:
            handle.event_generate("<ButtonPress-1>", rootx=x, rooty=y)
            handle.event_generate("<ButtonRelease-1>", rootx=x, rooty=y)
            self.drag_to_row(dashboard, source=1, target=1, after=True)
            for escape in (False, True):
                handle.event_generate("<ButtonPress-1>", rootx=x, rooty=y)
                handle.event_generate("<B1-Motion>", rootx=x, rooty=after_second)
                root.update_idletasks()
                self.assertTrue(dashboard.drop_marker.winfo_ismapped())
                self.assertEqual(list(dashboard.rows), original_order)
                if escape:
                    handle.focus_force()
                    root.update()
                    handle.event_generate("<Escape>")
                    handle.event_generate("<ButtonRelease-1>", rootx=x, rooty=after_second)
                else:
                    outside = dashboard.canvas.winfo_rootx() - 1
                    handle.event_generate("<ButtonRelease-1>", rootx=outside, rooty=after_second)
                self.assertFalse(dashboard.drop_marker.winfo_ismapped())
            save.assert_not_called()

        with patch.object(self.registry, "save", side_effect=OSError("disco indisponível")), \
                patch("app.messagebox.showerror") as showerror:
            self.drag_to_row(dashboard, source=1, target=2, after=True)
            showerror.assert_called_once_with("Falha ao salvar", "disco indisponível", parent=root)
        self.assertEqual(list(dashboard.rows), original_order)
        self.assertEqual(list(self.registry.load()), original_order)

        bottom = dashboard.canvas.winfo_rooty() + dashboard.canvas.winfo_height() - 1
        handle.event_generate("<ButtonPress-1>", rootx=x, rooty=y)
        handle.event_generate("<B1-Motion>", rootx=x, rooty=bottom)

        def scrolled_to_bottom():
            root.update()
            return dashboard.canvas.yview()[1] == 1.0

        wait_until(scrolled_to_bottom)
        self.assertEqual(list(self.registry.load()), original_order)
        handle.event_generate("<ButtonRelease-1>", rootx=x, rooty=bottom)
        expected_order = original_order[1:] + original_order[:1]
        self.assertEqual(list(dashboard.rows), expected_order)
        self.registry.close()
        self.registry = Registry(self.config, self.state)
        self.addCleanup(self.registry.close)
        self.assertEqual(list(self.registry.load()), expected_order)


if __name__ == "__main__":
    unittest.main()
