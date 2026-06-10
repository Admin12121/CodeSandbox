# CodeSandbox Plan

## Purpose

This plan explains how to implement the project described in `docs/project.md`: an autonomous cyber range and attack replay platform.

The product is closest to “CodeSandbox for cybersecurity”:

- disposable attack labs
- browser-based investigation UI
- sandbox runtime
- telemetry ingestion
- attack replay
- memory/disk/network forensics
- detection rule execution
- timeline generation
- case/report workflows
- RBAC for users, platform admins, and organizations

This document is an implementation plan, not a product pitch.

## Core Direction

- Use Flask as the application framework.
- Use the custom `app-router` package for page routing, layouts, route groups, colocated templates, and Jinja UI composition.
- Use the custom `nexorm` package as the ORM and migration system.
- Use Room24 only as a reference for UX shape, admin shell ideas, session/RBAC flow, and dense dashboard layout.
- Do not copy Room24 code, schema, naming, or hotel-specific product assumptions.
- Follow a feature-first architecture.

## Feature-First Architecture

The app should be organized by product capability first, not by technical layer first.

Recommended shape:

```text
src/codesandbox/
  app.py
  asgi.py
  settings.py
  container.py

  features/
    identity/
      models.py
      repository.py
      service.py
      routes.py
      permissions.py

    platform_admin/
      models.py
      repository.py
      service.py
      routes.py
      permissions.py

    organizations/
      models.py
      repository.py
      service.py
      routes.py
      permissions.py

    billing_limits/
      models.py
      repository.py
      service.py

    labs/
      models.py
      repository.py
      service.py
      routes.py

    runtime/
      models.py
      repository.py
      service.py
      routes.py
      workers.py

    storage_artifacts/
      models.py
      repository.py
      service.py

    telemetry/
      models.py
      repository.py
      service.py
      routes.py
      parsers.py

    analysis/
      models.py
      repository.py
      service.py
      workers.py

    detections/
      models.py
      repository.py
      service.py
      workers.py

    timeline/
      models.py
      repository.py
      service.py

    cases/
      models.py
      repository.py
      service.py
      routes.py

    system/
      models.py
      repository.py
      audit.py
      outbox.py

  infrastructure/
    nexorm/
      config.py
      registry.py
      transactions.py

    virtualization/
      docker_provider.py
      qemu_provider.py
      firecracker_provider.py
      libvirt_provider.py

    storage/
      object_storage.py
      evidence_store.py
      artifact_store.py

    realtime/
      asgi.py
      sse.py
      websocket.py

    workers/
      queue.py
      runner.py

  shared/
    security.py
    pagination.py
    validation.py
    result.py
    time.py
```

Each feature owns its models, service logic, repositories, permissions, and routes. Shared infrastructure exists only for cross-cutting adapters.

## App-Router Structure

The project uses `app-router`; it is not a demo dependency.

Pages should be grouped by route and feature:

```text
templates/
  layout.html

  (auth)/
    login/
      _components/
        form.html
      page.html

  (admin)/
    dashboard/
      _components/
        shell.html
        metrics.html
        nav.html
      page.html

    users/
      _components/
      page.html

    organizations/
      _components/
      page.html

  labs/
    _components/
    page.html

  runtime/
    _components/
    page.html

  cases/
    _components/
    page.html
```

Use the Jinja macro components in:

```text
src/codesandbox/templates/components/ui
```

Page-specific components belong in the page `_components` directory. Shared UI primitives stay in `components/ui`.

## NexORM Plan

NexORM is the main data layer.

Expected workflow:

```bash
nexorm init
python manage.py makemigrations
python manage.py migrate
python manage.py showmigrations
python manage.py rollback
python manage.py sqlmigrate 0001_initial.py
```

Implementation rules:

- Define database tables as NexORM models.
- Keep feature models inside their feature package.
- Import feature models through a registry if NexORM needs centralized discovery.
- Use NexORM repositories for database access.
- Use NexORM transactions for multi-step writes.
- Use `sqlmigrate` to inspect generated SQL when needed.
- Avoid raw SQL as the default path.

Flow:

