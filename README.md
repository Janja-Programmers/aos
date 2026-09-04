# AOS (Africa Online Space)

A multi-vendor marketplace platform enabling users to buy, sell, post short videos, go live, and communicate via chat and in-app calls across multiple countries.

## Documentation

Start at [`docs/README.md`](docs/README.md). Feature behavior and API semantics live under [`docs/features/`](docs/features/README.md); the complete code-derived HTTP route inventory is [`docs/api/reference.md`](docs/api/reference.md).

---

# 🚀 Installation

Install the app using bench:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app aos
```

---

# ⚙️ System Requirements

The selected backend runtime is exactly Python `3.14.6`; application metadata
accepts Python `>=3.14,<3.15`. The CI-pinned framework is stable Frappe
`v16.27.1` at commit `f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`,
with Frappe Bench `5.31.0` and Node `24.18.0`.

Install required system packages:

```bash
sudo apt update
sudo apt install -y libgl1 ffmpeg build-essential python3-dev curl wget git git-lfs
```

### Why these are required:

- `libgl1` → libgl1 / libglib dependencies are installed inside AI service containers where needed. The Frappe backend should not require rembg or ONNX Runtime system dependencies.
- `ffmpeg` → video processing (shorts)
- `build-essential` → build Python dependencies
- `python3-dev` → required for some Python packages
- `curl` / `wget` → service health checks and downloads
- `git-lfs` → required for downloading large model files such as the translation model

Image search model/runtime dependencies live in `infra/image-search` and are installed inside the image-search Docker service, not in the Frappe backend. Background-removal dependencies live in `infra/background-removal`. Translation dependencies live in `infra/translation`.

---

# 🐳 External Services Setup (Docker)

AOS depends on external services.
Run them using Docker Compose.

---

## 🧠 AI/ML Architecture

AI/ML features must run outside the Frappe business backend. The backend owns users, ads, permissions, moderation, rate limits, database records, and response serialization. AI services own model runtimes and vector infrastructure.

Current AI services:

- `image-search` → visual similarity search for ads. Owns OpenCLIP, Torch, embeddings, Qdrant access, vector scoring, and image-search thresholds.
- `translation` → chat message translation. Owns the NLLB translation runtime.
- `background-removal` → AI media processing for transparent PNG output. Owns rembg, ONNX Runtime, and background-removal model runtime.

Production rule for image search:

```text
AOS backend owns ads.
Image-search service owns vectors.
Qdrant is private infrastructure behind image-search.
Only Active ads with images should be indexed.
```

Do not import AI/ML dependencies from Frappe code. The Frappe backend should call private AI HTTP services through integration clients such as `aos.integrations.ai.image_search_client`, `aos.integrations.ai.background_removal_client`, and `aos.integrations.ai.translation_client`.

---

## 📁 Step 1: Navigate to app folder

```bash
cd apps/aos
```

---

## 📄 Step 2: Create environment file

```bash
cp .env.example .env
```

Update values inside `.env` as needed.

---

## 🌍 Step 3: Translation Model Setup

AOS uses a self-hosted translation service for chat message translation.

Default model:

```text
nllb-200-distilled-1.3B-ct2-int8
```

Create the local model directory:

```bash
mkdir -p models
```

Download the model into:

```text
models/nllb-200-distilled-1.3B-ct2-int8
```

Recommended using Hugging Face:

```bash
git lfs install
git clone https://huggingface.co/OpenNMT/nllb-200-distilled-1.3B-ct2-int8 models/nllb-200-distilled-1.3B-ct2-int8
```

Make sure `.env` points to the model path:

```env
TRANSLATION_MODEL_HOST_PATH=./models/nllb-200-distilled-1.3B-ct2-int8
```

If you do not need translation locally, you can skip starting the translation service.

Frappe should only know the private translation service URL, timeout, and maximum text length. Provider/model/device details belong to this Docker service, not chat business logic.

---

## ▶️ Step 4: Start services

```bash
docker compose up -d
```

To start only selected services:

```bash
docker compose up -d qdrant image-search background-removal minio livekit
```

To start translation too:

```bash
docker compose up -d translation
```

To start only image search and its vector store:

```bash
docker compose up -d qdrant image-search
```

To start only background removal:

```bash
docker compose up -d background-removal
```

---

## 🌐 Services Overview

### Image Search

- AOS Image Search Service
- URL: http://localhost:8110
- Health: http://localhost:8110/health
- Ready: http://localhost:8110/ready

Used for:

- Visual similarity search for ads
- Image embedding generation
- Qdrant vector indexing and search

Qdrant is still used internally, but the Frappe backend should not connect to Qdrant directly.

- Qdrant internal service: http://qdrant:6333
- Local host binding: http://127.0.0.1:6333

---

### Background Removal

- AOS Background Removal Service
- URL: http://localhost:8120
- Health: http://localhost:8120/health
- Ready: http://localhost:8120/ready

Used for:

- Removing image backgrounds
- Returning transparent PNG output
- Keeping rembg/ONNX Runtime outside the Frappe backend

The frontend should call the Frappe remove-background endpoint. It should not call this private service directly.

---

### Object Storage (Shorts)

- MinIO
- API: http://localhost:9100
- Console: http://localhost:9101

---

### Realtime (Calls & Live Streaming)

- LiveKit
- WebSocket: ws://localhost:7880

---

### Translation (Chat)

- AOS Translation Service
- Model: NLLB-200 distilled 1.3B CT2 INT8
- URL: http://localhost:8100
- Health: http://localhost:8100/health
- Ready: http://localhost:8100/ready

Used for:

- On-demand chat message translation
- Cached translated messages
- Multilingual buyer/seller communication
- Language normalization
- Returning translated text plus passive provider/model metadata

The frontend should call the Frappe chat translation endpoint. It should not call this private service directly. Frappe should call translation through `aos.integrations.ai.translation_client` only.

---

### Push Notifications

- Firebase Cloud Messaging
- No server required (managed by Firebase)

---

# 🔐 Firebase Setup (Push Notifications)

1. Go to Firebase Console
2. Create project
3. Download service account JSON
4. Add to `site_config.json`:

```json
{
  "firebase_service_account": "/absolute/path/to/service-account.json"
}
```

⚠️ Do NOT commit this file to GitHub.

---

# 📧 Email Setup (Required)

Configure Email Account in Frappe:

- SMTP server
- Email credentials
- Enable outgoing mail

Used for:

- OTP verification
- Password reset
- System notifications

---

# ⚙️ AOS Settings Configuration

After installing the app, configure **AOS Settings** in Frappe.

---

## Image Search

```text
service_url: http://127.0.0.1:8110
timeout_seconds: 20
default_limit: 20
max_limit: 100
```

For Docker-based single-server setup where Frappe runs on the host, use:

```text
http://127.0.0.1:8110
```

If Frappe is also running inside Docker on the same Docker network, use:

```text
http://aos-image-search:8000
```

Do not configure Qdrant in AOS Settings. Qdrant belongs behind the image-search service.

---

## Background Removal

```text
service_url: http://127.0.0.1:8120
timeout_seconds: 30
max_image_bytes: 10485760
```

For Docker-based single-server setup where Frappe runs on the host, use:

```text
http://127.0.0.1:8120
```

If Frappe is also running inside Docker on the same Docker network, use:

```text
http://aos-background-removal:8000
```

Do not configure rembg, ONNX Runtime, or model details in AOS Settings. Those belong to the background-removal service environment.

---

## MinIO

```text
endpoint: 127.0.0.1:9100
access_key: from .env
secret_key: from .env
public_base_url: http://127.0.0.1:9100
secure: 0
```

---

## LiveKit

```text
endpoint: ws://127.0.0.1:7880
api_key: from .env
api_secret: from .env
```

---

## Translation

```text
TRANSLATION_SERVICE_URL=http://127.0.0.1:8100  # .env
translation_service_timeout_seconds: 30          # AOS Settings
translation_max_characters: 1000                 # AOS Settings
```

For Docker-based single-server setup where Frappe runs on the host, use:

```text
http://127.0.0.1:8100
```

If Frappe is also running inside Docker on the same Docker network, use:

```text
http://aos-translation:8000
```

---

# ▶️ Running the App

```bash
bench start
```

---

# 🧪 Testing

```bash
bench run-tests --app aos
```

To check Docker services:

```bash
docker compose ps
```

To check image-search service health/readiness:

```bash
curl http://127.0.0.1:8110/health
curl http://127.0.0.1:8110/ready
```

To check translation service health:

```bash
curl http://127.0.0.1:8100/health
```

---

# 📁 Project Structure

```text
apps/aos/
├── aos/                         # Main application code
├── docker-compose.yml           # Dev/infra services
├── .env.example                 # Environment variables template
├── infra/
│   ├── image-search/
│   │   ├── Dockerfile           # Image-search service image
│   │   ├── requirements.txt     # Image-search ML/vector dependencies
│   │   └── app/
│   │       ├── main.py          # FastAPI app
│   │       ├── embedding.py     # OpenCLIP embedding runtime
│   │       ├── qdrant_store.py  # Qdrant access layer
│   │       └── service.py       # Image-search use cases
│   ├── livekit/
│   │   └── livekit.yaml         # LiveKit config
│   └── translation/
│       ├── Dockerfile           # Translation service image
│       ├── requirements.txt     # Translation service dependencies
│       └── app/
│           ├── main.py          # FastAPI app
│           ├── languages.py     # Language code mapping
│           └── translator.py    # NLLB translator runtime
├── models/
│   └── nllb-200-distilled-1.3B-ct2-int8/
├── pyproject.toml
└── README.md
```

---

# 🔐 Security Notes

- Do NOT commit `.env`
- Do NOT commit Firebase JSON
- Do NOT commit private credentials
- Always use strong passwords in production
- Do not expose internal services publicly unless protected by firewall, authentication, or reverse proxy rules

---

# 🧠 Production Notes

This Docker setup is intended for:

- Local development ✅
- Testing ✅
- Single-server deployments ✅

For high-scale production:

- Separate services into different servers
- Add SSL (HTTPS)
- Use reverse proxy (nginx)
- Use firewall rules to restrict internal services
- Monitor CPU, RAM, disk, and service health

---

## Image Search Production Notes

Image search runs as a separate internal service so heavy ML/vector dependencies do not affect Frappe workers.

Recommended production setup:

- Keep Frappe as the main AOS backend.
- Keep image search as a private Docker service.
- Keep Qdrant private and accessible only to image search.
- Do not expose image search or Qdrant publicly.
- Index only `Active` ads with images.
- Treat image search as always-on infrastructure. If it is unavailable, return a temporary unavailable error instead of disabling the feature.

Health checks:

```bash
curl http://127.0.0.1:8110/health
curl http://127.0.0.1:8110/ready
```

Manual rebuild after deployment, restore, or vector corruption:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index
```

