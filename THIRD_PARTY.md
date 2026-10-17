# Third-party sources and dependencies

Updated: 2026-10-17. Drov has not copied or modified source code from another Agent
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
remain applicable. The OpenAI extra implements a Responses adapter using the separately installed
official openai 3.29.0 client (Apache-2.0) and its httpx2 2.13.1 transport
(BSD-3-Clause); no SDK source is copied. The MCP extra implements stdio tools with the separately installed official
mcp 2.3.0 and mcp-types 2.3.0 clients (MIT), using jsonschema 4.26.0 (MIT).
The server extra implements a lifespan-owned or borrowed Runtime HTTP/SSE transport using separately installed FastAPI 0.143.0 (MIT), Starlette 1.7.0 (BSD-3-Clause) and Uvicorn 0.54.0 (BSD-3-Clause). No upstream server source is copied. Protocol sources and actual offline validation are recorded in
`docs/decisions/0022-openai-responses-adapter.md` and
`docs/validation/t11-openai-responses.md`.

| Package | Role | Upstream license | Source |
| --- | --- | --- | --- |
| pydantic | Core schema validation | MIT | https://github.com/pydantic/pydantic |
| typing-extensions | Generic runtime type aliases on Python 3.11+ | PSF-2.0 | https://github.com/python/typing_extensions |
| aiosqlite | Core async SQLite access | MIT | https://github.com/omnilib/aiosqlite |
| portalocker | Core file ownership lock | BSD-3-Clause | https://github.com/wolph/portalocker |
| openai | Optional model client | Apache-2.0 | https://github.com/openai/openai-python |
| e2b | Optional cloud sandbox SDK, 2.56.0 | MIT | https://github.com/e2b-dev/E2B |
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
| mkdocs | Documentation-only builder, 1.6.1 in docs group | BSD-2-Clause | https://github.com/mkdocs/mkdocs |

## Documentation site

The site theme under `docs/theme/` is Drov's own HTML/CSS/JavaScript and uses system
fonts. It does not copy the ignored brand originals or use a third-party theme.
MkDocs 1.6.1 and its transitive build dependencies are installed separately and
locked in `uv.lock`; they are not SDK runtime requirements. Its built-in search
plugin generates the documentation's JSON index; the build removes unused MkDocs
search scripts and Lunr before packaging. The browser runs only Drov's own search
client, which reads that local index. No MkDocs/Lunr runtime source is redistributed
in the static site. No external search service, font, tracking or CDN is configured.

GitHub Actions use `actions/checkout` and `astral-sh/setup-uv`, pinned to specific
upstream commits in the workflow. These are CI tooling, not redistributed library code.

## Adding copied or modified code

Before including upstream source, record its repository, exact commit, original path,
license, copyright/NOTICE requirements, destination and modifications here. Preserve
required notices beside the copied code and in distributions. Unverified provenance
must not be described as original Drov implementation. This record is an engineering
inventory, not a blanket compatibility assessment of future dependencies.

## T-10A/T-10B container recipe and copied Docker security profile

The analysis image is a separate, locally built resource, not a Python SDK dependency
or a published container. Its exact Linux ARM64 / CPython 3.11 wheel versions, PyPI
URLs and SHA-256 hashes are in `sandbox/analysis/wheels-linux-arm64.json` and
`requirements-linux-arm64.lock`. The actual wheel license declarations and license
file hashes are in `license-inventory-linux-arm64.json`; installed upstream license
and notice files are retained in the image. They are not relicensed as Drov code.

T-10B adds separately downloaded/verified Linux AMD64 / CPython 3.11 wheels in
`wheels-linux-amd64.json`, `requirements-linux-amd64.lock`, and
`license-inventory-linux-amd64.json`. The versions below are the same; platform
wheel hashes and bundled notices remain separate. Both fixed base manifests were
resolved from the previously recorded official Python image index; build and
execution evidence is recorded separately in `docs/validation/t10b-sandbox-faults.md`.

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
An additional unmodified copy of the profile and LICENSE is distributed at
`src/drov/sandbox/seccomp-v27.0.3.json` and `src/drov/sandbox/LICENSE.moby` (wheel paths
`drov/sandbox/`). The private T-10A control preflight checks the pinned profile bytes
and copies them into its explicit CLI configuration. Package checks require both
copies to match; `.gitattributes` preserves upstream bytes on every checkout. Docker
mechanisms and upstream analysis libraries are not claimed as Drov inventions.

