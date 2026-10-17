"""Entry script for PyInstaller (it cannot run a package __main__ with relative imports)."""
import sys

from orion_bg.cli import main

if __name__ == "__main__":
    sys.exit(main())
