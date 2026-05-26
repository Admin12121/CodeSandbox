<div align="center">

# app-router

A secure, server-driven app router for Flask and Jinja with route-based
templates, nested layouts, partial navigation, route-local assets, API routes,
CSRF protection, and server-rendered fallbacks.

<p>
  <img alt="Release v0.1.0" src="https://img.shields.io/badge/RELEASE-v0.1.0-e6b8b8?labelColor=2f2d42&style=flat-square">
  <img alt="Stars 0" src="https://img.shields.io/badge/STARS-0-b9b7ee?labelColor=2f2d42&style=flat-square">
  <img alt="Issues 0 open" src="https://img.shields.io/badge/ISSUES-0%20OPEN-f4a77b?labelColor=2f2d42&style=flat-square">
  <img alt="Contributors 1" src="https://img.shields.io/badge/CONTRIBUTORS-1-aee3a2?labelColor=2f2d42&style=flat-square">
</p>

</div>

## Overview

`app-router` is a Flask extension that adds a Next.js-style project shape to
normal Flask and Jinja applications. Flask remains responsible for routing,
auth, request handling, sessions, and responses. Jinja remains responsible for
templates and reusable UI macros. The bundled JavaScript only enhances
same-origin link navigation; direct requests and JavaScript-disabled browsers
still receive normal server-rendered HTML.

The Python decorators are the source of truth. Template folders organize views,
but files do not create routes by themselves.

```txt
Route declared + matching page.html exists    -> render page
Route declared + page.html missing            -> 404
page.html exists + no declared route          -> unreachable
```

## Features

- `@router.page(...)` for server-rendered page routes.
- `@router.api(...)` for JSON/API routes.
- Next-style template mapping: `/blog/<slug>` maps to `blog/[slug]/page.html`.
- Automatic root and nested `layout.html` wrapping.
- Internal DOM boundaries for partial navigation.
- Built-in client runtime served from `/_app/router.js`.
- Local asset rewriting for `./file.ext` and `@/path/file.ext`.
- Manifest-backed asset serving through `/_app/assets`.
- CSRF protection for unsafe page and API methods.
- Same-origin redirect validation.
- Built-in fallback 404 and 500 templates.
- Jinja globals: `csrf_token`, `csrf_input`, `cn`, `html_attrs`, and
  `app_router`.
- Default security headers with configurable CSP.

## Requirements

- Python 3.10+
- Flask
- Jinja2
- Werkzeug
- MarkupSafe
- itsdangerous

This repository includes `pyproject.toml` package metadata and the `app-router`
console script entry point. It does not currently include a dependency lock file
or tests.

## Quick Start

```python
from flask import Flask
from app_router import AppRouter

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-me"

router = AppRouter(app)


@router.page("/")
def home():
    return {
        "message": "Hello",
        "_meta": {"title": "Home"},
        "_cache": True,
        "_ttl": 60,
    }


@router.api("/api/users")
def users():
    return {"users": []}
```

The matching template for `/` is:

```html
<!-- templates/page.html -->
<h1>{{ message }}</h1>
```

Run the Flask app normally. The first request renders full HTML. Same-origin
links are enhanced by the packaged browser runtime when JavaScript is available.

## Project Shape

A typical consuming Flask app can organize templates like this:

```text
templates/
  layout.html
  page.html
  about/
    page.html
  admin/
    layout.html
    settings/
      page.html
  dashboard/
    page.html
    _components/
      sidebar.html
  components/
    ui/
      button.html
      card.html
static/
  app.css
```

Meaning:

- `layout.html`: shared layout wrapper.
- `page.html`: route page template.
- `[slug]`: dynamic route segment folder.
- `components/ui/`: conventional location for reusable Jinja macro files.
- `_components/`: conventional location for route-local Jinja macro files.
- `static/`: normal Flask public static assets.

The router itself only gives special meaning to `layout.html`, `page.html`, and
dynamic folders such as `[slug]`. Component folders are normal Jinja template
organization and do not create routes.

