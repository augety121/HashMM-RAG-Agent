import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_v2600_contract_gate_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify-contracts.py")],
        cwd=ROOT, text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "[contracts] OK" in result.stdout


def test_event_envelope_is_closed_and_redacted():
    schema = json.loads((ROOT / "contracts" / "hashmm-event-v1.schema.json").read_text("utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["payload_ref"]["properties"]["redacted"]["const"] is True
