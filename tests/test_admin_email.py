"""Admin-email identity matching (case-insensitive)."""

from __future__ import annotations


def test_get_admin_emails_lowercased(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Admin@Test.com,  Bob@X.IO ")
    from weatherbrief.notify.admin_email import get_admin_emails

    assert get_admin_emails() == ["admin@test.com", "bob@x.io"]


def test_is_admin_email_case_insensitive(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@test.com,bob@x.io")
    from weatherbrief.notify.admin_email import is_admin_email

    assert is_admin_email("ADMIN@Test.COM") is True
    assert is_admin_email("  bob@x.io  ") is True
    assert is_admin_email("nope@x.io") is False
    assert is_admin_email("") is False
    assert is_admin_email(None) is False


def test_is_admin_email_empty_config(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    from weatherbrief.notify.admin_email import is_admin_email

    assert is_admin_email("anyone@x.io") is False
