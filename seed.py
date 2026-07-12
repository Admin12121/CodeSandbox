#!/usr/bin/env python
"""One-time seed entrypoint.

Feature-specific seed logic lives in ``seeds/``:
- ``seeds.platform``: platform permissions, roles, first admin user
- ``seeds.sandbox_templates``: example sandbox templates/plans
"""
from __future__ import annotations

from seeds.bootstrap import prepare_runtime
from seeds.platform import print_seed_credentials, seed_platform_admin
from seeds.sandbox_templates import seed_sandbox_templates


def seed():
    prepare_runtime()
    admin_user_id = seed_platform_admin()
    seed_sandbox_templates(admin_user_id)
    print_seed_credentials()


if __name__ == "__main__":
    seed()
