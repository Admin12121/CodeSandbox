import sys
from nexorm.cli import main

if "--models" not in sys.argv:
    sys.argv.extend(["--models", "codesandbox.models"])

if __name__ == "__main__":
    main()