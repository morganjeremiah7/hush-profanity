"""Allows `python -m hush_profanity ...` as an alternative to the `hush` script."""
from .cli import main

raise SystemExit(main())
