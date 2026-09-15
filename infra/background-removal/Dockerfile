FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/nonexistent \
    REMBG_HOME=/opt/aos/rembg \
    NUMBA_CACHE_DIR=/var/cache/aos/numba \
    TMPDIR=/tmp \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 libglib2.0-0 \
    && groupadd --gid 10001 aosbg \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin aosbg \
    && mkdir -p /opt/aos/rembg/models/u2net /var/cache/aos/numba \
    && chown 10001:10001 /var/cache/aos/numba \
    && chmod 0700 /var/cache/aos/numba \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /app/requirements.lock

RUN python -m pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && python -m pip check

COPY app/model_manifest.json /tmp/background-removal-model-manifest.json

# The model is a versioned application artifact, not runtime state. Download it
# only while building the image and verify both AOS's SHA-256 pin and rembg's
# upstream MD5 before committing it to the immutable runtime layer.
RUN python - <<'PY'
import hashlib
import json
import pathlib
import time
import urllib.request

manifest_path = pathlib.Path('/tmp/background-removal-model-manifest.json')
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
required = {'name', 'filename', 'relative_path', 'url', 'sha256', 'upstream_md5'}
if set(manifest) != required:
    raise SystemExit('invalid background-removal model manifest fields')
if manifest['name'] != 'u2net' or manifest['relative_path'] != 'models/u2net/u2net.onnx':
    raise SystemExit('unexpected background-removal model identity')

root = pathlib.Path('/opt/aos/rembg')
destination = root / manifest['relative_path']
destination.parent.mkdir(parents=True, exist_ok=True)
temporary = destination.with_suffix('.onnx.download')

last_error = None
for attempt in range(1, 6):
    try:
        request = urllib.request.Request(
            manifest['url'],
            headers={'User-Agent': 'AOS-background-removal-image-build/1.0'},
        )
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        break
    except Exception as exc:
        last_error = exc
        temporary.unlink(missing_ok=True)
        if attempt == 5:
            raise SystemExit(f'failed to download pinned U2Net model: {exc}') from exc
        time.sleep(attempt * 2)
else:  # pragma: no cover - defensive build guard
    raise SystemExit(f'failed to download pinned U2Net model: {last_error}')

sha256 = hashlib.sha256()
md5 = hashlib.md5(usedforsecurity=False)
with temporary.open('rb') as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        sha256.update(chunk)
        md5.update(chunk)

if sha256.hexdigest() != manifest['sha256']:
    temporary.unlink(missing_ok=True)
    raise SystemExit('U2Net SHA-256 verification failed')
if md5.hexdigest() != manifest['upstream_md5']:
    temporary.unlink(missing_ok=True)
    raise SystemExit('U2Net upstream MD5 verification failed')

temporary.replace(destination)
destination.chmod(0o444)
PY

COPY --chown=root:root app /app/app

RUN chown -R root:root /app /opt/aos/rembg \
    && chmod -R a-w /opt/aos/rembg \
    && test "$(stat -c '%a' /opt/aos/rembg/models/u2net/u2net.onnx)" = "444"

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
