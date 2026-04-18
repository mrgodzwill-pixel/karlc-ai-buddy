import imaplib
import unittest

import gmail_imap


class _FakeMail:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def select(self, mailbox, readonly=True):
        self.calls.append((mailbox, readonly))
        outcome = self.outcomes.get(mailbox)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return "NO", []
        return outcome


class GmailImapTests(unittest.TestCase):
    def test_select_mailbox_falls_back_to_quoted_gmail_all_mail(self):
        mail = _FakeMail({
            "[Gmail]/All Mail": imaplib.IMAP4.error("BAD Could not parse command"),
            '"[Gmail]/All Mail"': ("OK", [b"1"]),
        })

        selected = gmail_imap._select_mailbox(mail)

        self.assertTrue(selected)
        self.assertEqual(mail.calls[0][0], "[Gmail]/All Mail")
        self.assertEqual(mail.calls[1][0], '"[Gmail]/All Mail"')

    def test_select_mailbox_returns_false_if_all_candidates_fail(self):
        mail = _FakeMail({})

        selected = gmail_imap._select_mailbox(mail)

        self.assertFalse(selected)


if __name__ == "__main__":
    unittest.main()
