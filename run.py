#!/usr/bin/env python
"""Convenience launcher: `uv run python run.py`."""

import sys

from rhfaccess.app import main

if __name__ == "__main__":
    sys.exit(main())
