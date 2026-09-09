# Simpplr Extensibility Center — documentation site

A [Mintlify](https://mintlify.com) documentation site for the Simpplr platform APIs:
hand-written guides plus a full API reference generated from the OpenAPI specs.

## Layout

```
.
├── docs.json                     # Site config: theme, colors, navigation, API tabs
├── index.mdx                     # Landing page
├── get-started/                  # Overview, API selection, credentials, first request
├── authentication/               # OAuth flows for both APIs
├── concepts/                     # Platform-wide conventions
├── guides/                       # Task-oriented walkthroughs
├── api-reference/
│   ├── openapi-user.json         # GENERATED — do not edit by hand
│   └── openapi-b2b.json          # GENERATED — do not edit by hand
└── scripts/
    └── normalize_openapi.py      # Generates the two specs above
```

The site has three tabs: **Documentation** (the hand-written pages), **User API**, and
**B2B API**. The two API tabs are generated entirely from the OpenAPI specs — 142
endpoint pages in total — so no endpoint page is written or maintained by hand.

## The specs are generated, not authored

`api-reference/*.json` are **build outputs**. Both source specs need normalizing before
a strict OpenAPI validator (including Mintlify's) will accept them, so they are passed
through `scripts/normalize_openapi.py`.

Regenerate them from the upstream sources with:

```bash
python3 scripts/normalize_openapi.py <source-user-spec> api-reference/openapi-user.json
python3 scripts/normalize_openapi.py <source-b2b-spec>  api-reference/openapi-b2b.json
```

### What the normalizer changes

Two classes of fix, both mechanical and both logged on stdout:

1. **Back-references into `#/paths/...` are hoisted into `components`.**
   Both specs reuse one operation's response or schema fragment from another operation
   via `$ref` pointers that point into `#/paths/...`. Strict validators reject these,
   and the ones containing `{pathParam}` braces are not resolvable as URI fragments at
   all. The normalizer resolves each pointer and moves the target into
   `components/responses` or `components/schemas`, then rewrites every reference to it.
   8 definitions are hoisted in the User spec, 21 in the B2B spec.

2. **`{variable}` templates in OAuth flow URLs are resolved.**
   Only `servers[].url` may contain templates in OpenAPI; `tokenUrl` and
   `authorizationUrl` are plain URIs. The B2B spec's `tokenUrl` contains `{env}`, which
   fails `format: uri` validation. The normalizer substitutes the spec's own
   `servers[].variables[].default` values, so no new information is introduced.

3. **`&` and `/` in operation summaries are replaced with `and` / `or`.**
   Mintlify derives each generated page's URL slug from the operation summary. An `&`
   prevents the page being generated **at all** — verified: the User spec's
   `Get Access & Refresh Token` (the token endpoint, i.e. the most important page in
   the reference) was silently missing from the nav, giving 66 of 67 User pages. A `/`
   is stripped rather than separated, turning `Publish/Unpublish Content` into the
   slug `publishunpublish-content`. Three summaries are rewritten in total.

Operation counts are unchanged by normalization: 67 (User) + 75 (B2B) = 142.

## Why the two API tabs need separate `directory` values

`docs.json` gives each OpenAPI tab its own output directory:

```json
{ "tab": "User API", "openapi": { "source": "...", "directory": "api-reference/user" } }
{ "tab": "B2B API",  "openapi": { "source": "...", "directory": "api-reference/b2b"  } }
```

This is **required, not cosmetic**. The two specs share tag names (Content, Sites, Feed,
Files, Account, People, Recognition, Audience) *and* operation summaries — `POST
/content/sites/{site_id}/page` and `POST /b2b/content/sites/{siteId}/page` are both
"Add a page to a site" under tag "Content". Generated into one namespace, **40 of 102
slugs collided** and ~40 B2B pages silently overwrote their User API counterparts.
With separate directories all 142 pages resolve.

If you ever point a tab at a spec without a `directory`, re-check the page count.

## Known upstream spec issues

Worth resolving at the source rather than patching downstream:

- **`tokenUrl` host disagrees with `servers`.** The B2B spec declares its OAuth
  `tokenUrl` on a `simpplr.xyz` host, while `servers` declares the production host as
  `platform.app.simpplr.com`. The normalizer only resolves the `{env}` template and
  leaves the domain as authored. The prose docs use the `servers` host. Confirm which
  is correct per environment.
- **Inconsistent error bodies.** `400` responses use at least eight different shapes
  across the two specs (`field_name` vs `param` vs `fieldName`, plural `errors` array
  vs singular `error` object). Documented as-is in `concepts/errors.mdx`.
- **Inconsistent pagination parameter names.** `pageSize`/`pageToken` in some endpoint
  families, `size`/`nextPageToken` in others. Documented in `concepts/pagination.mdx`.
- **Attribute casing drift.** The audience rule schema documents camelCase attribute
  names while its own examples use snake_case. Noted in
  `concepts/audiences-and-abac.mdx`.

## Running locally

```bash
mintlify dev
```

Mintlify's CLI is incompatible with Node 26 — its bundled `is-online` dependency
crashes during startup. Use Node 22:

```bash
env PATH="/opt/homebrew/opt/node@22/bin:$PATH" mintlify dev
```

If startup reports `local preview on port 3000 is already starting or running` after a
crash, clear the stale lock:

```bash
rm -rf ~/.mintlify/preview-locks
```

## Checks

```bash
mintlify validate       # config + OpenAPI schema validation
mintlify broken-links   # internal link check
```

Both should pass before publishing.

## Before publishing

- [ ] Replace the placeholder brand colors in `docs.json` with Simpplr's real palette
- [x] Add `logo/` (light + dark) and `favicon`, then reference them in `docs.json`
- [ ] Add a real support URL to a `navbar.links` entry
- [ ] Fill in the tenant-specific application-registration steps in
      `get-started/client-application.mdx` (marked with a callout)
- [ ] Confirm the OAuth token host per environment (see known issues above)
