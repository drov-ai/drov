import json
import subprocess
import sys
from pathlib import Path


def test_generated_schema_matches_types_and_discriminators() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/generate_schemas.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bundle = json.loads((root / "docs/reference/schema-v1.json").read_text())
    schemas = bundle["schemas"]
    assert schemas["RunOutcome"]["discriminator"]["propertyName"] == "status"
    assert schemas["InteractionResponse"]["discriminator"]["propertyName"] == "kind"
    assert schemas["ModelEvent"]["discriminator"]["propertyName"] == "type"
    assert schemas["RunContext"]["additionalProperties"] is False
    assert set(schemas["RunContext"]["properties"]) == {"tenant_id", "user_id"}
    assert "seq" not in schemas["TransientEvent"]["$defs"]["TokenDelta"]["properties"]
