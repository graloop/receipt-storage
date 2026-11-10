# A small mail server for the tests, speaking just enough real IMAP over a real
# socket, so the app's actual imaplib commands are exercised. It holds fake
# emails and remembers which were marked as read.

import email
import re
import socketserver
import threading
from email import policy


class FakeMailbox:
    def __init__(self, password="app-password"):
        self.password = password
        self.messages = {}  # uid -> raw bytes
        self.seen = set()
        self.deleted = False
        self.logins = []
        self.header_fetches = 0
        self.body_fetches = 0

    def add(self, raw: bytes) -> int:
        uid = len(self.messages) + 1
        self.messages[uid] = raw
        return uid

    def unseen(self) -> list[int]:
        return [uid for uid in self.messages if uid not in self.seen]


class _Handler(socketserver.StreamRequestHandler):
    def _send(self, text: str):
        self.wfile.write(text.encode() + b"\r\n")

    def handle(self):
        box = self.server.box
        self._send("* OK fake IMAP ready")
        while line := self.rfile.readline():
            tag, _, rest = line.decode().strip().partition(" ")
            command = rest.upper()
            if command.startswith("CAPABILITY"):
                self._send("* CAPABILITY IMAP4rev1")
                self._send(f"{tag} OK done")
            elif command.startswith("LOGIN"):
                _, user, password = rest.split(" ", 2)
                box.logins.append(user.strip('"'))
                if password.strip('"') == box.password:
                    self._send(f"{tag} OK logged in")
                else:
                    self._send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials")
            elif command.startswith("SELECT"):
                self._send(f"* {len(box.messages)} EXISTS")
                self._send(f"{tag} OK [READ-WRITE] selected")
            elif command.startswith("UID SEARCH UNSEEN"):
                self._send("* SEARCH " + " ".join(str(uid) for uid in box.unseen()))
                self._send(f"{tag} OK done")
            elif command.startswith("UID FETCH") and "HEADER.FIELDS" in command:
                box.header_fetches += 1
                fields = re.search(r"HEADER\.FIELDS \(([^)]*)\)", command)[1].split()
                for uid in (int(u) for u in rest.split()[2].split(",")):
                    message = email.message_from_bytes(box.messages[uid], policy=policy.compat32)
                    lines = [f"{name}: {value}" for name, value in message.items() if name.upper() in fields]
                    headers = ("\r\n".join(lines) + "\r\n\r\n").encode()
                    self.wfile.write(
                        f"* {uid} FETCH (UID {uid} BODY[HEADER.FIELDS ({' '.join(fields)})] {{{len(headers)}}}\r\n".encode()
                        + headers
                        + b")\r\n"
                    )
                self._send(f"{tag} OK done")
            elif command.startswith("UID FETCH"):
                box.body_fetches += 1
                uid = int(rest.split()[2])
                # Asking for anything but PEEK would mark the mail as read, as Gmail does.
                assert "BODY.PEEK[]" in command
                raw = box.messages[uid]
                self.wfile.write(f"* {uid} FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n")
                self._send(f"{tag} OK done")
            elif command.startswith("UID STORE"):
                uid = int(rest.split()[2])
                if not re.search(r"\+FLAGS \(\\SEEN\)$", command):
                    box.deleted = True  # Anything but "mark as read" is out of bounds.
                box.seen.add(uid)
                self._send(f"* {uid} FETCH (UID {uid} FLAGS (\\Seen))")
                self._send(f"{tag} OK done")
            elif command.startswith("LOGOUT"):
                self._send("* BYE")
                self._send(f"{tag} OK bye")
                return
            else:
                box.deleted = True
                self._send(f"{tag} BAD unexpected: {rest}")


class FakeImapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, box: FakeMailbox):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.box = box
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]
