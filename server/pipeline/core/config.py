"""Shared paths, .env load, ElevenLabs voice preset."""
from __future__ import annotations

import os
from pathlib import Path

SERVER_ROOT = Path(os.environ.get("VIDEO_CLONE_HOME", Path(__file__).resolve().parents[2]))
DATA = Path(os.environ.get("VIDEO_CLONE_DATA", SERVER_ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)

# ponytail: load server/.env once; no python-dotenv dep
_env_path = SERVER_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

EL_ADAM = "pNInz6obpgDQGcFmaJgB"
# ponytail: key thường thiếu voices_read → preset rộng; API 2s nếu được phép
_EL_PRESET = [
    (EL_ADAM, "Adam"),
    ("21m00Tcm4TlvDq8ikWAM", "Rachel"),
    ("AZnzlk1XvdvUeBnXmlld", "Domi"),
    ("EXAVITQu4vr4xnSDxMaL", "Bella"),
    ("ErXwobaYiN019PkySvjV", "Antoni"),
    ("MF3mGyEYCl7XYWbV9V6O", "Elli"),
    ("TxGEqnHWrfWFTfGW9XjX", "Josh"),
    ("VR6AewLTigWG4xSOukaG", "Arnold"),
    ("pMsXgVXv3BLzUgSXRplE", "Serena"),
    ("yoZ06aMxZJJ28mfd3POQ", "Sam"),
    ("ThT5KcBeYPX3keUQqHPh", "Dorothy"),
    ("2EiwWnXFnvU8HyyO9idq", "Clyde"),
    ("CYw3kZ02Hs0563khs1Fj", "Dave"),
    ("D38z5RcWu1voky8WS1ja", "Fin"),
    ("LcfcDJNUP1GaeZkokFn8", "Emily"),
    ("N2lVS1w4EtoT3dr4eOWO", "Callum"),
    ("ODq5zmih8GrVes37Dizd", "Patrick"),
    ("SOYHLrjzK2X1ezoPC6cr", "Harry"),
    ("TX3LPaxmHKxFdv7VOQHJ", "Liam"),
    ("XB0fDUnXU5powFXDhCwa", "Charlotte"),
    ("XrExE9yKIg1WjnnlVkGX", "Matilda"),
    ("Yko7PKHZNXotIFUBG7I9", "Matthew"),
    ("ZQe5CZNOzWyzPSCw5dgu", "James"),
    ("Zlb1dXrM653N07WRdFW3", "Joseph"),
    ("bVMeCyTHy58xNoL34h3p", "Jeremy"),
    ("flq6f7yk4E4fJM5XTYuZ", "Michael"),
    ("g5CIjZEefAph4nQFvHAz", "Ethan"),
    ("iP95p4xoKVk53GoZ742B", "Chris"),
    ("jBpfuIE2acCO8z3wKNLl", "Gigi"),
    ("jsCqWAovWfOLnJxsPWaX", "Freya"),
    ("nPczCjzI2devNBz1zQrb", "Brian"),
    ("oWAxZDx7w5VEj9dCyTzz", "Grace"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel"),
    ("pFZP5JQG7SspSbDNFt8N", "Lily"),
    ("pqHfZKP75CvOlQylNvMs", "Bill"),
    ("t0jbNlBVZ17f02VDIeMI", "Jessie"),
    ("z9fAnlkpzviPz146aGWa", "Glinda"),
    ("zcAOhNBS3c14rBihAFp1", "Giovanni"),
    ("zrHiDhphv9ZnVXBqCLjz", "Mimi"),
]
