from __future__ import annotations

import base64
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import email_sender
from config import Settings


class EmailAttachmentTests(unittest.TestCase):
    def test_send_email_encodes_optional_zip_attachment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.json"
            token = root / "token.json"
            archive = root / "creator-ready-2026-07-18.zip"
            credentials.write_text("{}", encoding="utf-8")
            token.write_text("{}", encoding="utf-8")
            archive.write_bytes(b"safe zip bytes")
            cfg = Settings(
                email_from="from@example.com",
                email_to="to@example.com",
                gmail_credentials_file=credentials,
                gmail_token_file=token,
            )
            execute = Mock(return_value={"id": "message-1"})
            service = Mock()
            service.users.return_value.messages.return_value.send.return_value.execute = execute
            with (
                patch.object(email_sender, "_credentials", return_value=Mock()),
                patch.object(email_sender, "build", return_value=service),
            ):
                message_id = email_sender.send_email(
                    "subject",
                    "plain",
                    "<p>html</p>",
                    cfg,
                    attachments=[archive],
                )

            raw = service.users.return_value.messages.return_value.send.call_args.kwargs[
                "body"
            ]["raw"]
            message = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(raw.encode("ascii"))
            )

        self.assertEqual(message_id, "message-1")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), archive.name)


if __name__ == "__main__":
    unittest.main()
