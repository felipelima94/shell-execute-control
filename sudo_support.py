"""Private control channel for a single sudo execution on Linux."""

import os
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


SUDO = "/usr/bin/sudo"
SYSTEM_PYTHON = "/usr/bin/python3"
PACKET_SIZE = 8192
HERE = Path(__file__).resolve().parent
SOCKET_ENV = "SHELL_PANEL_ASKPASS_SOCKET"
ROOT_UID = 0


def peer_uid(connection):
    layout = struct.Struct("3i")
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, layout.size)
    return layout.unpack(credentials)[1]


def askpass():
    """Reply only on stdout, which sudo directs to its authentication pipe."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as channel:
        channel.connect(os.environ[SOCKET_ENV])
        if peer_uid(channel) != os.getuid():
            return 1
        prompt = sys.argv[1] if len(sys.argv) > 1 else "Senha solicitada pelo sudo:"
        channel.sendall(b"A" + prompt.encode("utf-8")[:PACKET_SIZE - 1])
        reply = channel.recv(PACKET_SIZE)
        if not reply.startswith(b"P"):
            return 1
        sys.stdout.buffer.write(reply[1:] + b"\n")
        sys.stdout.buffer.flush()
        return 0


class ElevatedExecution:
    def __init__(self, command, directory, log_path):
        self.deadline = None
        self.started = False
        self.prompt = None
        self.auth = None
        self.control = None
        self.pending = {}
        self.log_path = log_path
        self.output = None
        self.launch_error = ""
        self.workdir = os.fsencode(directory.absolute())
        self.private = tempfile.TemporaryDirectory(prefix="api-sudo-", dir="/tmp")
        self.listener = None
        self.script = None
        try:
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            address = str(Path(self.private.name) / "control")
            self.listener.bind(address)
            self.listener.listen()
            self.listener.setblocking(False)
            self.script = tempfile.TemporaryFile(mode="w+b")
            self.script.write(command.script.encode("utf-8"))
            self.script.seek(0)
            with tempfile.NamedTemporaryFile(dir=log_path.parent, delete=False) as output:
                self.output = Path(output.name)
                env = {
                    **os.environ,
                    "SUDO_ASKPASS": str(HERE / "sudo_askpass"),
                    SOCKET_ENV: address,
                }
                self.process = subprocess.Popen(
                    [SUDO, "-A", "-u", "root", "-p", "Senha de %p:", "--",
                     SYSTEM_PYTHON, "-I", "-S", "-B", str(HERE / "sudo_support.py"), address],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, env=env,
                )
        except BaseException:
            self.close()
            raise

    def reply(self, password):
        if self.auth is None:
            return
        if password is not None:
            data = password.encode("utf-8")
            if len(data) >= PACKET_SIZE or any(byte in data for byte in (b"\n", b"\r", b"\0")):
                raise ValueError("Informe uma senha em uma única linha.")
            try:
                self.auth.sendall(b"P" + data)
            except OSError:
                pass
        self.auth.close()
        self.auth = self.prompt = None

    def stop(self):
        if self.deadline is not None:
            return
        self.deadline = True
        self.reply(None)
        if self.control is not None:
            try:
                self.control.sendall(b"S")
            except OSError:
                self.control.close()
                self.control = None

    def advance(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except BlockingIOError:
                break
            connection.setblocking(False)
            self.pending[connection] = peer_uid(connection)
        for connection, uid in list(self.pending.items()):
            try:
                request = connection.recv(PACKET_SIZE)
            except BlockingIOError:
                continue
            except OSError:
                request = b""
            del self.pending[connection]
            if self.deadline is not None:
                connection.close()
            elif request.startswith(b"A") and uid == os.getuid() and self.auth is None:
                self.auth = connection
                self.prompt = request[1:].decode("utf-8", errors="replace").replace("\0", "�")
            elif request == b"R" and uid == ROOT_UID and self.control is None and not self.started:
                try:
                    socket.send_fds(connection, [self.workdir], [self.script.fileno()])
                except OSError:
                    connection.close()
                else:
                    self.control = connection
            else:
                connection.close()
        if self.auth is not None and select.select([self.auth], [], [], 0)[0]:
            self.reply(None)
        if self.control is not None and select.select([self.control], [], [], 0)[0]:
            try:
                response = self.control.recv(PACKET_SIZE)
            except OSError:
                response = b""
            if response == b"R" and not self.started:
                try:
                    self.output.replace(self.log_path)
                except OSError as error:
                    self.launch_error = str(error)
                    self.stop()
                else:
                    self.started = True
                    self.output = None
                self.script.close()
            else:
                self.control.close()
                self.control = None

    def poll(self):
        self.advance()
        code = self.process.poll()
        if code is not None:
            self.advance()
        return code

    def diagnostic(self):
        if self.launch_error:
            return self.launch_error
        if self.output is None:
            return ""
        try:
            with self.output.open("rb") as file:
                file.seek(max(0, os.fstat(file.fileno()).st_size - 4096))
                return file.read().decode("utf-8", errors="replace").replace("\0", "�").strip()
        except OSError as error:
            return str(error)

    def close(self):
        self.reply(None)
        for connection in self.pending:
            connection.close()
        for channel in (self.control, self.listener, self.script):
            if channel is not None:
                channel.close()
        if self.output is not None:
            self.output.unlink(missing_ok=True)
        self.private.cleanup()


def privileged_main(address):
    if os.geteuid() != ROOT_UID:
        raise PermissionError("O supervisor sudo precisa rodar como root.")
    return supervise(address, int(os.environ["SUDO_UID"]))


def supervise(address, caller):
    # The isolated interpreter uses only the standard library and this project.
    from runtime import Execution, spawn_script

    interrupted = False

    def interrupt(*_):
        nonlocal interrupted
        interrupted = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupt)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as control:
        control.connect(address)
        if peer_uid(control) != caller:
            raise PermissionError("O canal de controle sudo pertence a outro usuário.")
        control.sendall(b"R")
        cwd, descriptors, flags, _ = socket.recv_fds(
            control, PACKET_SIZE, 1, socket.MSG_CMSG_CLOEXEC
        )
        try:
            if not cwd:
                return 1
            if len(descriptors) != 1 or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                raise ValueError("O supervisor não recebeu o script.")
            # A stop during authentication can close the channel before spawn.
            if interrupted or select.select([control], [], [], 0)[0]:
                return 1
            execution = Execution(spawn_script(descriptors[0], cwd))
        finally:
            for fd in descriptors:
                os.close(fd)

        try:
            control.sendall(b"R")
        except OSError:
            execution.stop()
        while True:
            code = execution.poll()
            if code is not None:
                return code if code >= 0 else 128 - code
            if execution.deadline is None:
                if interrupted or select.select([control], [], [], 0.1)[0]:
                    execution.stop()
            else:
                select.select([], [], [], 0.1)


if __name__ == "__main__":
    sys.path.append(str(HERE))
    try:
        sys.exit(privileged_main(sys.argv[1]))
    except (OSError, ValueError, KeyError) as error:
        sys.exit(str(error))
