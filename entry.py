"""PyInstaller entry point for pn-bin (absolute import of the package CLI)."""

import sys

from pathnest.cli import main

if __name__ == "__main__":
    sys.exit(main())
