---
name: htmlet
description: Publish an HTML file to the user's self-hosted htmlet server and get back a live public URL. Use when the user asks to share, publish, host, or deploy an HTML page, report, dashboard, demo, or visualization, or mentions htmlet. Also covers listing, updating, and deleting hosted pages. Requires HTMLET_SERVER and HTMLET_KEY environment variables.
---

# htmlet — publish HTML to a shareable URL

htmlet hosts single HTML files at clean public URLs (`$HTMLET_SERVER/<slug>`).
Uploading needs an API key; viewing is public — **anyone with the URL can open
the page, so never publish secrets, tokens, or private data**.

## Setup check

Both variables must be set (ask the user for them if missing — do not guess):

```bash
: "${HTMLET_SERVER:?set HTMLET_SERVER, e.g. https://share.example.com}"
: "${HTMLET_KEY:?set HTMLET_KEY (an upload API key)}"
```

Verify the server is reachable with `curl -fsS "$HTMLET_SERVER/healthz"` (returns `ok`).

## Make the page worth opening

HTML beats Markdown at information density — use that when authoring the page
(a report, plan, review, comparison, dashboard):

- **Show, don't list.** Tables for data, inline SVG for diagrams, flowcharts,
  and architecture (never ASCII art), color-coding for severity or status,
  absolute positioning for spatial layouts.
- **Structure for skimming.** Lead with the conclusion; use tabs or internal
  links for long content. The reader should get the point in one pass — no
  100-line walls of text.
- **Compare side by side.** When presenting options (designs, approaches,
  configs), render them in a labeled grid with each option's tradeoffs, not as
  sequential prose.
- **Interactive where it helps.** Sliders for tunable parameters, live preview
  panes, sortable/filterable tables, draggable cards for prioritization.
  Plain inline JS is fine — the serving sandbox allows scripts.
- **Close the loop.** On pages the user acts on (triage, ranking, config
  editing), add "copy as Markdown/JSON" buttons so their decisions flow back
  into tools or a follow-up prompt.
- **Responsive.** Relative units, flexbox/grid, `max-width:100%` on images —
  the link will be opened on phones too.

## Publish a page

Pages must be a **single self-contained HTML file** — inline all CSS/JS and
embed images as `data:` URIs. Served pages run in a sandboxed opaque origin
(scripts allowed, but no cookies/storage from the host domain).

**Auto-named URL** (server picks `html-N`; fails with 413 if over the size cap,
default 5 MiB):

```bash
curl -fsS -X POST --data-binary @page.html \
  -H "X-API-Key: $HTMLET_KEY" "$HTMLET_SERVER/api/upload"
# → {"slug":"html-1","url":"https://…/html-1","size":1234,"created":true}
```

**Named URL** (idempotent upsert — re-run the same command to update the page
in place; this is how you "redeploy" without changing the link):

```bash
curl -fsS -X PUT --data-binary @page.html \
  -H "X-API-Key: $HTMLET_KEY" "$HTMLET_SERVER/api/files/my-report"
# → {"slug":"my-report","url":"https://…/my-report","size":1234}
```

Slug rules: `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, and not one of the reserved
words `api`, `health`, `healthz`, `robots.txt`, `favicon.ico`, `favicon.svg`,
`static`. Prefer short kebab-case names describing the page (`q3-report`,
`perf-dashboard`).

After publishing, always give the user the returned `url` as the deliverable.
Updating an existing page keeps its URL — reuse the same slug via PUT; don't
POST a new auto slug for a new revision of the same page.

## Manage pages

```bash
# List hosted pages (paged; total/count/offset in the response)
curl -fsS -H "X-API-Key: $HTMLET_KEY" "$HTMLET_SERVER/api/files"

# Download a page's HTML (public, no key needed)
curl -fsS "$HTMLET_SERVER/my-report" -o my-report.html

# Delete a page (confirm with the user first — this breaks the public link)
curl -fsS -X DELETE -H "X-API-Key: $HTMLET_KEY" "$HTMLET_SERVER/api/files/my-report"
# → {"deleted":"my-report"}
```

## Share a folder

Create a folder and add existing pages. One page belongs to at most one folder;
adding it elsewhere moves it. Folder and page slugs follow the same rules.

```bash
curl -fsS -X POST -H "X-API-Key: $HTMLET_KEY" \
  -H 'Content-Type: application/json' -d '{"slug":"project"}' \
  "$HTMLET_SERVER/api/folders"
curl -fsS -X PUT -H "X-API-Key: $HTMLET_KEY" \
  "$HTMLET_SERVER/api/folders/project/pages/my-report"
# Share $HTMLET_SERVER/f/project — visitors can search and open its pages.
```

`GET /api/folders` lists folders and their pages (key required). Removing a page
from a folder keeps its public page URL. Deleting a folder keeps all its pages.
Folder links are public, including the list of page names.

## Errors

| Status | Meaning | What to do |
| --- | --- | --- |
| 400 | empty upload or invalid slug | fix the slug / check the file isn't empty |
| 401 | bad or missing API key | ask the user to check `HTMLET_KEY` |
| 409 | slug already exists (POST only) | use `PUT /api/files/<slug>` to overwrite |
| 413 | file exceeds the size cap (default 5 MiB) | shrink the page (strip embedded assets) |
| 507 | server's page limit reached | ask the user to delete old pages or raise `MAX_FILES` |

Error bodies are JSON: `{"detail": "…"}` — surface `detail` to the user.
