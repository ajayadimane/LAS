"""Minimal IMAP4rev1 server for adapter tests: LOGIN SELECT STATUS CREATE UID SEARCH/FETCH/MOVE/COPY
/STORE EXPUNGE IDLE LOGOUT. Single mailbox store shared by all connections."""

from __future__ import annotations

import re
import socketserver
import threading
from dataclasses import dataclass, field


@dataclass
class Store:
    uidvalidity: int = 777
    folders: dict[str, dict[int, bytes]] = field(default_factory=lambda: {"INBOX": {}})
    next_uid: int = 1
    lock: threading.Lock = field(default_factory=threading.Lock)
    changed: threading.Condition = field(default_factory=threading.Condition)

    def deliver(self, raw: bytes, folder: str = "INBOX") -> int:
        with self.lock:
            uid = self.next_uid
            self.next_uid += 1
            self.folders.setdefault(folder, {})[uid] = raw
        with self.changed:
            self.changed.notify_all()
        return uid


def _unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1].replace('\\"', '"') if s.startswith('"') and s.endswith('"') else s


class Handler(socketserver.StreamRequestHandler):
    store: Store

    def send(self, line: str | bytes) -> None:
        self.wfile.write(line if isinstance(line, bytes) else line.encode())
        self.wfile.flush()

    def handle(self) -> None:
        self.selected = "INBOX"
        self.send("* OK [CAPABILITY IMAP4rev1 IDLE MOVE UIDPLUS] fake ready\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode().rstrip("\r\n")
            tag, _, rest = text.partition(" ")
            cmd, _, args = rest.partition(" ")
            cmd = cmd.upper()
            if cmd == "UID":
                sub, _, args = args.partition(" ")
                cmd = "UID " + sub.upper()
            try:
                if not self.dispatch(tag, cmd, args):
                    return
            except Exception as e:  # noqa: BLE001 - report any handler error to the client
                self.send(f"{tag} BAD {e}\r\n")

    def dispatch(self, tag: str, cmd: str, args: str) -> bool:
        st = self.store
        if cmd == "CAPABILITY":
            self.send("* CAPABILITY IMAP4rev1 IDLE MOVE UIDPLUS\r\n")
        elif cmd == "LOGIN":
            pass
        elif cmd == "LOGOUT":
            self.send("* BYE\r\n")
            self.send(f"{tag} OK LOGOUT\r\n")
            return False
        elif cmd == "SELECT":
            self.selected = _unquote(args)
            with st.lock:
                n = len(st.folders.setdefault(self.selected, {}))
            self.send(f"* {n} EXISTS\r\n* OK [UIDVALIDITY {st.uidvalidity}] ok\r\n")
            self.send(f"{tag} OK [READ-WRITE] SELECT done\r\n")
            return True
        elif cmd == "CREATE":
            name = _unquote(args)
            with st.lock:
                if name in st.folders:
                    self.send(f"{tag} NO [ALREADYEXISTS] exists\r\n")
                    return True
                st.folders[name] = {}
        elif cmd == "UID SEARCH":
            with st.lock:
                uids = sorted(st.folders[self.selected])
            m = re.search(r"UID (\d+):\*", args)
            hdr = re.search(r'HEADER Message-ID "?([^"]+)"?', args, re.I)
            if m:
                lo = int(m.group(1))
                found = [u for u in uids if u >= lo] or (uids[-1:] if uids else [])
            elif hdr:
                with st.lock:
                    found = [
                        u
                        for u, raw in st.folders[self.selected].items()
                        if hdr.group(1).encode() in raw
                    ]
            else:
                found = uids
            self.send("* SEARCH " + " ".join(map(str, found)) + "\r\n")
        elif cmd == "UID FETCH":
            uid = int(args.split(" ", 1)[0])
            with st.lock:
                raw = st.folders[self.selected][uid]
            self.send(f"* 1 FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n")
        elif cmd in ("UID MOVE", "UID COPY"):
            uid_s, _, dest = args.partition(" ")
            dest = _unquote(dest)
            with st.lock:
                raw = st.folders[self.selected][int(uid_s)]
                st.folders.setdefault(dest, {})[int(uid_s)] = raw
                if cmd == "UID MOVE":
                    del st.folders[self.selected][int(uid_s)]
        elif cmd == "IDLE":
            self.send("+ idling\r\n")
            with st.lock:
                seen = len(st.folders[self.selected])
            done = threading.Event()

            def watch() -> None:
                while not done.is_set():
                    with st.changed:
                        st.changed.wait(0.1)
                    with st.lock:
                        n = len(st.folders[self.selected])
                    if n > seen:
                        self.send(f"* {n} EXISTS\r\n")
                        return

            t = threading.Thread(target=watch, daemon=True)
            t.start()
            self.rfile.readline()  # DONE
            done.set()
            t.join(1)
            self.send(f"{tag} OK IDLE terminated\r\n")
            return True
        self.send(f"{tag} OK {cmd} done\r\n")
        return True


def start_server() -> tuple[socketserver.ThreadingTCPServer, Store]:
    store = Store()
    handler = type("H", (Handler,), {"store": store})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, store
