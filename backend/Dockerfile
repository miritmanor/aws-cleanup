# The API and the core library it drives. Built from the REPO ROOT (see docker-compose.yml).
# The image runs Python 3.12, but aws_resource_audit/ must stay 3.9-compatible for the CLI.

FROM python:3.12-slim AS base

# Unbuffered, so scan progress streams to `docker compose logs -f` as it happens.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUDIT_DATA_DIR=/data

WORKDIR /srv

# Dependency metadata first, so a code change does not re-resolve every wheel.
COPY pyproject.toml requirements.txt ./
COPY backend/pyproject.toml ./backend/

# CPU-only torch first: the PyPI build pulls several GB of CUDA a laptop cannot use.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch

RUN mkdir -p backend/app && touch backend/app/__init__.py \
 && pip install --no-cache-dir ./backend -r requirements.txt

COPY aws_resource_audit ./aws_resource_audit
COPY backend/app ./backend/app

# Both packages, --no-deps because the layer above already resolved them.
RUN pip install --no-cache-dir --no-deps . ./backend

# Both test suites run during the build, so an image whose tests fail is never produced.
FROM base AS test
# No test may download a model: a test that tries fails here instead of needing the internet.
ENV HF_HUB_OFFLINE=1
COPY .gitignore aws_resource_audit.py aws_regenerate_report.py cli_output.py ./
COPY tests ./tests
COPY tools ./tools
COPY help/cli-messages.md ./help/
COPY backend/apitests ./backend/apitests
# -b holds back a passing test's printed output, so the log shows only failures.
RUN python -m unittest discover -b -s . -t . \
 && python -m unittest discover -b -s backend -t backend \
 && touch /tmp/tests-passed

FROM base AS runtime
# Without this line BuildKit would skip the test stage: nothing else depends on it.
COPY --from=test /tmp/tests-passed /tmp/tests-passed

# Not root: the container holds AWS credentials and writes a full account inventory.
RUN useradd --create-home --uid 10001 audit \
 && mkdir -p /data && chown audit:audit /data
USER audit

WORKDIR /srv/backend

EXPOSE 8000

# One worker on purpose: the scan lock and its global state live in process memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