## Pages

Page routes register Flask routes and render matching Jinja page templates.

```python
@router.page("/admin/settings", methods=["GET", "POST"])
def settings():
    return {
        "section": "settings",
        "_meta": {
            "title": "Settings",
            "description": "Manage account settings",
        },
    }
```

The router handles the page lifecycle:

```txt
1. Confirm the matching page template exists.
2. Validate CSRF for unsafe methods when enabled.
3. Call the page loader.
4. Convert the loader result into template context or redirect response.
5. Render page.html.
6. Wrap it with available layout.html files.
7. Return full HTML or a partial JSON patch response.
```

Page loaders may return:

- `dict`: template context.
- `None`: empty context.
- `RedirectResult`: from `router_redirect(...)`.
- `flask.Response`: direct custom response.

Special dictionary keys:

- `_meta`: `title`, `description`, and `image`.
- `_cache`: enables public full-page cache headers when truthy.
- `_ttl`: cache lifetime in seconds when `_cache` is enabled.
- `_redirect`: redirect URL.
- `_status`: redirect status code, defaulting to `303`.
- `_message`: redirect message included in partial redirect responses.

By default, page responses use `Cache-Control: no-store`. Enable `_cache` only
for public pages.

## Route Mapping

Default route-to-template mapping:

```text
/                         -> templates/page.html
/about                    -> templates/about/page.html
/data/<slug>              -> templates/data/[slug]/page.html
/user/<id>                -> templates/user/[id]/page.html
/blog/<year>/<slug>       -> templates/blog/[year]/[slug]/page.html
/admin/settings           -> templates/admin/settings/page.html
```

You can override the template:

```python
@router.page("/profile", template="account/profile.html")
def profile():
    return {}
```

Catch-all Flask `path` converters are rejected for page routes. Mixed
static/dynamic route segments such as `post-<id>` are also rejected by the
mapper.

## Blueprints

The router can bind to either a Flask app or a Blueprint.

```python
from flask import Blueprint
from app_router import AppRouter

bp = Blueprint("site", __name__, template_folder="templates")
router = AppRouter(bp)


@router.page("/")
def index():
    return {}
```

When bound to a Blueprint, the router initializes itself on the parent Flask app
when the blueprint is registered.

## Layouts

Layouts wrap pages automatically. Do not use Jinja `{% extends %}` for the
router layout chain.

```html
<!-- templates/layout.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
  </head>
  <body>
    <nav>...</nav>
    {{ children|safe }}
  </body>
</html>
```

Nested layouts wrap descendants:

```text
templates/layout.html
  -> templates/admin/layout.html
      -> templates/admin/settings/page.html
```

For `/admin/settings`, the router looks for:

```text
templates/layout.html
templates/admin/layout.html
templates/admin/settings/page.html
```

The router injects internal boundaries around layout children:

```html
<div data-router-boundary="root">
  <div data-router-boundary="admin">
    ...
  </div>
</div>
```

Developers should render `children` where child content belongs. The boundaries
are generated by the router and used by the client runtime.

## Jinja Components

The package does not implement a separate component framework. Reusable
components are standard Jinja macros organized by convention.

Global component convention:

```text
templates/components/ui/button.html
templates/components/ui/card.html
```

Example:

```jinja
{# templates/components/ui/button.html #}
{% macro button(variant="default") %}
  <button class="{{ cn('button', 'button-' ~ variant) }}">
    {{ caller() }}
  </button>
{% endmacro %}
```

Usage:

```jinja
{% import "components/ui/button.html" as ui %}

{% call ui.button(variant="primary") %}
  Save
{% endcall %}
```

Route-local component convention:

```text
templates/dashboard/_components/sidebar.html
```

Usage:

```jinja
{% import "dashboard/_components/sidebar.html" as dashboard %}
{{ dashboard.sidebar() }}
```

These files are not routed or served by `app-router`; they are normal Jinja
templates imported by other templates.

