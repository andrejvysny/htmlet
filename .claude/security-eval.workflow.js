export const meta = {
  name: 'htmlet-security-eval',
  description: 'Adversarial multi-agent security & safety review of the htmlet HTML-hosting app (Docker + Traefik deployment)',
  whenToUse: 'Evaluate the security posture of html-share/htmlet before/after deploying via Docker behind Traefik.',
  phases: [
    { title: 'Review', detail: '8 threat-dimension finders + a completeness critic, in parallel' },
    { title: 'Verify', detail: 'adversarially refute each finding; drop false positives and by-design behavior' },
    { title: 'Synthesize', detail: 'prioritized report + concrete fixes incl. hardened Traefik config' },
  ],
}

// args = absolute repo root. Fall back to the known path if not provided.
const ROOT = (typeof args === 'string' && args) || '/Users/williambrach/Developer/html-share'

// ---- Shared context handed to every agent ------------------------------- //
const APP_SUMMARY = `
APP: "htmlet" (dir: html-share). A small self-hosted FastAPI service that hosts single
HTML files at clean public URLs with token-gated uploads. Deployment target stated by the
owner: Docker, reverse-proxied by **Traefik** (NOTE: the repo currently only ships a
Caddyfile + an optional commented-out Caddy compose service — there is NO Traefik config
present yet; treat that absence as in-scope).

KEY FILES (read them yourself; do not assume):
  ${ROOT}/app/main.py        - routes: GET / (admin UI), /healthz, /favicon.ico, /robots.txt,
                                /api/login, /api/logout, /api/session, POST /api/upload,
                                PUT/GET/DELETE /api/files[/{slug}], and the public catch-all GET /{name}.
                                Upload size enforcement (_reject_oversized_content_length,
                                _read_body_capped, _extract_upload), serve headers, CSPs, cookie set.
  ${ROOT}/app/security.py    - slug regex + reserved slugs, API-key constant-time compare,
                                HMAC-SHA256 signed session token, session cookie validation,
                                require_auth dependency, X-HS-Auth + SameSite=Strict CSRF story.
  ${ROOT}/app/storage.py     - filesystem storage; _path_for resolve+parent guard, O_EXCL create,
                                tempfile+rename overwrite, auto-slug allocation, capacity/count.
  ${ROOT}/app/config.py      - env-driven Settings, fail-closed validate(), session secret derivation.
  ${ROOT}/app/templates/index.html - admin UI (inline JS): login, drop-zone upload, page list,
                                replace/delete. DOM construction (textContent vs innerHTML, CSS.escape).
  ${ROOT}/Dockerfile         - python:3.12-slim, non-root uid 10001, healthcheck, uvicorn --proxy-headers.
                                NOTE: installs UNPINNED deps via 'uv pip install ... >=' (NOT from uv.lock).
  ${ROOT}/docker-compose.yml - htmlet service + commented Caddy service; direct 8080 host port mapping;
                                API_KEYS passed as env; named volume htmlet-data:/data.
  ${ROOT}/Caddyfile          - the CURRENT reverse-proxy config (Caddy, not Traefik).
  ${ROOT}/.env.example       - documents env vars; default API_KEYS=change-me-please.
  ${ROOT}/.dockerignore, ${ROOT}/.gitignore - exclude data/, .env, etc.
  ${ROOT}/pyproject.toml, ${ROOT}/uv.lock - deps: fastapi>=0.110, uvicorn[standard]>=0.29, python-multipart>=0.0.9.
`

const THREAT_MODEL = `
THREAT MODEL — reason about these four adversaries explicitly:
  A1 Authenticated uploader (holds a valid API key): hosts arbitrary HTML/JS by design.
     Question is BLAST RADIUS — can they escape /data, read host files, traverse paths,
     overwrite other tenants' pages, exhaust resources, or pivot to the admin/host?
  A2 Anonymous internet client (no key): can they bypass auth (write/list/delete), read
     arbitrary files, enumerate, forge a session token/cookie, or DoS the service?
  A3 Victim viewing a MALICIOUS hosted page: attacker JS runs in the victim's browser
     under this service's origin. Can it steal the admin session cookie, read same-origin
     data, drive the write API via the victim's session (CSRF), attack other hosted pages,
     or escape the sandbox CSP? Does keeping pages on an isolated subdomain actually contain it?
  A4 Network/proxy-position adversary: host-header spoofing, X-Forwarded-* spoofing,
     TLS downgrade/MITM, cache poisoning, log injection.

DESIGN INTENT (do NOT report these as vulnerabilities — they are the product):
  * Hosting arbitrary attacker-controlled HTML and JavaScript at public URLs IS the feature.
  * Public unauthenticated VIEWING of hosted pages is intended.
  * The sandbox CSP deliberately still allows the page's OWN scripts/forms to run.
  Report a finding ONLY if it lets an adversary do something BEYOND "host/serve active HTML on
  an isolated host" — e.g. break isolation, bypass auth, escape the container/data dir, DoS,
  leak secrets, or attack the admin plane.
`

