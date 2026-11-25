"""Make the pure-Python `logic` package importable without Home Assistant.

Tests import `logic.*` directly so that `cajonera/__init__.py`, which imports
`homeassistant`, is never executed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "cajonera"))