## T-10C optional E2B dependency and private template

E2B 2.56.0 is separately installed under its MIT license; no E2B source is copied
into Drov. Exact artifacts and transitive versions are locked in uv.lock. The
private template reuses the existing Linux AMD64 Python base manifest and hashed
analysis-package recipe above; upstream license/notice files remain installed.
The builder uploads the fixed recipe/hash lock only, with no repository directory
or credential-file copy. Drov's root monitor and integration are original code.

The resolved RPC path uses connectrpc0.11.1 and protobuf-py0.1.1 (Apache-2.0),
pyqwest0.12.0 (MIT text in its wheel), e2b-dockerfile-utils0.1.1 (Apache-2.0),
and httpx0.28.1 (BSD-3-Clause). protobuf-py-ext0.1.1 is an upstream native
dependency whose wheel/sdist omit license metadata/text. Its hash-verified sdist
matches all 14 Python/Rust source/stub/build files plus Cargo.lock, README and
pyproject.toml in upstream snapshot
[9f963031200cbe6c70d62d5a142d7ba27313f203](https://github.com/bufbuild/protobuf-py/tree/9f963031200cbe6c70d62d5a142d7ba27313f203),
whose root LICENSE is Apache-2.0 (SHA-256
1c5e374691049b02313a909936cb4b70df33e5d94404688f27766fa42b8088e5).
The sdist Cargo.toml is packaging-normalized and PKG-INFO is generated; this source
comparison does not claim a reproducible native binary build. Artifact SHA-256 is
e85bfdfdb3ed50634db8ccc7429dd9286520109489c735463971a418707b4fef.
No native code is vendored or relicensed. Sources checked 2026-10-15:
[0.1.1 artifact](https://pypi.org/project/protobuf-py-ext/0.1.1/),
[fixed upstream license](https://github.com/bufbuild/protobuf-py/blob/9f963031200cbe6c70d62d5a142d7ba27313f203/LICENSE).
Preserve the upstream artifact metadata omission in the T-18 public-extra review;
do not invent a License-Expression inside its wheel.
Actual SDK/template/installation and cloud evidence is in
`docs/validation/t10c-e2b.md`; this does not publish the template, image or package.


## T-12 MCP stdio source inspection

Inspected 2026-10-15: official python-sdk v2.3.0 tag resolves to commit
`2118f14f8a19bc158d8a1cf90af58d85d187f849`; the installed wheel and lock retain
upstream licenses. Client/ClientSession/stdio and POSIX/Windows process cleanup
were inspected for lifecycle and single-call behavior. No upstream SDK code is
vendored or modified. Drov's connection owner, frozen definitions, policy mapping
and synthetic fixture are project code. Protocol references and compatibility
limits are recorded in ADR-0024 and the T-12 validation record; using this SDK does
not imply all MCP protocol versions or capabilities are supported.


## T-13 HTTP transport

Inspected installed metadata/source on 2026-10-15 for the locked FastAPI 0.143.0,
Starlette 1.7.0 and Uvicorn 0.54.0 lifespan, streaming responses and flow control.
Their original licenses remain in their distributions. Drov's authorization,
route mapping, upload ledger and fixtures are original integration code; protocol
sources are linked in ADR-0025. httpx 0.28.1 (BSD-3-Clause), already locked through
other extras, is now explicitly a development test requirement; the server extra
does not require it. No third-party code or built frontend assets were vendored.


## T-14 workbench build assets

The workbench uses Vite 8.3.4 (MIT) to compile project TypeScript/CSS. Its generated browser module-preload helper is retained with the upstream MIT notice in `workbench/public/THIRD_PARTY.txt` and `drov/server/static/THIRD_PARTY.txt` inside the wheel. Exact development dependencies are recorded in `workbench/package-lock.json`: TypeScript 5.9.3 and Playwright 1.64.1 (Apache-2.0), openapi-typescript 7.13.0 and Prettier 3.8.3 (MIT). These tools are not application SDKs loaded by the browser; no third-party UI/component/icon library is copied. Source and installed license metadata inspected 2026-10-15. The original upstream distributions retain their other bundled/build dependency notices; this is not a T-18 complete transitive license audit.

The selected `drov-icon.svg` is a byte-for-byte copy of the user-approved local `drov-brand/svg/drov-icon-black.svg` vector reconstruction; other ignored brand originals are untouched and excluded from sdist. Provenance and public-use scope are still to be consolidated before the first package/site publication, as required by release-policy; this task does not publish either.