const RUBRIC = `
SEVERITY: critical (remote auth bypass / RCE / container or data-dir escape / secret leak),
high (cross-page or admin compromise, persistent DoS with trivial effort, isolation break),
medium (conditional/limited-impact issue, missing important defense-in-depth control),
low (hardening gap, info disclosure of low value), info (note / best-practice).
For EACH finding give a concrete, reproducible attack scenario and a specific fix. Prefer few
HIGH-CONFIDENCE findings over speculation; mark confidence honestly. Cite file:line.
`

// ---- Schemas ------------------------------------------------------------- //
const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['dimension', 'findings'],
  properties: {
    dimension: { type: 'string' },
    summary: { type: 'string', description: '1-3 sentence posture summary for this dimension' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'title', 'severity', 'threat_actor', 'location', 'description', 'attack_scenario', 'recommendation', 'confidence'],
        properties: {
          id: { type: 'string', description: 'short stable id, e.g. AUTH-1' },
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
          threat_actor: { type: 'string', enum: ['A1', 'A2', 'A3', 'A4', 'mixed'] },
          location: { type: 'string', description: 'file:line or component' },
          description: { type: 'string' },
          attack_scenario: { type: 'string' },
          recommendation: { type: 'string' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'is_real', 'is_by_design', 'adjusted_severity', 'exploitability', 'reasoning'],
  properties: {
    id: { type: 'string' },
    is_real: { type: 'boolean', description: 'true only if a genuine security issue beyond intended design' },
    is_by_design: { type: 'boolean', description: 'true if this is intended product behavior, not a bug' },
    adjusted_severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
    exploitability: { type: 'string', description: 'how hard to exploit + preconditions' },
    reasoning: { type: 'string' },
  },
}

const GAPS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['gaps'],
  properties: {
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['area', 'why_it_matters', 'severity'],
        properties: {
          area: { type: 'string' },
          why_it_matters: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
        },
      },
    },
  },
}

const SYNTHESIS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['posture_rating', 'executive_summary', 'confirmed_findings', 'missing_controls', 'traefik_hardening', 'prioritized_remediations', 'residual_risk'],
  properties: {
    posture_rating: { type: 'string', enum: ['strong', 'adequate-with-fixes', 'weak', 'unsafe-to-deploy'] },
    executive_summary: { type: 'string' },
    confirmed_findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'title', 'severity', 'location', 'impact', 'fix'],
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
          location: { type: 'string' },
          impact: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
    missing_controls: { type: 'array', items: { type: 'string' } },
    traefik_hardening: { type: 'string', description: 'concrete, paste-ready guidance: compose labels, TLS, security-headers + rate-limit middleware, network isolation, port removal, FORWARDED_ALLOW_IPS / BASE_URL' },
    prioritized_remediations: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['priority', 'action', 'severity_addressed'],
        properties: {
          priority: { type: 'integer' },
          action: { type: 'string' },
          severity_addressed: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
        },
      },
    },
    residual_risk: { type: 'string', description: 'what risk remains even after all fixes (honest accounting)' },
  },
}

