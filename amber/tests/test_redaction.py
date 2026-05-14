from __future__ import annotations

from app.core.redaction import redact_mapping, redact_pii_text


def test_redact_pii_text_masks_email_phone_account_and_wallet() -> None:
    text = "email alice@example.com phone +375291234567 account 1234567890123456 wallet 0x1234567890abcdef1234"
    masked = redact_pii_text(text)
    assert masked is not None
    assert "alice@example.com" not in masked
    assert "+375291234567" not in masked
    assert "1234567890123456" not in masked
    assert "0x1234567890abcdef1234" not in masked


def test_redact_mapping_masks_string_values_only() -> None:
    payload = {"counterparty": "john.doe@example.com", "amount": 100, "wallet": "0xabcdef1234567890abcd"}
    masked = redact_mapping(payload)
    assert masked["counterparty"] != payload["counterparty"]
    assert masked["wallet"] != payload["wallet"]
    assert masked["amount"] == 100
