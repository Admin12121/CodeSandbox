<p align="center">
  <img src="./src/codesandbox/templates/public/logo.webp" alt="CodeSandBox logo" width="96" />
</p>

<h1 align="center">CodeSandbox</h1>

<p align="center">
  <a href="https://github.com/Admin12121/codesandbox/stargazers">
    <img alt="Stars" src="https://img.shields.io/github/stars/Admin12121/codesandbox?style=for-the-badge&labelColor=34364d&color=b8b8f3">
  </a>
  <a href="https://github.com/Admin12121/codesandbox/issues">
    <img alt="Issues" src="https://img.shields.io/github/issues/Admin12121/codesandbox?style=for-the-badge&labelColor=34364d&color=f4a77c">
  </a>
  <a href="https://github.com/Admin12121/codesandbox/graphs/contributors">
    <img alt="Contributors" src="https://img.shields.io/github/contributors/Admin12121/codesandbox?style=for-the-badge&labelColor=34364d&color=a7dc9a">
  </a>
</p>

CodeSandbox is a self-hosted sandbox platform: spin up disposable, browser-based dev environments (terminal, editor, live preview) from templates, run them in isolated Docker workers, and manage usage, billing, and access across personal, organization, and platform-admin workspaces.

## Stack

- **Backend:** Flask + [NexORM](./packages/nexorm) (MySQL) + a custom [app-router](./packages/app_router) for file-based pages
- **Frontend:** server-rendered Jinja templates + Tailwind v4 (browser compiler for dev, prebuilt CSS for Windows fast mode)
- **Sandboxes:** Docker-in-Docker workers, orchestrated over NATS
- **Auth:** sessions, 2FA (TOTP), WebAuthn passkeys

## Getting started

```bash
cp .env.example .env   # fill in the required secrets — docker-compose.yml fails fast on any that are missing
docker compose up -d
```

On Docker Desktop for Windows, set `WINDOW=true` in `.env`; the app image serves baked source and prebuilt CSS instead of using Python reload/browser Tailwind compilation.

Environment variables are documented in `.env.example` and `src/codesandbox/config.py`.

## Project layout

- `src/codesandbox/features/` — one folder per domain (identity, sandbox, organizations, finance, worker, workflow, billing)
- `src/codesandbox/templates/` — Jinja pages and `components/ui/*.html` macros
- `packages/` — the two in-house libraries (`nexorm`, `app_router`) this app is built on
- `worker/` — the sandbox runtime that runs inside each worker node
