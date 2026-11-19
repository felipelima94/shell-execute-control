import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import sudo_support
from runtime import Command, Registry, Supervisor
from test_panel import wait_until


class SudoTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.registry = Registry(self.directory / "config", self.directory / "state")
        self.addCleanup(self.registry.close)
        self.supervisor = Supervisor(self.registry)
        self.addCleanup(self.clean_executions)
        fake_sudo = self.directory / "sudo"
        fake_sudo.write_text(
            "#!/usr/bin/python3\n"
            "import os, subprocess, sys\n"
            f"sys.path.insert(0, {str(sudo_support.HERE)!r})\n"
            "from sudo_support import supervise\n"
            "mode = os.environ['PANEL_TEST_SUDO']\n"
            "if mode == 'deny':\n"
            "    sys.exit('policy denied execution')\n"
            "if mode == 'prompt':\n"
            "    for attempt in range(2):\n"
            "        result = subprocess.run([os.environ['SUDO_ASKPASS'], 'Senha de root:'], stdout=subprocess.PIPE)\n"
            "        if result.returncode:\n"
            "            sys.exit('authentication cancelled')\n"
            "        if result.stdout == b'test-only\\n':\n"
            "            break\n"
            "        print('authentication failed', file=sys.stderr)\n"
            "    else:\n"
            "        sys.exit(1)\n"
            "sys.exit(supervise(sys.argv[-1], os.getuid()))\n",
            encoding="utf-8",
        )
        fake_sudo.chmod(0o700)
        self.enterContext(patch.multiple(sudo_support, SUDO=str(fake_sudo), ROOT_UID=os.getuid()))

    def clean_executions(self):
        self.supervisor.stop_all()
        wait_until(self.finished)

    def finished(self):
        self.supervisor.poll()
        return not self.supervisor.active

    def command(self, script="echo saída; echo erro >&2", elevated=True):
        return Command(uuid4().hex, "API", str(self.directory), script, elevated)

    def play(self, command, mode):
        with patch.dict(os.environ, PANEL_TEST_SUDO=mode):
            self.supervisor.play(command)
        return self.supervisor.active[command.id]

    def log(self, command):
        return self.registry.log(command.id)

    def wait_for_prompt(self, execution):
        def waiting():
            self.supervisor.poll()
            return execution.prompt is not None

        wait_until(waiting)
        self.assertEqual(execution.prompt, "Senha de root:")

    def test_existing_configuration_defaults_to_current_user(self):
        command = self.command(script="id -u", elevated=False)
        self.registry.config.write_text(json.dumps([{
            "id": command.id, "name": command.name, "directory": command.directory,
            "script": command.script,
        }]))
        loaded = self.registry.load()[command.id]
        self.assertFalse(loaded.sudo)
        self.play(loaded, "deny")
        wait_until(self.finished)
        self.assertEqual(self.log(command).read_text(), f"{os.getuid()}\n")

    def test_wrong_password_then_retry(self):
        command = self.command()
        self.registry.save({command.id: command})
        self.assertTrue(self.registry.load()[command.id].sudo)
        execution = self.play(command, "prompt")
        self.wait_for_prompt(execution)
        execution.reply("wrong-test-only")
        self.wait_for_prompt(execution)
        execution.reply("test-only")
        wait_until(self.finished)
        log = self.log(command).read_text()
        self.assertIn("saída\nerro\n", log)
        self.assertNotIn("test-only", log)
        self.assertNotIn("test-only", self.registry.config.read_text())

    def test_authentication_failure_cancel_and_policy_rejection_keep_last_log(self):
        command = self.command()
        self.log(command).write_text("anterior")
        execution = self.play(command, "prompt")
        for _ in range(2):
            self.wait_for_prompt(execution)
            execution.reply("wrong-test-only")
        wait_until(self.finished)
        self.assertIn("authentication failed", self.supervisor.errors[command.id])
        self.assertEqual(self.log(command).read_text(), "anterior")

        execution = self.play(command, "prompt")
        self.wait_for_prompt(execution)
        self.supervisor.stop_all()
        wait_until(self.finished)
        self.assertTrue(self.supervisor.status[command.id].startswith("Cancelado"))
        self.assertEqual(self.log(command).read_text(), "anterior")

        self.play(command, "deny")
        wait_until(self.finished)
        self.assertIn("policy denied", self.supervisor.errors[command.id])
        self.assertEqual(self.log(command).read_text(), "anterior")

    def test_passwordless_execution_and_parallel_stop_on_disconnect(self):
        commands = [
            self.command("bash -c 'trap \"\" TERM; echo pronto; exec sleep 60' & wait")
            for _ in range(2)
        ]
        first, second = [self.play(command, "passwordless") for command in commands]

        def ready():
            self.supervisor.poll()
            return all(self.log(command).exists() and "pronto" in self.log(command).read_text()
                       for command in commands)

        wait_until(ready)
        self.assertIsNone(first.prompt)
        self.supervisor.stop(commands[0].id)
        second.control.close()  # Simulate the panel losing the other connection.
        second.control = None
        wait_until(self.finished)
        self.assertTrue(self.supervisor.status[commands[0].id].startswith("Parado"))
        self.assertEqual(first.process.returncode, 143)
        self.assertEqual(second.process.returncode, 143)


if __name__ == "__main__":
    unittest.main()
