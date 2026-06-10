from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        from codesandbox.db import apply_migrations

        applied = apply_migrations()
        if applied:
            print("Applied migrations:")
            for migration in applied:
                print(f"- {migration}")
        else:
            print("No pending migrations.")
        return

    from codesandbox.app import create_app

    app = create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1").lower() not in {"0", "false", "no"}
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
