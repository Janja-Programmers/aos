# AOS (Africa Online Stores)

A multi-vendor marketplace platform enabling users to buy, sell, post short videos, go live, and communicate via chat and in-app calls across multiple countries.

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

Install required system packages:

```bash
sudo apt update
sudo apt install -y libgl1 ffmpeg build-essential python3-dev curl wget git git-lfs
```

### Why these are required:

- `libgl1` → required for background removal (rembg)
- `ffmpeg` → video processing (shorts)
- `build-essential` → build Python dependencies
- `python3-dev` → required for some Python packages
- `curl` / `wget` → service health checks and downloads
- `git-lfs` → required for downloading large model files such as the translation model

---

# 🐳 External Services Setup (Docker)

AOS depends on external services.
Run them using Docker Compose.

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

---

## ▶️ Step 4: Start services

```bash
docker compose up -d
```

To start only selected services:

```bash
docker compose up -d qdrant minio livekit
```

To start translation too:

```bash
docker compose up -d translation
```

---

## 🌐 Services Overview

### Vector Search (Image Search)

- Qdrant
- URL: http://localhost:6333

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

Used for:

- On-demand chat message translation
- Cached translated messages
- Multilingual buyer/seller communication

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

## Qdrant

```text
host: 127.0.0.1
port: 6333
collection: ads
```

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
enabled: 1
service_url: http://127.0.0.1:8100
timeout_seconds: 10
provider: nllb
model_name: nllb-200-distilled-1.3B-ct2-int8
max_chars: 1000
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

GitHub Actions workflows:

- CI → installs app and runs tests
- Linters → static analysis

---

# 📄 License

MIT
