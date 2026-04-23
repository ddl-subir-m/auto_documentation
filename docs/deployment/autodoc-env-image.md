# Vendor autodoc into a Domino environment image

For MVP, autodoc ships as a Python wheel that you bake into a Domino
compute environment image. Jobs launched by the MRM Portal run inside
containers built from that image and call autodoc from the pre-installed
site-packages — no runtime `pip install`, no internet access required.

This is a one-time setup per Domino tenant, repeated only when autodoc
releases a new version.

## 1. Build the wheel locally

From the repository root:

```bash
cd auto_model_docs
python -m build --wheel
```

Output: `auto_model_docs/dist/auto_model_docs-0.1.0-py3-none-any.whl`.

If `build` is not installed: `python -m pip install build`. Python 3.10+
is required.

Copy the wheel into the directory holding your Domino environment image
Dockerfile (typically a separate repository or folder that tracks your
tenant's compute environments). Do not commit the wheel to this repo —
`dist/` is gitignored.

## 2. Dockerfile snippet

Add the following to your Domino environment image Dockerfile, after the
base Python install:

```dockerfile
# --- autodoc (MRM Portal model documentation) ---
ARG AUTODOC_VERSION=0.1.0
COPY auto_model_docs-${AUTODOC_VERSION}-py3-none-any.whl /tmp/
RUN pip install --no-cache-dir /tmp/auto_model_docs-${AUTODOC_VERSION}-py3-none-any.whl \
    && rm /tmp/auto_model_docs-${AUTODOC_VERSION}-py3-none-any.whl
```

Pip resolves autodoc's transitive dependencies (`anthropic`, `openai`,
`python-docx`, `mlflow`, etc.) from your configured index. If the image
builds without outbound network access, pre-stage a wheelhouse and pass
`--no-index --find-links /path/to/wheels`.

## 3. Pin the version in the Domino env config

In the Domino UI, edit the environment and set the `AUTODOC_VERSION`
build arg (or hard-code the version in the Dockerfile) so image rebuilds
are reproducible. Record the pinned version in your environment's
description field, e.g. `autodoc 0.1.0 vendored`.

Point the MRM Portal target projects at this environment:

- **Project → Settings → Compute Environment** — select the image built
  above.
- Any Jobs the Portal launches inherit the environment, so `python -m
  autodoc.main ...` resolves immediately.

## 4. Rebuilding on a new autodoc release

When autodoc publishes a new version:

1. Pull the tag from this repo (e.g. `git checkout autodoc-v0.2.0`).
2. Rebuild the wheel: `cd auto_model_docs && python -m build --wheel`.
3. Copy the new `.whl` into the env-image Dockerfile directory.
4. Bump `AUTODOC_VERSION` in the Dockerfile.
5. Rebuild the Domino environment image (Domino UI → Environments →
   Build) and promote the new revision to active.
6. Restart any long-running target-project Jobs so they pick up the new
   image.

The Portal does not pin autodoc versions per-run; whatever is in the
active environment image is what runs.

## 5. Smoke test from a Domino Job

After the environment image is live, launch a one-off Job in a target
project using the new environment with this command:

```bash
python -c "import autodoc; print('autodoc', autodoc.__name__, 'imported OK')"
```

Expected stdout: `autodoc autodoc imported OK`, exit code 0. If import
fails, the wheel did not install correctly — check the image build logs
for pip errors.

For a deeper check, run the library against a sample spec:

```bash
python -m autodoc.main --spec doc_spec.yaml --dry-run
```

(Requires `doc_spec.yaml` in the project working directory. `--dry-run`
exits before calling the LLM.)

## Releasing a new autodoc version

autodoc uses semantic versioning in `auto_model_docs/pyproject.toml`
(`project.version`). To cut a release:

1. **Bump the version** in `pyproject.toml` — patch for fixes, minor for
   additive changes, major for breaking API changes.
2. **Update CHANGELOG / release notes** if the repo keeps one.
3. **Commit** the version bump on `master`:
   ```bash
   git commit -am "chore(autodoc): release 0.2.0"
   ```
4. **Tag** the commit:
   ```bash
   git tag -a autodoc-v0.2.0 -m "autodoc 0.2.0"
   git push origin master --tags
   ```
   Use the `autodoc-v<version>` prefix so tags sort independently from
   other components in the repo.
5. **Build the wheel** from the tagged commit and attach it to a GitHub
   release for the tag, so tenants have a stable download URL.
6. **Notify** tenants that a new version is available; they follow the
   rebuild steps in section 4.

Internal PyPI publishing is a post-MVP optimization. Until release
cadence justifies the automation, vendoring wheels per tenant is the
supported path.