// ---- Dimensions ---------------------------------------------------------- //
const DIMENSIONS = [
  {
    key: 'auth-session',
    label: 'find:auth+session',
    focus: `Authentication, session tokens, and CSRF. Scrutinize: constant-time API-key compare
(hmac.compare_digest) vs any leak; the HMAC-SHA256 signed session token — can it be forged,
replayed, or have its expiry/clock-skew (-300..session_max_age) abused? Is the session secret
derivation (sha256 over sorted API keys when SESSION_SECRET unset) predictable or rotatable in
a way that weakens it? Cookie flags (HttpOnly, SameSite=Strict, Secure only when scheme==https
— what if behind a proxy and scheme detection fails?). The X-HS-Auth:1 + SameSite=Strict CSRF
defense for cookie auth — does it actually hold against A3 (a same-site sandboxed page) and A4?
allow_anonymous footgun. logout cookie deletion. login endpoint behavior on bad key.`,
  },
  {
    key: 'content-isolation-xss',
    label: 'find:isolation+xss',
    focus: `The core risk: hosting active HTML/JS. Analyze the served-page CSP
("sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"): does the
ABSENCE of allow-same-origin truly force an opaque origin so a hosted page CANNOT read this
domain's cookies/localStorage, call the authenticated API with the admin's cookies, or touch
other hosted pages? Can a hosted page still fetch same-site URLs carrying the hs_session cookie
(SameSite=Strict means same-site requests DO send it — does X-HS-Auth save it)? MIME handling +
X-Content-Type-Options nosniff; serving user HTML as text/html. Clickjacking (served pages have
no frame-ancestors; admin UI has frame-ancestors none). The admin UI's OWN DOM-XSS surface in
index.html (innerHTML vs textContent vs createElement, CSS.escape on slug, the friendly()/status
strings, file names). The _INDEX_CSP for the admin page (unsafe-inline script/style). Whether the
"isolated subdomain" advice is necessary and what breaks without it.`,
  },
  {
    key: 'path-traversal-storage',
    label: 'find:path+storage',
    focus: `Filesystem safety in storage.py. Verify the slug regex
^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ + RESERVED_SLUGS fully prevents traversal/abs-path/NUL/dot
tricks; the _path_for resolve()+parent==data_dir guard; the ".html" suffix strip in serve()
(GET /{name}); symlink following on read/write; the tempfile+os.replace overwrite and O_EXCL
auto-slug allocation for TOCTOU/races; the count_files capacity check race; tmp file cleanup on
error; data-dir/file permissions (0o644, dir mode); whether case-insensitive or unicode slugs
could collide or escape; whether reserved-slug list is complete vs the registered routes.`,
  },
  {
    key: 'dos-resource',
    label: 'find:dos+limits',
    focus: `Denial-of-service & resource exhaustion. Trace ALL upload-size enforcement paths:
Content-Length pre-check (spoofable?), streaming _read_body_capped (buffers whole body in
memory), multipart form() max_part_size/max_files/max_fields, and the post-read length check.
Memory amplification: every upload and every serve reads the full file into RAM — what's the
worst case under concurrency * MAX_UPLOAD_BYTES? MAX_FILES cap and the 507 path. CRITICAL: is
there ANY rate limiting / brute-force protection on /api/login or write endpoints? Slowloris /
slow-body (uvicorn defaults). list_files pagination + the glob/sort cost at MAX_FILES scale.
auto-slug _scan_auto_high cost. Any ReDoS in regexes. Unbounded header/cookie handling.`,
  },
  {
    key: 'http-proxy-headers',
    label: 'find:http+proxy',
    focus: `HTTP semantics, host/proxy trust, and response headers. Host-header spoofing in
_public_base/_url when BASE_URL unset; the interaction with uvicorn --proxy-headers +
FORWARDED_ALLOW_IPS (default 127.0.0.1) — what happens behind Traefik on a docker network if
it's NOT widened, and what's the risk if widened to a broad CIDR (X-Forwarded-* / scheme / IP
spoofing, log forgery)? Missing security headers on API/JSON responses vs served/index pages.
CORS posture (none configured — correct?). Public exposure of /api/docs + /api/openapi.json
(unauthenticated API surface disclosure). Open-redirect potential. Cache-Control on served
user pages (public, max-age=60) — cache poisoning / serving stale-after-delete. Response
splitting / header injection via any reflected value.`,
  },
  {
    key: 'deployment-docker',
    label: 'find:docker',
    focus: `Container & compose hardening (Dockerfile + docker-compose.yml). Non-root uid 10001
is good — but assess: read-only root filesystem? cap_drop ALL? security_opt no-new-privileges?
PID/memory/CPU limits (fork bomb / OOM)? tmpfs for /tmp (tempfile writes)? The Dockerfile
installs UNPINNED deps ('uv pip install fastapi>=... ') instead of the pinned uv.lock — supply
chain & reproducibility risk; base image python:3.12-slim patch cadence. Secrets via env
(API_KEYS visible in docker inspect / process env) vs docker secrets. The direct '8080:8080'
host port mapping (becomes an unencrypted bypass if Traefik is added and it isn't removed).
Volume ownership/permissions for /data. Healthcheck quality. Any secrets baked into the image.`,
  },
  {
    key: 'traefik-infra',
    label: 'find:traefik',
    focus: `Reverse-proxy / network architecture for the STATED Traefik deployment. The repo has
NO Traefik config at all (only a Caddyfile). Evaluate the gap and what a SECURE Traefik setup
requires: router/service labels; an ACME/TLS cert resolver and HTTPS redirect; removing the
htmlet '8080:8080' host port so ONLY Traefik is exposed; a Traefik network the app joins
(internal: true backend); setting FORWARDED_ALLOW_IPS to the Traefik container/network so scheme
detection works, and BASE_URL to the public https origin (host-spoof mitigation); a Traefik
security-headers middleware (HSTS, nosniff, referrer-policy, frame-deny for admin) as
defense-in-depth; a Traefik RATE-LIMIT middleware to compensate for the app's lack of one
(esp. /api/login + write API); serving hosted pages on an ISOLATED subdomain that shares no
cookies with anything sensitive; Traefik dashboard/api exposure (must be off or auth'd);
TLS options (min version, ciphers). Produce concrete label/middleware recommendations.`,
  },
  {
    key: 'secrets-config',
    label: 'find:secrets+config',
    focus: `Secrets & configuration hygiene. The fail-closed validate() requires API_KEYS or
explicit anonymous mode — but does it reject WEAK/DEFAULT keys? .env.example ships
API_KEYS=change-me-please and there's a real .env in the working tree; flag the risk of
deploying with a guessable/default key (no strength/entropy check). SESSION_SECRET optionality
and its derivation from keys. Whether keys or secrets can leak via logs, error messages, the
/api response bodies, OpenAPI schema, or stack traces (debug mode?). Is uvicorn/FastAPI debug
or reload off in prod? The genkey helper & docs guidance. allow_anonymous documentation/footgun.
.gitignore/.dockerignore actually covering .env and data/. Recommend concrete config-hardening
defaults (key length enforcement, startup warning on default key, etc.).`,
  },
]

