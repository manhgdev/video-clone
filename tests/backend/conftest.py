"""Allow backend modules to be imported when pytest is run from the repository root."""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
