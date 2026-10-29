#!/usr/bin/env python
"""Create the per-service custom fields every in-house service needs.

For each in-house service in Settings.services (field id F), the fields
F_Payment, F_PayMode, F_TxnId and F_Doc (see app/services/service_fields.py),
all non-searchable and non-unique. F itself and the three summary fields
already exist. Fields that already exist are skipped, so it is safe to run
again, e.g. after adding a service to config.

It changes the program manager's setup for every customer: ask before
running --apply against a real vendor (CLAUDE.md > Rules).

Signs in as VENDOR_SMOKE_USER_ID (the gateway needs a user bearer), then as
the super admin (VENDOR_ADMIN_*) to create the fields. Nothing is printed
about either token.

Usage:
    python scripts/setup_custom_fields.py           # dry run: show what is missing
    python scripts/setup_custom_fields.py --apply   # create the missing fields
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import SecretStr  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from app.clients.vendor_client import VendorCallContext, VendorClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import VendorError  # noqa: E402
from app.main import build_http_client  # noqa: E402
from app.schemas.custom_field import CreateCustomFieldRequest  # noqa: E402
from app.schemas.identity import LoginRequest  # noqa: E402
from app.services.admin_session import AdminSession  # noqa: E402
from app.services.custom_field import CustomFieldService  # noqa: E402
from app.services.identity import IdentityService  # noqa: E402
from app.services.service_fields import (  # noqa: E402
    SERVICE_FIELD_SUFFIXES,
    in_house,
)


class Creds(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    vendor_smoke_user_id: str
    vendor_smoke_password: SecretStr
    vendor_smoke_client_ip: str = "127.0.0.1"


def wanted(settings) -> list[CreateCustomFieldRequest]:
    out = []
    for service in in_house(settings):
        for suffix, what in SERVICE_FIELD_SUFFIXES.items():
            field_id = (service.field_id or "") + suffix
            out.append(
                CreateCustomFieldRequest(
                    custom_field_id=field_id,
                    custom_field_name=field_id,
                    custom_field_description=f"LAS {service.name} {what}",
                    custom_field_data_type="Text",
                    is_searchable=False,
                    is_unique=False,
                    search_scope="NA",
                )
            )
    return out


async def main(apply: bool) -> int:
    settings = get_settings()
    creds = Creds()
    print(
        f"vendor: {settings.vendor_base_url}  program manager: "
        f"{settings.vendor_program_manager_id}"
    )
    async with build_http_client(settings) as http:
        client = VendorClient(http, settings)
        ctx = VendorCallContext(
            client_ip=creds.vendor_smoke_client_ip,
            latitude=settings.default_geo_latitude,
            longitude=settings.default_geo_longitude,
        )
        login = await IdentityService(client, settings).login(
            LoginRequest(
                user_id=creds.vendor_smoke_user_id, password=creds.vendor_smoke_password
            ),
            ctx,
        )
        ctx = VendorCallContext(**{**ctx.__dict__, "token": login.access_token})
        fields = CustomFieldService(client, settings)

        existing = {
            f.custom_field_id
            for f in (await fields.list_for_program_manager(None, ctx)).custom_fields
        }
        missing = [r for r in wanted(settings) if r.custom_field_id not in existing]
        print(
            f"defined: {len(existing)}  wanted: {len(wanted(settings))}  "
            f"missing: {len(missing)}"
        )
        for r in missing:
            print(f"  - {r.custom_field_id}  ({r.custom_field_description})")
        if not missing or not apply:
            if missing:
                print("\ndry run: nothing created. Re-run with --apply.")
            return 0

        admin = AdminSession(client, settings)
        for r in missing:
            try:
                ack = await admin.run(ctx, lambda c, r=r: fields.create(r, c))
            except VendorError as exc:
                if exc.http_status == 409:
                    print(f"  = {r.custom_field_id} already exists")
                    continue
                print(
                    f"  ! {r.custom_field_id} failed: {exc.http_status} "
                    f"{exc.error_code} -- stopping"
                )
                return 1
            print(f"  + {r.custom_field_id} created (txn {ack.transaction_id})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="create missing fields")
    sys.exit(asyncio.run(main(parser.parse_args().apply)))
