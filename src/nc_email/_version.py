"""Single source of the version string.

Kept in its own module so `pyproject.toml` can read it at build time and the
User-Agent can read it at runtime without either importing the other. A version
that disagrees between the wheel metadata and the wire is a support ticket
nobody can reproduce.
"""

from __future__ import annotations

__version__ = "0.3.0"