```text
NexORM model
  -> repository
  -> feature service
  -> route/page handler
  -> app-router template
```

## Database Direction

Use MySQL as the primary database target through NexORM.

Recommended approach:

- Local testing should run MySQL in Docker.
- Use the latest MySQL Docker image for local testing unless a compatibility issue is found.
- Production should use MySQL or a MySQL-compatible managed database.
- NexORM models and migrations should target MySQL from the start.
- SQLite should not be used for this project, except possibly isolated NexORM package tests.
- Do not design core models around PostgreSQL-specific extensions.
- Add MySQL-specific indexes and tuning only after NexORM models and migrations are stable.

Use Redis as required runtime infrastructure. Redis should also run in Docker for local testing.

Use the latest Redis Docker image for local testing unless a compatibility issue is found.

Redis responsibilities:

- realtime pub/sub fanout
- live sandbox event streams
- live telemetry feed coordination
- task queue coordination if the first worker queue uses Redis
- short-lived RBAC/session permission cache
- login/rate-limit counters
- websocket connection coordination
- background job progress updates

MySQL remains the source of truth. Redis is cache, coordination, pub/sub, and realtime transport support.

Use an S3-compatible object store in Docker for local testing.

Recommended local S3 service:

- MinIO

Use the latest MinIO Docker image for local testing unless a compatibility issue is found.

S3-compatible storage responsibilities:

- malware samples
- PCAP files
- memory dumps
- disk images
- generated reports
- exported timelines
- lab assets
- forensic artifacts

The database stores metadata and object keys. The S3-compatible bucket stores large binary objects.

Local project run flow:

```bash
docker compose up -d mysql redis minio
uv run main.py
```

Docker is for infrastructure services only:

- MySQL
- Redis
- MinIO/S3

The Flask application runs on the host and should be started manually with `uv run main.py`.

## MVP Feature Map

The MVP table groups from `docs/mvp` map directly to feature packages.

### identity

Owns:

- users
- sessions
- api_keys
- login_attempts

Responsibilities:

- signup
- login
- logout
- current session
- API key lifecycle
- login attempt tracking

### platform_admin

Owns:

- platform_roles
- platform_permissions
- platform_role_permissions
- platform_user_roles

Responsibilities:

- platform owner/admin access
- global user management
- platform RBAC
- cross-organization visibility

### organizations

Owns:

- organizations
- organization_members
- organization_invitations
- organization_roles
- organization_permissions
- organization_role_permissions
- organization_member_roles

Responsibilities:

- organizations as Linux-group-like tenants
- Discord-like member roles
- invitations
- organization permission checks

### billing_limits

Owns:

- plans
- plan_limits
- subscriptions
- usage_counters

Responsibilities:

- plan definition
- runtime/lab usage limits
- quota checks before expensive operations

### labs

Owns:

- labs
- lab_versions
- lab_machines
- lab_networks
- lab_machine_networks
- lab_steps
- lab_assets
- lab_attempts
- lab_step_submissions

Responsibilities:

- lab templates
- lab versioning
- lab machines/networks
- learner/analyst attempts
- step validation

### runtime

Owns:

- sandbox_instances
- sandbox_machines
- sandbox_networks
- sandbox_ports
- sandbox_credentials
- sandbox_events
- sandbox_leases
- sandbox_resource_usage
- runtime_workers
- runtime_tasks
- runtime_task_logs

Responsibilities:

- sandbox lifecycle
- task queue
- workers
- runtime events
- resource usage
- virtualization provider orchestration

### storage_artifacts

Owns:

- storage_objects
- artifacts
- artifact_files
- artifact_tags
- artifact_relationships

Responsibilities:

- uploaded evidence
- malware samples
- PCAPs
- disk/memory dumps
- artifact relationships

### telemetry

Owns:

- telemetry_batches
- telemetry_events
- process_events
- network_events
- dns_events
- file_events
- registry_events
- powershell_events

Responsibilities:

- ingest telemetry
- normalize events
- store process/network/DNS/file/registry/PowerShell activity
- feed timeline and detections

### analysis

Owns:

