from __future__ import annotations

import logging
from decimal import Decimal

from codesandbox.features.billing import fx
from codesandbox.features.identity.models import User
from codesandbox.features.sandbox.repository import get_balance

log = logging.getLogger(__name__)


def get_header_balance(user: User, active_workspace: dict | None) -> dict | None:
    """Balance for the header widget — GBP (ledger truth) plus a live NPR
    display conversion. Only sandbox users have a balance at all.

    Returns None (rather than raising) on any failure — a header widget
    should never be able to break page rendering.
    """
    if user.platform_role != "user":
        return None
    try:
        if active_workspace:
            entity_type, entity_id = "org", str(active_workspace["id"])
        else:
            entity_type, entity_id = "user", str(user.id)
        balance = get_balance(entity_type, entity_id)
        amount_gbp = Decimal(str(balance.amount)) if balance else Decimal("0")
    except Exception:
        log.exception("Failed to load balance for header widget")
        return None

    npr_display = None
    try:
        npr_display = fx.gbp_to_npr(amount_gbp)
    except Exception:
        log.warning("NPR conversion unavailable for header widget", exc_info=True)

    return {
        "gbp": f"{amount_gbp:.2f}",
        "npr": f"{npr_display:.2f}" if npr_display is not None else None,
    }
