"""Generate the actual HTTP contract without opening Runtime or a socket (server extra)."""

import argparse
import json
from pathlib import Path

from drov import Agent, DrovApp
from drov.models import ScriptedModel


def schema_text() -> str:
    agent: Agent[str] = Agent(name="schema-only", revision="1", model=ScriptedModel([]))
    app = DrovApp(agents={"schema-only": agent}, local=True)
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "docs/reference/openapi-v1.json"
    expected = schema_text()
    if args.check:
        if not target.exists() or target.read_text() != expected:
            raise SystemExit("Generated OpenAPI is out of date")
    else:
        target.write_text(expected)
    print("OpenAPI check passed" if args.check else "OpenAPI generated")


if __name__ == "__main__":
    main()