Dry run:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"dry_run": true}'
```

## Background Removal Production Notes

Background removal runs as a separate internal service so rembg/ONNX Runtime dependencies do not affect Frappe workers.

Recommended production setup:

- Keep Frappe as the main AOS backend.
- Keep background removal as a private Docker service.
- Do not expose background removal publicly.
- Store the processed result as a new Frappe File.
- Treat background removal as always-on infrastructure. If it is unavailable, return a temporary unavailable error instead of disabling the feature.

Health checks:

```bash
curl http://127.0.0.1:8120/health
curl http://127.0.0.1:8120/ready
```

Direct service test:

```bash
curl -X POST http://127.0.0.1:8120/remove-background \
  -F "image=@/path/to/test-image.jpg" \
  --output removed-bg.png
```

## Translation Production Notes

For production, translation runs as a separate internal service so heavy ML dependencies do not affect Frappe workers.

Recommended production setup:

- Keep Frappe as the main AOS backend
- Keep translation as a separate Docker service
- Do not expose translation publicly unless protected by firewall/reverse proxy
- Use cached translations in `AOS Message Translation`
- Keep `TRANSLATION_MAX_CHARS` conservative for chat messages
- Use a stronger server or GPU if translation latency becomes high

Recommended default:

```text
Model: nllb-200-distilled-1.3B-ct2-int8
Device: cpu
Compute type: int8
Max chars: 1000
```

If translation becomes slow under real usage:

- Reduce `TRANSLATION_MAX_CHARS`
- Lower translation endpoint rate limits
- Move translation to a stronger server
- Use GPU-backed deployment
- Keep translation cached aggressively

---

# 🤝 Contributing

```bash
cd apps/aos
pre-commit install
```

Tools used:

- ruff
- eslint
- prettier
- pyupgrade

---

# 🔄 CI

The existing GitHub Actions CI and linter workflows have been consolidated into
one hardened Checkpoint 1 workflow. It targets Python `3.14.6`, stable Frappe
`v16.27.1` at commit
`f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`, Bench `5.31.0`, and Node
`24.18.0`. Runtime and service-image pins are recorded in `ci/versions.env`.

CI covers repository quality, secret/hygiene scanning, vulnerability auditing,
pinned Semgrep rules, isolated tests and complete production dependency checks
for all eight FastAPI services, a disposable full Frappe site test, Compose
validation, and infrastructure validation. The stable branch-protection check
is `CI / Required Gate`.

The correction archive does not claim those configured jobs have passed merely
because they exist. See `TEST_RESULTS.md` for the local evidence and any
fail-closed `NOT RUN` gates. In particular, branch protection should be enabled
only after the archive is pushed and the first complete GitHub-hosted gate is
green.

```bash
make help
make fast
make fastapi
make compat
make full-ci
```

See `docs/development/testing.md` for single-service and Frappe commands and
`docs/production/ci-cd.md` for job, pin-update, artifact, and branch-protection
details. This checkpoint deliberately performs no deployment.

---

# 📄 License

MIT