// ---- Phase 1+2: find each dimension, then adversarially verify each finding ---- //
phase('Review')

const finderPrompt = (d) => `You are a senior application-security auditor doing a SECURITY & SAFETY review.
${APP_SUMMARY}
${THREAT_MODEL}
${RUBRIC}

YOUR DIMENSION: ${d.key}
${d.focus}

Read the relevant files under ${ROOT} with your tools to ground every claim in real code (cite
file:line). Be exhaustive within your dimension but do NOT invent issues; respect DESIGN INTENT.
Return findings via the structured schema.`

const verifyPrompt = (f, dimKey) => `You are a SKEPTICAL security verifier. Your job is to REFUTE the
finding below unless it is genuinely real. Default to is_real=false if uncertain or if it merely
describes intended product behavior.
${APP_SUMMARY}
${THREAT_MODEL}

CANDIDATE FINDING (dimension ${dimKey}):
${JSON.stringify(f, null, 2)}

Independently re-read the cited code under ${ROOT} and check:
  1. Is the described code/behavior accurately characterized? (re-read file:line)
  2. Does a real adversary (A1-A4) gain something BEYOND the intended design of an HTML-hosting
     service? If it's just "users can host JS / pages are public", set is_by_design=true, is_real=false.
  3. Are there existing mitigations the finder missed that neutralize it?
  4. What is the true exploitability and adjusted severity?
Return your verdict via the schema.`

const finderStage = (d) =>
  agent(finderPrompt(d), { label: d.label, phase: 'Review', schema: FINDINGS_SCHEMA, agentType: 'Explore' })

const verifyStage = (review, d) => {
  if (!review || !Array.isArray(review.findings) || review.findings.length === 0) return []
  return parallel(
    review.findings.map((f) => () =>
      agent(verifyPrompt(f, d.key), { label: `verify:${d.key}:${f.id}`, phase: 'Verify', schema: VERDICT_SCHEMA })
        .then((v) => (v ? { ...f, dimension: d.key, verdict: v } : null))
    )
  )
}

