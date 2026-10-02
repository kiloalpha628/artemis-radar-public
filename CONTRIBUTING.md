# Contributing

Open an issue for bugs or proposed changes, or submit a focused pull request. Include the radar site, observation time, relevant configuration, expected result, and actual result. Remove credentials, private addresses, personal paths, and unrelated logs before posting. See [SECURITY.md](SECURITY.md) for sensitive reports.

## Local development

Use Python 3.12 on Linux or macOS, matching the Docker image:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Run from the repository root. All 43 tests should run without skips when the dependencies are installed. Tests use committed fixtures and mocked upstream services; the HTTP test needs permission to bind a loopback port. Dependency-free runs do not validate rendering or numeric volumes.

Validate deployment configuration without starting the worker:

```bash
docker compose --env-file .env.example config --quiet
```

For container changes, also build the image when a Docker engine is available. Keep downloaded radar sources and runtime data outside Git. Do not commit `.env`; use `.env.example` for documented defaults.

## Output and fixtures

Keep [CONTRACT.md](CONTRACT.md) consistent with behavior. Change `SCHEMA_VERSION` in `app.py` when clients could misinterpret a changed layout, and `PROCESSING_VERSION` when output or its metadata changes for the same source. Preserve explicit unavailable products and missing-value codes.

See [fixtures/README.md](fixtures/README.md) for regeneration. The generator overwrites generated fixtures, so review its diff and rerun the tests. Preserve source checksums and provenance; label synthetic or simulated data clearly. Record measured validation and remaining limitations in [VALIDATION.md](VALIDATION.md), distinguishing local tests from container and live deployment checks.

Contributions to project code and documentation are under the [MIT license](LICENSE). Preserve third-party notices and identify the source and license of imported code or assets.