## APIs

API routes return JSON and do not render templates, layouts, or partial page
patches.

```python
@router.api("/api/users")
def users():
    return {"users": []}
```

Tuple-style return values are supported:

```python
return {"created": True}, 201, {"X-App": "app-router"}
```

Unsafe API methods are CSRF-protected by default when the route is registered
with `POST`, `PUT`, `PATCH`, or `DELETE`.

API responses use `Cache-Control: no-store`.

## Client Navigation

The first page load is normal Flask SSR:

```http
GET /admin/settings
```

The returned HTML includes router state metadata and the package-owned client
script:

```html
<script type="module" src="/_app/router.js" data-app-router-client></script>
```

When a user clicks a same-origin link, the client script sends:

```http
GET /admin/settings
X-Flask-Router: partial
X-Flask-Current-Path: /admin
X-Flask-Current-Tree: root,admin
Accept: application/json
```

The server then:

```txt
1. Resolves the current route from X-Flask-Current-Path.
2. Recomputes the server-side current layout tree.
3. Compares it with X-Flask-Current-Tree.
4. Builds the target route layout tree.
5. Finds the deepest shared boundary.
6. Renders the needed page/layout fragment.
7. Returns a JSON patch response.
```

Supported partial response modes:

- `patch`: replace a DOM boundary with server-rendered HTML.
- `reload`: perform normal full-page navigation.
- `redirect`: navigate to a redirect target.

If the response is not JSON, the state does not match, the boundary is missing,
or fetch fails, the client falls back to `window.location.assign(...)`.

## Partial Response Shape

A patch response looks like this:

```json
{
  "mode": "patch",
  "url": "/admin/settings",
  "boundary": "admin",
  "html": "...rendered html...",
  "tree": ["root", "admin"],
  "meta": {
    "title": "Settings",
    "description": "Manage account settings",
    "image": "/static/og/settings.png"
  },
  "cache": false,
  "scripts": [],
  "styles": []
}
```

Metadata from `_meta` is applied on full HTML responses and updated during
partial navigation:

- `title` updates `<title>` or `document.title`.
- `description` updates the description meta tag.
- `image` updates `og:image`.

## Forms and CSRF

Forms are normal Flask forms. They should work without JavaScript.

Unsafe page and API methods are protected by default when registered with
`POST`, `PUT`, `PATCH`, or `DELETE`.

```jinja
<form method="post">
  {{ csrf_input() }}
  <button type="submit">Save</button>
</form>
```

For JavaScript requests, send the token in either supported header:

```http
X-CSRF-Token: ...
X-CSRFToken: ...
```

CSRF behavior:

- Tokens require Flask `SECRET_KEY`.
- Tokens are signed with `itsdangerous.URLSafeTimedSerializer`.
- Tokens are tied to a per-session seed.
- The default token max age is 8 hours.
- Override max age with `APP_ROUTER_CSRF_MAX_AGE`.

## Local Assets

Route templates and layouts can explicitly reference local assets:

```html
<script type="module" src="./index.js"></script>
<link rel="stylesheet" href="./style.css">
<img src="./hero.webp" alt="Hero">
```

`./file.ext` resolves relative to the template file that contains the import.
Only same-directory simple filenames are allowed for `./` imports.

Reusable assets can use the `@/` alias:

```html
<script type="module" src="@/admin/settings/index.js"></script>
<link rel="stylesheet" href="@/components/ui/button.css">
```

`@/path.ext` resolves through the active Jinja loader using the path after
`@/`.

Only explicit imports are rewritten. A file existing next to `page.html` does
not load automatically.

Rewritten output uses opaque hashed URLs:

```html
<script type="module" src="/_app/assets/a8f31c4d9e0f12345678.js"></script>
```

Allowed extensions:

```text
.js, .css, .png, .jpg, .jpeg, .webp, .svg, .woff, .woff2
```

Security rules implemented by the asset resolver:

