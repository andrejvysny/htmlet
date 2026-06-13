# htmlet

Host a single HTML file at a clean public URL. Upload `page.html`, get back
`https://your-domain/html-1` — it renders in the browser as real HTML.

Uploading needs an API key. Viewing is public.

## Run it

```bash
cp .env.example .env     # then edit .env and set a strong API_KEYS
mkdir -p data            # uploaded pages are stored here (bind-mounted to /data)
docker compose up -d --build
# open http://localhost:8080
```

Pages persist in `./data` on the host. On a **Linux** server the dir must be writable by the non-root container
user: `sudo chown 10001:10001 data` (not needed on Docker Desktop / macOS).

Generate a key for `API_KEYS`:

```bash
openssl rand -hex 32
```

## Use it

**Web UI** — open http://localhost:8080 or https://your-domain, sign in with your key, drop an `.html`
file, and get the live URL. The page list lets you replace or delete pages.

**curl**

```bash
KEY=your-api-key
SRV=http://localhost:8080

# Upload (auto URL like /html-1)
curl -X POST --data-binary @page.html -H "X-API-Key: $KEY" $SRV/api/upload

# Upload to a chosen name
curl -X PUT --data-binary @page.html -H "X-API-Key: $KEY" $SRV/api/files/landing

# List / delete
curl -H "X-API-Key: $KEY" $SRV/api/files
curl -X DELETE -H "X-API-Key: $KEY" $SRV/api/files/landing
```

## Config

All settings are environment variables — see `.env.example`. The only required
one is `API_KEYS` (comma-separated; add a key per user, remove one to revoke).
