"""Test for scripts/trello_utils.py's add_attachment: the multipart upload it sends, and its
read-after-write check.

Run directly: python plugins/trello/tests/test_add_attachment.py
"""
import email
import email.policy
import json
import os
import sys
import tempfile
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.abspath(os.path.join(HERE, "..", "skills", "trello", "scripts"))
sys.path.insert(0, SCRIPTS_DIR)

import trello_utils  # noqa: E402
from trello_utils import add_attachment  # noqa: E402

failures = []
SESSION = ("key", "token")
CONTENT = b"\x89PNG\r\n\x1a\n\x00\xff--not a boundary\r\n"


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


class FakeTrello:
    """Stands in for urlopen: parses the uploaded multipart body and echoes it back on the GET."""

    def __init__(self, stored_bytes=None):
        self.stored_bytes = stored_bytes
        self.requests = []
        self.parts = {}

    def __call__(self, req, timeout=None):
        path = urllib.parse.urlparse(req.full_url).path.removeprefix("/1")
        self.requests.append((req.get_method(), path))
        if req.get_method() == "POST":
            head = f"Content-Type: {req.get_header('Content-type')}\r\n\r\n".encode()
            message = email.message_from_bytes(head + req.data, policy=email.policy.HTTP)
            for part in message.iter_parts():
                self.parts[part.get_param("name", header="content-disposition")] = part
            return _Resp({"id": "A1"})
        size = len(self.parts["file"].get_payload(decode=True))
        return _Resp({"id": "A1", "name": self.parts["name"].get_payload(decode=True).decode(),
                      "bytes": size if self.stored_bytes is None else self.stored_bytes})


class _Resp:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


with tempfile.TemporaryDirectory() as tmp:
    image = os.path.join(tmp, "scan front.png")
    with open(image, "wb") as f:
        f.write(CONTENT)

    print("add_attachment")
    fake = FakeTrello()
    trello_utils.urllib.request.urlopen = fake
    stored = add_attachment("C1", image, "Front of the card ✓", SESSION)
    file_part = fake.parts["file"]
    check("posts to the card's attachments, then reads that attachment back",
          fake.requests == [("POST", "/cards/C1/attachments"), ("GET", "/cards/C1/attachments/A1")], fake.requests)
    check("uploads the file's exact bytes", file_part.get_payload(decode=True) == CONTENT)
    check("sends the file's own name", file_part.get_filename() == os.path.basename(image), file_part.get_filename())
    check("types the file by its extension", file_part.get_content_type() == "image/png",
          file_part.get_content_type())
    check("returns the stored attachment under the asked name",
          stored["id"] == "A1" and stored["name"] == "Front of the card ✓", stored)

    trello_utils.urllib.request.urlopen = FakeTrello(stored_bytes=3)
    try:
        add_attachment("C1", image, "Front", SESSION)
        check("a short read-back raises", False)
    except RuntimeError as e:
        check("a short read-back raises", "A1" in str(e), e)

print()
if failures:
    print(f"Verification FAILED - {len(failures)} errors")
    sys.exit(1)
print("Verification passed ✅")
