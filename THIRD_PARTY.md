# Third-party sources and dependencies

Updated: 2026-10-14. Drov has not copied or modified source code from another Agent
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

## T-10A container recipe and copied Docker security profile

The analysis image is a separate, locally built resource, not a Python SDK dependency
or a published container. Its exact Linux ARM64 / CPython 3.11 wheel versions, PyPI
URLs and SHA-256 hashes are in `sandbox/analysis/wheels-linux-arm64.json` and
`requirements-linux-arm64.lock`. The actual wheel license declarations and license
file hashes are in `license-inventory-linux-arm64.json`; installed upstream license
and notice files are retained in the image. They are not relicensed as Drov code.

| Package | Actual version | Declared license / upstream license text |
| --- | --- | --- |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; bundled notices remain applicable |
| pandas | 3.0.6 | BSD-3-Clause |
| matplotlib | 3.11.2 | Matplotlib license based on PSF terms; its license text remains applicable |
| contourpy | 1.3.3 | BSD-3-Clause |
| cycler | 0.12.1 | BSD-3-Clause |
| fonttools | 4.66.1 | MIT; LICENSE.external retained |
| kiwisolver | 1.5.1 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 12.3.0 | MIT-CMU; bundled notices remain applicable |
| pyparsing | 3.3.3 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause |
| six | 1.17.0 | MIT |

The base is the Docker Official Python 3.11.17 slim-bookworm ARM64 manifest recorded
in the wheel manifest; its source and CPython/base-system licenses are recorded in
`docs/validation/sandbox-environment-preparation.md`. No base-system license files
are removed. A fixed digest is not a vulnerability scan or security certification.

Copied, unmodified source: Moby **v27.0.3**
[`profiles/seccomp/default.json`](https://github.com/moby/moby/blob/v27.0.3/profiles/seccomp/default.json),
Apache-2.0, destination `sandbox/docker/seccomp-v27.0.3.json`, SHA-256
`9c1025c88ccaa517b648da571961838744ea2137f176bfe6a48b21294cae9c76`.
The original upstream LICENSE is preserved as `sandbox/docker/LICENSE.moby`.
The file appears in source/sdist preparation resources, not in the SDK wheel. Docker
mechanisms and upstream analysis libraries are not claimed as Drov inventions.
