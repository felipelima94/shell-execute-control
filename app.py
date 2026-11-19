#!/usr/bin/python3
"""Painel desktop para executar comandos Bash no Linux."""

import os
import signal
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from uuid import uuid4

from runtime import APP_ID, Command, Registry, Supervisor, xdg_path


POLL_MS = 150
LOG_WINDOW_BYTES = 256 * 1024


class LogWindow(tk.Toplevel):
    def __init__(self, parent, name, path):
        super().__init__(parent)
        self.title(f"Log · {name}")
        self.geometry("800x460")
        self.path = path
        self.change = None
        self.follow = tk.BooleanVar(self, value=True)

        toolbar = ttk.Frame(self, padding=(12, 8))
        toolbar.pack(fill="x")
        self.status = ttk.Label(toolbar)
        self.status.pack(side="left")
        self.error = ttk.Label(self, wraplength=650, padding=(12, 0, 12, 6))
        ttk.Checkbutton(toolbar, text="Acompanhar saída", variable=self.follow,
                        command=self.scroll_to_end).pack(side="right")
        self.output = ScrolledText(
            self, wrap="none", state="disabled", font="TkFixedFont",
            padx=10, pady=10, background="#15202c", foreground="#ecf2f8",
        )
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.output.xview)
        self.output.configure(xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        self.output.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.refresh()

    def show_error(self, detail):
        if detail:
            self.error.configure(text=detail)
            self.error.pack(fill="x", before=self.output.frame)
        else:
            self.error.pack_forget()

    def scroll_to_end(self):
        if self.follow.get():
            self.output.see("end")

    def refresh(self):
        try:
            with self.path.open("rb") as file:
                stat = os.fstat(file.fileno())
                change = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if change == self.change:
                    return
                offset = max(0, stat.st_size - LOG_WINDOW_BYTES)
                file.seek(offset)
                data = file.read(LOG_WINDOW_BYTES)
                text = data.decode("utf-8", errors="replace").replace("\0", "�")
                status = ("Últimos 256 KiB · stdout / stderr" if offset else
                          "stdout / stderr" if data else "Aguardando saída…")
        except FileNotFoundError:
            change, text, status = ("missing", "", "Nenhum log disponível.")
        except OSError as error:
            self.status.configure(text=f"Erro ao ler o log ({error.strerror})")
            return
        self.status.configure(text=status)
        if change == self.change:
            return
        self.change = change
        position = self.output.yview()[0]
        horizontal = self.output.xview()[0]
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.configure(state="disabled")
        self.output.xview_moveto(horizontal)
        self.output.yview_moveto(position)
        self.scroll_to_end()


class Editor(tk.Toplevel):
    def __init__(self, parent, command, save):
        super().__init__(parent)
        self.title("Editar comando" if command else "Novo comando")
        self.transient(parent)
        self.geometry("740x490")
        self.minsize(440, 350)
        self.command = command
        self.save = save
        self.name = tk.StringVar(self, value=command.name if command else "")
        self.directory = tk.StringVar(self, value=command.directory if command else "")
        self.sudo = tk.BooleanVar(self, value=command.sudo if command else False)
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Nome da API").pack(anchor="w")
        name = ttk.Entry(body, textvariable=self.name)
        name.pack(fill="x", pady=(4, 12))
        ttk.Label(body, text="Diretório de trabalho (opcional)").pack(anchor="w")
        directory_row = ttk.Frame(body)
        directory_row.pack(fill="x", pady=(4, 12))
        ttk.Entry(directory_row, textvariable=self.directory).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(directory_row, text="Procurar…", command=self.choose_directory).pack(
            side="right", padx=(8, 0)
        )
        ttk.Checkbutton(body, text="Executar com sudo", variable=self.sudo).pack(
            anchor="w", pady=(0, 10)
        )
        ttk.Label(body, text="Script Bash").pack(anchor="w")
        actions = ttk.Frame(body)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(actions, text="Salvar", command=self.submit).pack(side="right")
        ttk.Button(actions, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self.script = ScrolledText(body, wrap="none", font="TkFixedFont", undo=True)
        self.script.pack(fill="both", expand=True, pady=(4, 0))
        if command:
            self.script.insert("1.0", command.script)
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()
        name.focus_set()

    def choose_directory(self):
        directory = filedialog.askdirectory(parent=self)
        if directory:
            self.directory.set(directory)

    def submit(self):
        name = self.name.get().strip()
        script = self.script.get("1.0", "end-1c")
        if not name or not script.strip():
            messagebox.showerror("Cadastro", "Preencha o nome e o script Bash.", parent=self)
            return
        command_id = self.command.id if self.command else uuid4().hex
        command = Command(command_id, name, self.directory.get(), script, self.sudo.get())
        if self.save(command, self):
            self.destroy()


class PasswordDialog(tk.Toplevel):
    def __init__(self, parent, name, prompt, reply, cancel):
        super().__init__(parent)
        self.title("Autenticação sudo")
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", cancel)
        self.bind("<Escape>", lambda event: cancel())
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=name, wraplength=420).pack(anchor="w")
        ttk.Label(body, text=prompt, wraplength=420).pack(anchor="w", pady=(10, 4))
        self.password = ttk.Entry(body, show="•", width=40, exportselection=False)
        self.password.pack(fill="x")

        def submit(event=None):
            password = self.password.get()
            self.password.delete(0, "end")
            try:
                reply(password)
            except ValueError as error:
                messagebox.showerror("Autenticação sudo", str(error), parent=self)
                return
            self.destroy()

        self.bind("<Return>", submit)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Autenticar", command=submit).pack(side="right")
        ttk.Button(actions, text="Cancelar", command=cancel).pack(side="right", padx=(0, 8))
        self.password.focus_set()


class Dashboard:
    def __init__(self, root, registry, commands):
        self.root = root
        self.registry = registry
        self.commands = commands
        self.supervisor = Supervisor(registry)
        self.log_windows = {}
        self.password_dialogs = {}
        self.rows = {}
        self.closing = False
        self.exit_requested = False
        root.title("Painel de APIs")
        root.geometry("880x480")
        root.minsize(690, 310)
        root.protocol("WM_DELETE_WINDOW", self.close)

        header = ttk.Frame(root, padding=(20, 18))
        header.pack(fill="x")
        ttk.Label(header, text="Painel de APIs", font=("TkDefaultFont", 17, "bold")).pack(side="left")
        self.add = ttk.Button(header, text="+ Novo comando", command=self.edit)
        self.add.pack(side="right")

        table = ttk.Frame(root, padding=(20, 0, 20, 16))
        table.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(table, highlightthickness=0, background=root.cget("background"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = ttk.Frame(self.canvas)
        self.list.columnconfigure(0, weight=1)
        window = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.list.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        root.bind("<Button-4>", lambda event: self.canvas.yview_scroll(-3, "units"))
        root.bind("<Button-5>", lambda event: self.canvas.yview_scroll(3, "units"))
        self.footer = ttk.Label(root, padding=(20, 0, 20, 12))
        self.footer.pack(fill="x")
        self.render()
        self.tick()

    def render(self):
        for widget in self.list.winfo_children():
            widget.destroy()
        self.rows.clear()
        ttk.Label(self.list, text="Comando").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(self.list, text="Estado").grid(row=0, column=1, sticky="w", pady=(0, 6))
        if not self.commands:
            ttk.Label(self.list, text="Cadastre um comando para começar.").grid(
                row=1, column=0, columnspan=3, sticky="w", pady=12
            )
        for index, command in enumerate(self.commands.values(), start=1):
            command_id = command.id
            name = f"{command.name} · sudo" if command.sudo else command.name
            ttk.Label(self.list, text=name, wraplength=240).grid(
                row=index, column=0, sticky="w", padx=(0, 12), pady=5
            )
            status = ttk.Label(self.list, width=24)
            status.grid(row=index, column=1, sticky="w", padx=(0, 8))
            actions = ttk.Frame(self.list)
            actions.grid(row=index, column=2, sticky="e")
            buttons = {}
            for label, action in (
                ("Play", self.play), ("Stop", self.stop), ("Ver log", self.show_log),
                ("Editar", self.edit), ("Excluir", self.delete),
            ):
                button = ttk.Button(actions, text=label, width=len(label) + 1,
                                    command=lambda cb=action, cid=command_id: cb(cid))
                button.pack(side="left", padx=(4, 0))
                buttons[label] = button
            self.rows[command_id] = status, buttons
            self.update_row(command_id)
        self.update_footer()

    def update_row(self, command_id):
        status, buttons = self.rows[command_id]
        execution = self.supervisor.active.get(command_id)
        status.configure(text=self.supervisor.status.get(command_id, "Parado"))
        for label in ("Play", "Editar", "Excluir"):
            buttons[label].configure(state="disabled" if execution or self.closing else "normal")
        can_stop = execution and execution.deadline is None
        buttons["Stop"].configure(state="normal" if can_stop and not self.closing else "disabled")

    def update_footer(self):
        message = ("Encerrando os comandos…" if self.closing else
                   f"{len(self.supervisor.active)} em execução · {len(self.commands)} cadastrados")
        self.footer.configure(text=message)

    def play(self, command_id):
        try:
            self.supervisor.play(self.commands[command_id])
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("Falha ao iniciar", str(error), parent=self.root)
        self.update_row(command_id)
        self.update_footer()

    def stop(self, command_id):
        self.supervisor.stop(command_id)
        self.close_password_dialog(command_id)
        self.update_row(command_id)

    def edit(self, command_id=None):
        Editor(self.root, self.commands.get(command_id), self.save)

    def persist(self, commands, parent):
        try:
            self.registry.save(commands)
        except OSError as error:
            messagebox.showerror("Falha ao salvar", str(error), parent=parent)
            return False
        self.commands = commands
        self.render()
        return True

    def save(self, command, parent):
        if not self.persist({**self.commands, command.id: command}, parent):
            return False
        window = self.log_windows.get(command.id)
        if window and window.winfo_exists():
            window.title(f"Log · {command.name}")
        return True

    def delete(self, command_id):
        command = self.commands[command_id]
        if not messagebox.askyesno("Excluir comando", f"Excluir “{command.name}” e seu log?",
                                   parent=self.root):
            return
        commands = self.commands.copy()
        del commands[command_id]
        if self.persist(commands, self.root):
            self.supervisor.status.pop(command_id, None)
            self.supervisor.errors.pop(command_id, None)
            window = self.log_windows.pop(command_id, None)
            if window and window.winfo_exists():
                window.destroy()
            try:
                self.registry.log(command_id).unlink(missing_ok=True)
            except OSError as error:
                messagebox.showerror("Falha ao excluir o log", str(error), parent=self.root)

    def show_log(self, command_id):
        window = self.log_windows.get(command_id)
        if window and window.winfo_exists():
            window.lift()
            window.focus_set()
            return
        self.log_windows[command_id] = LogWindow(
            self.root, self.commands[command_id].name, self.registry.log(command_id)
        )
        self.log_windows[command_id].show_error(self.supervisor.errors.get(command_id))

    def close_password_dialog(self, command_id):
        current = self.password_dialogs.pop(command_id, None)
        if current and current[1].winfo_exists():
            current[1].destroy()

    def refresh_password_dialogs(self):
        for command_id, (request, _) in list(self.password_dialogs.items()):
            execution = self.supervisor.active.get(command_id)
            if getattr(execution, "auth", None) is not request:
                self.close_password_dialog(command_id)
        for command_id, execution in self.supervisor.active.items():
            request = getattr(execution, "auth", None)
            if request is not None and command_id not in self.password_dialogs:
                dialog = PasswordDialog(
                    self.root, self.commands[command_id].name, execution.prompt, execution.reply,
                    lambda cid=command_id: self.stop(cid),
                )
                self.password_dialogs[command_id] = request, dialog

    def tick(self):
        if self.exit_requested and not self.closing:
            self.close()
        changed = self.supervisor.poll()
        self.refresh_password_dialogs()
        for command_id in changed:
            self.update_row(command_id)
        if changed:
            self.update_footer()
        if self.closing and not self.supervisor.active:
            self.root.destroy()
            return
        for command_id, window in list(self.log_windows.items()):
            if window.winfo_exists():
                window.show_error(self.supervisor.errors.get(command_id))
                window.refresh()
            else:
                del self.log_windows[command_id]
        self.root.after(POLL_MS, self.tick)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.supervisor.stop_all()
        for command_id in list(self.password_dialogs):
            self.close_password_dialog(command_id)
        self.add.configure(state="disabled")
        for command_id in self.rows:
            self.update_row(command_id)
        self.update_footer()

    def request_exit(self, *_):
        self.exit_requested = True


def main():
    root = tk.Tk()
    root.withdraw()
    config = xdg_path("XDG_CONFIG_HOME", ".config") / APP_ID
    state = xdg_path("XDG_STATE_HOME", ".local/state") / APP_ID
    registry = None
    try:
        registry = Registry(config, state)
        dashboard = Dashboard(root, registry, registry.load())
    except (OSError, ValueError, TypeError, AttributeError) as error:
        detail = "O painel já está aberto." if isinstance(error, BlockingIOError) else str(error)
        messagebox.showerror("Painel de APIs", detail, parent=root)
        if registry:
            registry.close()
        root.destroy()
        return 1
    signal.signal(signal.SIGTERM, dashboard.request_exit)
    signal.signal(signal.SIGINT, dashboard.request_exit)
    root.deiconify()
    try:
        root.mainloop()
    finally:
        registry.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
