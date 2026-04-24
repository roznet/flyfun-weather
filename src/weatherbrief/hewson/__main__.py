"""`python -m weatherbrief.hewson` entry point — delegates to cli.main()."""

import sys

from weatherbrief.hewson.cli import main

if __name__ == "__main__":
    sys.exit(main())
