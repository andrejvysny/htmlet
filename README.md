# htmlet

Host a single HTML file at a clean public URL. Upload `page.html`, get back
`https://your-domain/html-1` — it renders in the browser as real HTML.

Uploading needs an API key. Viewing is public.

![htmlet web UI — drag an HTML file in and get a live URL back](docs/screenshot.png)

## Deploy

```bash
cp .env.example .env     # set API_KEYS (generate one: openssl rand -hex 32)
docker compose up -d     # pulls the prebuilt image from GHCR
# open http://localhost:8080
```

That's it — one compose file, all config comes from `.env` at start. Uploaded
pages persist in the `htmlet-data` Docker volume (created automatically).

- **Want pages as plain files on the host?** Swap the volume for `- ./data:/data` in the compose file, then `mkdir -p data && sudo chown 10001:10001 data` (sudo not needed on Docker Desktop / macOS).
- **Hacking on the code?** `docker compose up -d --build` builds from source instead.
- **Production TLS?** [docs/traefik/](docs/traefik/docker-compose.yml) adds Let's Encrypt HTTPS, rate limits, and admin/pages origin isolation.

## Use

**Web UI** — open the site, sign in with your key, drop an `.html` file, get the
live URL. The page list lets you replace or delete pages.

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

Everything is an environment variable — see [.env.example](.env.example).
Only `API_KEYS` is required (comma-separated: add a key per user, remove one to revoke).