- analysis_jobs
- analysis_job_steps
- forensic_findings
- iocs

Responsibilities:

- Volatility jobs
- disk analysis jobs
- automatic findings
- IOC extraction
- AI-assisted analysis later

### detections

Owns:

- detection_rules
- detection_rule_versions
- detection_runs
- detection_matches

Responsibilities:

- YARA/Sigma rule storage
- detection execution
- versioned rule runs
- matches against telemetry/artifacts

### timeline

Owns:

- timeline_events
- timeline_edges

Responsibilities:

- attack replay
- event graph
- process/network relationship graph
- timeline reconstruction

### cases

Owns:

- cases
- case_members
- case_artifacts
- case_findings
- case_notes
- case_reports

Responsibilities:

- investigation workspace
- evidence linking
- case notes
- report generation

### system

Owns:

- audit_logs
- idempotency_keys
- outbox_events
- notifications

Responsibilities:

- audit trail
- reliable background events
- idempotent APIs
- user notifications

## RBAC Plan

Use Discord-like RBAC:

- users can have platform roles
- users join organizations through memberships
- organization members can have multiple organization roles
- roles contain permissions
- permissions use stable string keys

Example permission keys:

```text
platform.users.read
platform.users.manage
organizations.read
organizations.manage
org.members.read
org.members.manage
labs.read
labs.write
runtime.start
runtime.stop
telemetry.ingest
analysis.run
detections.run
timeline.read
cases.read
cases.write
cases.report
```

Permission check flow:

1. Resolve current session.
2. Resolve platform roles if platform action.
3. Resolve organization membership if organization action.
4. Resolve role permissions.
5. Cache resolved permissions for the request.
6. Enforce checks in services and page guards.

UI hiding is not security. Every privileged route and service operation must check RBAC.

## Auth Plan

Initial auth:

- email/password signup
- email/password login
- server-side sessions
- logout
- login attempt tracking

Later auth:

- email verification
- password reset
- two-factor auth
- social login
- API keys
- platform-admin impersonation if needed

Sessions should store only an opaque token in the cookie. Store token hashes in the database.

## Runtime Plan

Runtime should be implemented in slices:

1. Database state model only.
2. Redis-backed worker/task coordination.
3. Browser-visible runtime event stream using Redis fanout.
4. Resource accounting.
5. Network isolation design.
6. Sandbox provider integration after the core app is stable.

Do not containerize the Flask application for local development. Run the app on the host with `uv run main.py`.

Docker is used for MySQL, Redis, and MinIO/S3 test services. The sandbox execution provider should be decided later when labs/runtime workflows are ready.

## Telemetry Plan

Start with normalized event ingestion:

- process events
- network events
- DNS events
- file events
- registry events
- PowerShell events

Later add parsers/connectors:

- Sysmon
- EVTX
- PCAP
- Zeek
- Suricata
- Velociraptor

Telemetry writes should be append-oriented. Large event views must be paginated.

## Timeline And Replay Plan

Timeline is built from normalized telemetry and sandbox events.

Initial replay:

- ordered event list
- process tree view
- network connection list
- artifact references

Later replay:

- frame-by-frame attack replay
- graph traversal
- timeline edges
- memory/network reconstruction views

## Forensics And Detection Plan

Initial integrations:

- YARA runner
- Sigma rule runner
- basic artifact hash extraction
- basic IOC extraction

Later integrations:

- Volatility 3
- disk image analysis
- Suricata
- AI-assisted malware behavior summaries
- AI-generated YARA/Sigma suggestions

Tool integrations should run as background jobs, not inside request handlers.

## Realtime And ASGI Plan

ASGI support is required for realtime growth.

Initial realtime:

- MySQL-backed runtime events as source of truth
- Redis pub/sub or streams for live fanout
- outbox events for reliable background processing
- ASGI websocket endpoint for browser realtime

Later realtime:

- live sandbox state
- live telemetry feed
- live task logs
- Redis-backed connection/channel coordination

Realtime subscriptions must reuse the same session and RBAC checks as HTTP routes.

## Implementation Phases

### Phase 0: Manual Review

