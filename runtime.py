"""Persistent command registry and Linux process supervision."""

import fcntl
import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from sudo_support import ElevatedExecution


APP_ID = "shell-execute-control"
STOP_TIMEOUT = 5.0


def xdg_path(variable, default):
    path = Path(os.environ.get(variable, ""))
    return path if path.is_absolute() else Path.home() / default


@dataclass(frozen=True)
class Command:
    id: str
    name: str
    directory: str
    script: str
    sudo: bool = False


class Registry:
    def __init__(self, config, state):
        for folder in (config, state):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)
        self.config = config / "commands.json"
        self.state = state
        self.lock = (config / "instance.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise

    def load(self):
        try:
            raw = self.config.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        commands = {}
        for item in json.loads(raw):
            command = Command(**item)
            if UUID(command.id).hex != command.id or command.id in commands:
                raise ValueError("Identificador de comando inválido ou duplicado.")
            if not all(isinstance(value, str) for value in
                       (command.name, command.directory, command.script)) or type(command.sudo) is not bool:
                raise ValueError("Cadastro de comando inválido.")
            commands[command.id] = command
        return commands

    def save(self, commands):
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.config.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            try:
                json.dump([asdict(c) for c in commands.values()], output,
                          ensure_ascii=False, indent=2)
                output.write("\n")
                output.close()
                temporary.replace(self.config)
            finally:
                temporary.unlink(missing_ok=True)

    def log(self, command_id):
        return self.state / f"{command_id}.log"

    def close(self):
        self.lock.close()


@dataclass
class Execution:
    process: subprocess.Popen
    deadline: float | None = None
    killed: bool = False

    def send(self, sig):
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass

    def stop(self):
        if self.deadline is None:
            self.send(signal.SIGTERM)
            self.deadline = time.monotonic() + STOP_TIMEOUT

    def poll(self):
        pid = self.process.pid
        # Keep the leader unreaped until the group is done so its ID cannot
        # be recycled while we still need to signal it.
        exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if exited and not group_has_children(pid):
            return self.process.wait()
        if self.deadline is not None and not self.killed:
            if time.monotonic() >= self.deadline:
                self.send(signal.SIGKILL)
                self.killed = True
        return None


def group_has_children(group):
    # killpg(0) sees zombies too, including the unreaped leader. Ignore them
    # and inspect only members of the session and group created by Popen.
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal() or int(entry.name) == group:
                continue
            try:
                stat = Path(entry.path, "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            state, _, pgrp, session, *_ = stat.rsplit(b")", 1)[1].split()
            if state not in (b"Z", b"X") and int(pgrp) == int(session) == group:
                return True
    return False


def spawn_script(fd, directory, output=None):
    return subprocess.Popen(
        ["/bin/bash", f"/dev/fd/{fd}"],
        cwd=directory, stdin=subprocess.DEVNULL,
        stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        pass_fds=(fd,),
    )


class Supervisor:
    def __init__(self, registry):
        self.registry = registry
        self.active = {}
        self.status = {}
        self.errors = {}

    def play(self, command):
        if command.id in self.active:
            raise ValueError("O comando já está em execução.")
        directory = Path(command.directory).expanduser() if command.directory else Path.home()
        self.errors.pop(command.id, None)
        if command.sudo:
            self.active[command.id] = ElevatedExecution(
                command, directory, self.registry.log(command.id)
            )
            self.status[command.id] = "Iniciando com sudo"
            return
        with tempfile.TemporaryFile(mode="w+b") as script:
            script.write(command.script.encode("utf-8"))
            script.seek(0)
            with tempfile.NamedTemporaryFile(dir=self.registry.state, delete=False) as log:
                temporary = Path(log.name)
                try:
                    process = spawn_script(script.fileno(), directory, output=log)
                    execution = self.active[command.id] = Execution(process)
                    self.status[command.id] = "Em execução"
                    try:
                        temporary.replace(self.registry.log(command.id))
                    except OSError:
                        execution.stop()
                        self.status[command.id] = "Encerrando"
                        raise
                finally:
                    temporary.unlink(missing_ok=True)

    def stop(self, command_id):
        execution = self.active.get(command_id)
        if execution:
            execution.stop()
            self.status[command_id] = "Encerrando"

    def stop_all(self):
        for command_id in self.active:
            self.stop(command_id)

    def poll(self):
        changed = []
        for command_id, execution in list(self.active.items()):
            code = execution.poll()
            elevated = isinstance(execution, ElevatedExecution)
            if code is None:
                if elevated and execution.deadline is None:
                    status = ("Em execução" if execution.started else
                              "Aguardando autenticação" if execution.prompt is not None else
                              "Iniciando com sudo")
                    if self.status[command_id] != status:
                        self.status[command_id] = status
                        changed.append(command_id)
                continue
            del self.active[command_id]
            if elevated:
                try:
                    if execution.launch_error or (not execution.started and execution.deadline is None):
                        detail = execution.diagnostic()
                        self.errors[command_id] = detail or "O sudo não iniciou o script."
                finally:
                    execution.close()
            if code < 0:
                result = f"sinal {-code}"
            else:
                result = f"código {code}"
            if elevated and not execution.started:
                label = "Cancelado" if execution.deadline is not None and not execution.launch_error else "Falha ao iniciar"
            elif execution.deadline is not None:
                label = "Parado"
            elif code == 0:
                label = "Concluído"
            else:
                label = "Falhou"
            self.status[command_id] = f"{label} ({result})"
            changed.append(command_id)
        return changed
