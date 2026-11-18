"""Lead capture for senders who are not registered BDs (user, 2026-11-18)."""

from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.services.intake import leads
from app.services.intake.mail import not_registered

SENDER = "stranger@example.org"
GOOD = {
    "mobile": "98765 43210",
    "state": "Karnataka",
    "district": "Mysuru",
    "pincode": "570001",
    "consent": "1",
}


@pytest.fixture
def lead_settings(settings):
    return settings.model_copy(
        update={
            "intake_encryption_key": SecretStr("k" * 44),
            "intake_public_base_url": "https://las.example.com/",
            "intake_lead_notify_email": "admin@example.com",
            "context_path": "",
        }
    )


def _token(lead_settings) -> str:
    link = leads.form_link(lead_settings, SENDER)
    assert link and link.startswith("https://las.example.com/leads/intake/")
    return link.rsplit("/", 1)[1]


def test_reply_without_a_form_asks_for_details_by_reply(settings):
    mail = not_registered("Loan enquiry", settings, SENDER)
    assert mail.subject == "Re: Loan enquiry"
    assert "state, district and mobile number" in mail.text
    assert "leads/intake" not in mail.text


def test_reply_with_a_form_has_the_button_and_the_services(lead_settings):
    mail = not_registered("Loan enquiry", lead_settings, SENDER)
    assert "Connect me with a Business Associate Executive" in mail.html
    assert "https://las.example.com/leads/intake/" in mail.html
    assert "https://las.example.com/leads/intake/" in mail.text
    assert "How we can help" in mail.text and "joining fee" in mail.text


def test_a_token_is_for_one_sender_and_tamper_proof(lead_settings):
    token = _token(lead_settings)
    assert leads.read_token(lead_settings, token)["sender"] == SENDER
    assert leads.read_token(lead_settings, token + "x") is None
    assert leads.read_token(lead_settings, "nonsense") is None


def test_validation_checks_every_field():
    fields, errors = leads.validate_form(
        mobile="123", state="", district="", pincode="12", consent=False
    )
    assert fields is None
    assert set(errors) == {"mobile", "state", "district", "pincode", "consent"}
    fields, errors = leads.validate_form(
        mobile="+91 98765 43210",
        state=" Karnataka ",
        district="Mysuru",
        pincode="",
        consent=True,
    )
    assert errors == {}
    assert fields == leads.LeadFields("9876543210", "Karnataka", "Mysuru", None)


@pytest.fixture
def lead_client(lead_settings):
    with TestClient(create_app(lead_settings)) as c:
        yield c


def test_form_page_renders_for_a_good_link(lead_client, lead_settings):
    r = lead_client.get(f"/leads/intake/{_token(lead_settings)}")
    assert r.status_code == 200
    assert "Mobile number" in r.text and "Before you continue" in r.text


def test_a_bad_link_is_a_404_page(lead_client):
    r = lead_client.get("/leads/intake/forged")
    assert r.status_code == 404
    assert "expired" in r.text


def test_an_invalid_submission_shows_the_errors(lead_client, lead_settings):
    r = lead_client.post(
        f"/leads/intake/{_token(lead_settings)}",
        content=urlencode({**GOOD, "mobile": "1", "consent": ""}).replace(
            "consent=", "x="
        ),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 422
    assert "valid 10-digit mobile" in r.text
    assert "tick the box" in r.text


def test_a_valid_submission_is_saved_and_mailed(
    lead_client, lead_settings, monkeypatch
):
    saved = []

    def fake_save(engine, token_id, sender, fields):
        saved.append((sender, fields))
        return 1

    monkeypatch.setattr(leads, "save_lead", fake_save)
    monkeypatch.setattr("app.api.leads_web.core_db_engine", lambda request: object())
    r = lead_client.post(
        f"/leads/intake/{_token(lead_settings)}",
        content=urlencode(GOOD),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    assert "received your request" in r.text
    assert saved == [
        (SENDER, leads.LeadFields("9876543210", "Karnataka", "Mysuru", "570001"))
    ]