- Review this plan.
- Remove previous generated implementation files before coding continues.
- Confirm internal Python package name.
- Confirm latest MySQL Docker image for NexORM testing.
- Confirm latest Redis Docker image for realtime and worker coordination.
- Confirm latest MinIO/S3 Docker image for artifact storage.
- Confirm Flask app runs on host with `uv run main.py`.
- Confirm websocket-first realtime.

Internal Python package name means the import package used by code, for example:

```python
from codesandbox.app import create_app
```

If the package stays `codesandbox`, imports remain `codesandbox.*`.

If it changes to something like `cyberrange`, imports become:

```python
from cyberrange.app import create_app
```

This package name affects:

- the folder under `src/`
- imports throughout the app
- ASGI/WSGI import paths
- test imports
- CLI entrypoints
- generated documentation examples

It does not have to match the product display name shown in the UI. For example, the product can be called CodeSandbox while the internal package remains `codesandbox`.

Recommendation: keep `codesandbox` for now unless you want a more specific internal name before implementation begins. Renaming later is possible, but it creates noisy import and file-path changes.

### Phase 1: App Foundation

- Keep app-router as the page foundation.
- Remove previous generated implementation files before coding continues.
- Configure Flask application factory.
- Configure NexORM for MySQL.
- Configure Redis client infrastructure.
- Configure S3-compatible storage client infrastructure.
- Run `nexorm init`.
- Ensure `manage.py` exists and supports migrations.
- Add app settings.
- Add ASGI entrypoint for websocket support.

### Phase 2: Identity

- Add NexORM models for users, sessions, API keys, and login attempts.
- Generate identity migration.
- Implement signup, login, logout.
- Add session helper.
- Add login page with app-router templates and UI macros.

### Phase 3: Platform RBAC

- Add platform role/permission models.
- Generate RBAC migration.
- Seed platform owner/admin roles.
- Add permission resolver.
- Add platform admin route guard.

### Phase 4: Organizations

- Add organization and membership models.
- Add organization roles and permissions.
- Add invitations.
- Add organization guard.
- Add organization admin pages.

### Phase 5: Admin Dashboard

- Build dashboard shell using the existing Jinja UI components.
- Add users page.
- Add organizations page.
- Add roles/permissions page.
- Add audit view.

### Phase 6: Labs

- Add lab templates.
- Add lab versions.
- Add machines/networks/steps/assets.
- Add lab listing and detail pages.
- Add lab attempt model.

### Phase 7: Runtime

- Add sandbox instance/task models.
- Add runtime worker.
- Add event logging.
- Add runtime dashboard page.
- Decide sandbox execution provider after the core runtime workflow is proven.

### Phase 8: Storage And Telemetry

- Add storage object/artifact models.
- Add S3-compatible object storage integration.
- Add telemetry batch/event models.
- Add ingestion endpoint.
- Add artifact explorer page.

### Phase 9: Analysis, Detections, Timeline, Cases

- Add analysis jobs and findings.
- Add IOC extraction.
- Add detection rules/runs/matches.
- Add timeline events/edges.
- Add cases, notes, reports, and evidence linking.

### Phase 10: Production Readiness

- Add tests for NexORM migrations.
- Add tests for auth and RBAC.
- Add route guard tests.
- Add worker tests.
- Add deployment docs.
- Add sandbox security docs.
- Add backup/restore docs.

## Optimization Plan

Start simple and measure.

Initial rules:

- index common foreign keys and filters through NexORM migrations
- paginate large lists
- batch telemetry writes if NexORM supports it
- keep long tasks in workers
- use transactions for multi-step writes
- avoid N+1 queries in repositories

Bloom filters:

- not required in the first pass
- useful later for IOC matching or permission membership checks
- should be introduced only after there is a measured bottleneck

## Review Questions

- Internal Python import package: keep `codesandbox` unless you choose a new name before coding starts.
- Docker test infrastructure should use latest images for MySQL, Redis, and MinIO/S3.
- Flask app runs on the host with `uv run main.py`.
- Realtime is websocket-first.
- Previous generated implementation code should be removed before implementation continues.
