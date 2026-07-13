from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def prepare_runtime() -> None:
    """Make project imports and NexORM's default connection available."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from codesandbox.config import get_settings
    from codesandbox.infrastructure.nexorm import configure_db

    settings = get_settings()
    configure_db(settings.database_url)

    import codesandbox.models  # noqa: F401 — registers all models
    import codesandbox.features.finance  # noqa: F401 — registers finance platform permissions