// Run the dimension pipeline AND an independent completeness critic concurrently.
const completenessPrompt = `You are a security completeness critic. The 8 dimensions under review are:
auth-session, content-isolation-xss, path-traversal-storage, dos-resource, http-proxy-headers,
deployment-docker, traefik-infra, secrets-config.
${APP_SUMMARY}
${THREAT_MODEL}
Read the code under ${ROOT}. Identify threat vectors, attack surfaces, or controls that those 8
dimensions might MISS for an app of this class (e.g. business-logic abuse, multi-tenant data
exposure via list/enumeration, abuse for phishing/malware distribution & reputation/blocklist
risk to the domain, lack of audit logging, content moderation/takedown, supply-chain of the
Google Fonts CDN dependency in the admin UI, IPv6/forwarded edge cases, backup/restore of /data,
graceful handling of disk-full). Return gaps via the schema. Be concrete, avoid duplicating the
obvious in-scope items.`

const [byDimension, critic] = await Promise.all([
  pipeline(DIMENSIONS, finderStage, verifyStage),
  agent(completenessPrompt, { label: 'completeness-critic', phase: 'Review', schema: GAPS_SCHEMA, agentType: 'Explore' }),
])

const allVerified = byDimension.flat().filter(Boolean)
const confirmed = allVerified.filter((f) => f.verdict && f.verdict.is_real)
const byDesignOrFP = allVerified.filter((f) => !f.verdict || !f.verdict.is_real)

log(`Findings: ${allVerified.length} raised, ${confirmed.length} confirmed real, ${byDesignOrFP.length} refuted/by-design. Critic gaps: ${(critic && critic.gaps ? critic.gaps.length : 0)}.`)

// ---- Phase 3: synthesize ------------------------------------------------- //
phase('Synthesize')

const synthInput = {
  confirmed_findings: confirmed.map((f) => ({
    id: f.id, dimension: f.dimension, title: f.title,
    severity: f.verdict.adjusted_severity || f.severity,
    location: f.location, threat_actor: f.threat_actor,
    description: f.description, attack_scenario: f.attack_scenario,
    recommendation: f.recommendation,
    verifier: { exploitability: f.verdict.exploitability, reasoning: f.verdict.reasoning },
  })),
  refuted_or_by_design: byDesignOrFP.map((f) => ({ id: f.id, title: f.title, why: f.verdict ? f.verdict.reasoning : 'verifier unavailable' })),
  completeness_gaps: critic && critic.gaps ? critic.gaps : [],
}

const synthesisPrompt = `You are the lead security reviewer writing the final report for the owner of
"htmlet", who will deploy it via Docker behind **Traefik** and wants it "secure without any holes if
possible".
${APP_SUMMARY}

Below is the verified evidence: confirmed findings (passed adversarial verification), items that were
refuted or are intended-by-design, and completeness gaps from an independent critic.

EVIDENCE:
${JSON.stringify(synthInput, null, 2)}

Produce the final report via the schema:
  - posture_rating: honest overall judgment.
  - executive_summary: what the app does well (it has real, thoughtful defenses — name them) and the
    top risks, in plain language.
  - confirmed_findings: dedup/merge the evidence into a clean prioritized list with concrete fixes.
  - missing_controls: defense-in-depth that's absent (e.g. rate limiting, weak-key rejection, abuse/
    takedown, audit logging) — fold in the completeness gaps where valid.
  - traefik_hardening: PASTE-READY guidance for a secure Traefik deployment — compose labels, TLS +
    HTTPS redirect, removing the 8080 host port, a security-headers middleware, a rate-limit
    middleware (to cover the app's missing rate limiting, esp. /api/login), FORWARDED_ALLOW_IPS +
    BASE_URL, isolated-subdomain hosting, dashboard off. Be specific and correct for Traefik v3.
  - prioritized_remediations: ordered action list (priority 1 = do first).
  - residual_risk: be honest — hosting arbitrary active HTML on a domain has irreducible risk;
    state what remains even after every fix (abuse/phishing/reputation), and the ONE architectural
    control that matters most (origin isolation).
Do not overstate: respect that hosting active HTML is the product's purpose.`

const report = await agent(synthesisPrompt, { label: 'synthesize-report', phase: 'Synthesize', schema: SYNTHESIS_SCHEMA })

return {
  counts: { raised: allVerified.length, confirmed: confirmed.length, refuted: byDesignOrFP.length, gaps: synthInput.completeness_gaps.length },
  report,
  confirmed_findings_detail: synthInput.confirmed_findings,
  refuted_or_by_design: synthInput.refuted_or_by_design,
}
