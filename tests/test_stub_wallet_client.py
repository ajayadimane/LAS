"""The stub wallet payment client: fakes the vendor's money-moving transfer
call so dev/test/Playwright runs can exercise pay later without real vendor
credentials or moving real money. See app/services/stub_wallet_client.py and
app/core/config.py's vendor_wallet_stub_enabled.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from tests.test_enrolment import AUTH, BASE, PAY_LATER, TRANSFER, fields, person  # noqa: F401


def test_vendor_wallet_stub_enabled_is_refused_outside_dev_or_test():
    for bad_environment in ("production", "sandbox", "staging", ""):
        with pytest.raises(ValidationError, match="vendor_wallet_stub_enabled"):
            Settings(
                _env_file=None,
                vendor_wallet_stub_enabled=True,
                environment=bad_environment,
            )


@pytest.mark.parametrize("ok_environment", ["local", "dev", "test"])
def test_vendor_wallet_stub_enabled_is_allowed_for_dev_and_test(ok_environment):
    s = Settings(
        _env_file=None, vendor_wallet_stub_enabled=True, environment=ok_environment
    )
    assert s.vendor_wallet_stub_enabled is True


@pytest.fixture
def stub_settings(settings):
    return settings.model_copy(update={"vendor_wallet_stub_enabled": True})


@pytest.fixture
def stub_client(stub_settings):
    with TestClient(create_app(stub_settings)) as c:
        yield c


def test_pay_later_with_the_stub_never_calls_the_real_vendor_transfer(
    stub_client,
    vendor,
    person,  # noqa: F811
    fields,  # noqa: F811
):
    transfer_route = vendor.put(TRANSFER).mock()  # would fail the test if called
    response = stub_client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)

    assert response.status_code == 200
    assert not transfer_route.called
    body = response.json()
    assert body["payment_status"] == "Paid"
    assert body["status_recorded"] is True
    assert body["transfer"]["transaction_id"].startswith("stub-")


def test_pay_later_with_the_stub_still_persists_the_same_custom_fields(
    stub_client,
    person,  # noqa: F811
    fields,  # noqa: F811
):
    response = stub_client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)
    assert response.status_code == 200
    # Two field writes: claim ("Processing"), then settle ("Paid") -- same
    # shape as the real-vendor flow in test_enrolment.py, the stub only
    # replaces the transfer call itself.
    assert len(fields.calls) == 2
    second_write = fields.calls[1].request
    assert "Paid" in second_write.read().decode()
