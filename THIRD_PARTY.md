# Third-party sources and dependencies

Updated: 2026-10-13. Drov has not copied or modified source code from another Agent
framework. Design references are recorded in `docs/planning/evidence.md` and the
decision records. Integration does not make upstream capabilities Drov inventions.

The standard Apache-2.0 text in `LICENSE` comes from
[the Apache Software Foundation](https://www.apache.org/licenses/LICENSE-2.0.txt).
It applies to Drov's own source and documentation. Existing files under `drov-brand/`
have not been relicensed; their provenance and permitted public use must be confirmed
before publication. They are excluded from the Python distributions.

## Direct dependencies

Direct requirements are declared in `pyproject.toml`. Exact resolved versions, transitive
dependencies, artifacts and hashes are recorded in `uv.lock`. Packages are installed
separately; their code is not vendored in Drov's wheel. Their own licenses and notices
remain applicable. Adapter extras currently install dependencies only; no Drov adapter
API is implemented.

| Package | Role | Upstream license | Source |
| --- | --- | --- | --- |
| pydantic | Core schema validation | MIT | https://github.com/pydantic/pydantic |
| typing-extensions | Generic runtime type aliases on Python 3.11+ | PSF-2.0 | https://github.com/python/typing_extensions |
| aiosqlite | Core async SQLite access | MIT | https://github.com/omnilib/aiosqlite |
| portalocker | Core file ownership lock | BSD-3-Clause | https://github.com/wolph/portalocker |
| openai | Optional model client | Apache-2.0 | https://github.com/openai/openai-python |
| mcp | Optional MCP client | MIT | https://github.com/modelcontextprotocol/python-sdk |
| fastapi | Optional HTTP service | MIT | https://github.com/fastapi/fastapi |
| uvicorn | Optional ASGI server | BSD-3-Clause | https://github.com/encode/uvicorn |
| hatchling | Build backend | MIT | https://github.com/pypa/hatch |
| ruff | Development lint/format | MIT | https://github.com/astral-sh/ruff |
| mypy | Development type checks | MIT | https://github.com/python/mypy |
| pytest | Development tests | MIT | https://github.com/pytest-dev/pytest |
| pytest-asyncio | Development async tests | Apache-2.0 | https://github.com/pytest-dev/pytest-asyncio |
| twine | Distribution metadata checks | Apache-2.0 | https://github.com/pypa/twine |
| uv | Development resolver/installer, not a runtime dependency | MIT OR Apache-2.0 | https://github.com/astral-sh/uv |

GitHub Actions use `actions/checkout` and `astral-sh/setup-uv`, pinned to specific
upstream commits in the workflow. These are CI tooling, not redistributed library code.

## Adding copied or modified code

Before including upstream source, record its repository, exact commit, original path,
license, copyright/NOTICE requirements, destination and modifications here. Preserve
required notices beside the copied code and in distributions. Unverified provenance
must not be described as original Drov implementation. This record is an engineering
inventory, not a blanket compatibility assessment of future dependencies.
