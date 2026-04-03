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
sudo apt install -y libgl1 ffmpeg build-essential python3-dev curl wget
```

### Why these are required:

- `libgl1` → required for background removal (rembg)
- `ffmpeg` → video processing (shorts)
- `build-essential` → build Python dependencies
- `python3-dev` → required for some Python packages

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

## ▶️ Step 3: Start services

```bash
docker compose up -d
```

---

## 🌐 Services Overview

### Vector Search (Image Search)

- Qdrant
- URL: http://localhost:6333

---

### Object Storage (Shorts)

- MinIO
- API: http://localhost:9000
- Console: http://localhost:9001

---

### Realtime (Calls & Live Streaming)

- LiveKit
- WebSocket: ws://localhost:7880

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

After installing the app, configure **AOS Settings** in Frappe:

---

## MinIO

```text
endpoint: 127.0.0.1:9000
access_key: from .env
secret_key: from .env
public_base_url: http://127.0.0.1:9000
secure: 0
```

---

## Qdrant

```text
host: 127.0.0.1
port: 6333
collection: ads
```

---

## LiveKit

```text
endpoint: ws://127.0.0.1:7880
api_key: from .env
api_secret: from .env
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

---

# 📁 Project Structure

```text
apps/aos/
├── aos/                     # Main application code
├── docker-compose.yml      # Dev/infra services
├── .env.example            # Environment variables template
├── infra/
│   └── livekit/
│       └── livekit.yaml    # LiveKit config
├── pyproject.toml
└── README.md
```

---

# 🔐 Security Notes

- Do NOT commit `.env`
- Do NOT commit Firebase JSON
- Always use strong passwords in production

---

# 🧠 Production Notes

This docker setup is intended for:

- Local development ✅
- Testing ✅
- Single-server deployments ✅

For high-scale production:

- Separate services into different servers
- Add SSL (HTTPS)
- Use reverse proxy (nginx)

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