- No direct filesystem paths in browser asset URLs.
- No `../` imports.
- No backslashes in alias imports.
- Unsupported extensions are rejected.
- Missing files are rejected.
- Same-directory symlink escapes are rejected for `./` imports.
- Alias imports with symlink path parts are rejected.
- Unknown asset IDs return 404.
- Served assets include `X-Content-Type-Options: nosniff`.

Route-local assets are public, immutable-cache assets by default because
`private_assets` defaults to `False` in `@router.page(...)`.

```text
Cache-Control: public, max-age=31536000, immutable
```

Use `private_assets=True` when route-local assets should not be cached:

```python
@router.page("/admin", private_assets=True)
def admin():
    return {}
```

Important: `private_assets=True` does not mean the asset route becomes
authentication-aware. In the current code it only changes asset cache headers to
`Cache-Control: no-store`. Protect private pages with normal Flask auth
decorators or checks on the page route, and do not put secrets in client
JavaScript or CSS.

Shared public files can still live in Flask's normal `static/` folder. CDN
assets require a CSP change because the default CSP is same-origin.

## Build Metadata

The CLI builds route and asset metadata without executing page loaders or
pre-rendering HTML. In the normal case, run one command from your Flask project
root:

```bash
app-router build
```

The command auto-detects a Flask app from `FLASK_APP` or common modules such as
`app.py`, `wsgi.py`, `main.py`, and `application.py`. It looks for `app`,
`application`, `create_app()`, or `make_app()`.

Output:

```text
.app-router/
  manifest.json
  routes.json
  assets/
```

What the build does:

- Scans declared page and API routes.
- Checks whether each page template exists.
- Finds matching `layout.html` files.
- Scans matching page/layout template source for explicit asset imports.
- Resolves `./file.ext` and `@/path/file.ext`.
- Copies hashed assets into `.app-router/assets/`.
- Writes `manifest.json` for runtime asset lookup.
- Writes `routes.json` for route metadata.

What the build does not do:

- It does not execute page loaders.
- It does not pre-render HTML.
- It does not produce static pages.
- It does not decide static versus dynamic rendering; route metadata currently
  records dynamic rendering.

At runtime, the router automatically loads `.app-router/manifest.json`
when it exists. You can override the build directory:

```python
router = AppRouter(app, build_dir="build/app-router")
```

or through Flask config:

```python
app.config["APP_ROUTER_BUILD_DIR"] = "build/app-router"
```

## Publishing

Version `0.1.0` is packaged with `pyproject.toml`.

Build the source distribution and wheel:

```bash
python -m build
```

Validate the distributions:

```bash
python -m twine check dist/*
```

Upload to TestPyPI first:

```bash
python -m twine upload --repository testpypi dist/*
```

Upload to PyPI after the TestPyPI install check passes:

```bash
python -m twine upload dist/*
```

## Error Pages

The package installs Flask error handlers for 404 and 500 responses. It also
ships fallback `404.html` and `500.html` package templates.

Override them by creating app templates with the same names:

```text
templates/
  404.html
  500.html
```

Error templates receive:

- `status_code`
- `message`
- `error`
- `router`
- `app_router`

If no template is found, the router renders a minimal HTML document shell.
Error responses use `Cache-Control: no-store`.

## Built-In Jinja Helpers

The router installs these globals:

- `csrf_token(name="default")`: return a signed token string.
- `csrf_input(name="default")`: render a hidden CSRF input.
- `cn(...)`: merge CSS class names from strings, mappings, and iterables.
- `html_attrs(...)`: render escaped HTML attributes.
- `app_router`: current router instance.

Example:

```jinja
<form method="post">
  {{ csrf_input() }}
  <button
    {{ html_attrs(
      class_=cn("button", {"button-primary": primary}),
      disabled=disabled
    ) }}
  >
    Save
  </button>
</form>
```

Keyword names ending in `_` are rendered without the trailing underscore, so
`class_` becomes `class`.

## Configuration

