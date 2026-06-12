import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexorm.cli import main

if "--models" not in sys.argv:
    sys.argv = [sys.argv[0], "--models", "codesandbox.models"] + sys.argv[1:]

if __name__ == "__main__":
    main()