```python
router = AppRouter(
    app,
    asset_url_path="/_app/assets",
    client_url_path="/_app/router.js",
    partial_header="X-Flask-Router",
    security_headers=True,
    csrf=True,
    build_dir=".app-router",
)
```

Defaults:

- `asset_url_path`: `/_app/assets`
- `client_url_path`: `/_app/router.js`
- `partial_header`: `X-Flask-Router`
- `security_headers`: enabled
- `csp`: same-origin default CSP
- `csrf`: enabled
- `build_dir`: `.app-router`

Default CSP:

```text
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
font-src 'self';
object-src 'none';
base-uri 'self';
frame-ancestors 'none'
```

Flask config:

- `SECRET_KEY`: required for CSRF token signing.
- `APP_ROUTER_CSRF_MAX_AGE`: CSRF max age in seconds.
- `APP_ROUTER_BUILD_DIR`: alternate runtime manifest directory.

## Security Assessment

No critical vulnerability was identified in the reviewed source.

Implemented protections:

- Python decorators are the route source of truth; templates alone do not expose
  routes.
- Missing page templates return a 404.
- Page and API routes use the same loader path for full and partial requests,
  so normal Flask auth checks still apply.
- CSRF is enabled by default for unsafe page and API methods.
- Redirects are limited to local paths or same-origin absolute URLs.
- Partial navigation headers are treated as state hints. Mismatch causes reload.
- Asset serving is manifest-backed and only serves registered opaque asset IDs.
- Asset resolution rejects traversal, unsupported extensions, missing files,
  and symlink escapes covered by the resolver.
- API, partial, error, and uncached page responses use `Cache-Control: no-store`.
- Public assets use immutable cache headers and `nosniff`.
- Default security headers include CSP, `X-Content-Type-Options`, and
  `Referrer-Policy`.

Known limitations and risks:

- The default CSP allows inline styles with `style-src 'unsafe-inline'`.
- Partial navigation uses `innerHTML` to insert trusted server-rendered HTML.
  Keep Jinja autoescape enabled and avoid marking untrusted input as safe.
- Inline page scripts are not given a lifecycle by the client runtime; prefer
  explicit module files.
- `private_assets=True` does not enforce route authorization.
- Build metadata stores source paths in `manifest.json`; keep build artifacts
  out of public source disclosure channels if paths are sensitive.
- This source tree does not include a dependency lock file or tests.

Recommended hardening:

- Add tests for route mapping, layout wrapping, CSRF, redirects, asset resolver
  security, manifest loading, error templates, and partial navigation.
- Use a stricter CSP if consuming templates do not need inline styles.
- Sanitize rich text before rendering it into templates.
- Protect private pages with normal Flask auth/RBAC and keep secrets out of
  frontend assets.

## Current Limitations

- No automatic route creation from files.
- No catch-all page routes using Flask `path` converters.
- No static pre-rendering.
- No streaming rendering.
- No JavaScript component hydration.
- No bundled UI component library.
- No automatic frontend bundling or TypeScript pipeline.
- No prefetching.
- No page-specific JavaScript init/destroy lifecycle.
- No auth-aware private asset serving.
- No nested error boundaries.

## Project Structure

```text
app-router/
  pyproject.toml       Package metadata and app-router console script
  app_router/
    __init__.py        Public API exports
    router.py          Core router, rendering, headers, errors, and internal routes
    assets.py          Asset resolver, HTML rewriter, manifest builder, and server
    csrf.py            CSRF token generation and validation
    helpers.py         Jinja and routing helpers
    responses.py       Redirect response helper
    exceptions.py      Package-specific exceptions
    static/router.js   Partial-navigation browser runtime
    templates/         Built-in 404 and 500 templates
  README.md            Project documentation
```

## Verification

This README was matched against the current source files in this directory.
The package builds successfully as an sdist and wheel. The wheel installs in a
temporary environment, imports as `app_router`, exposes the `app-router` console
script, and `app-router build` generates `.app-router/` metadata for a smoke
test Flask app.
