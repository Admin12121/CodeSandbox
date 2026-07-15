from codesandbox.shared.permissions import (
    register_org_permission,
    register_platform_permission,
)

# Organization sandbox permissions. Owners bypass these checks, while custom
# roles can receive only the capabilities they need.
register_org_permission("sandbox.allocations.prepare", "Prepare org sandbox allocations", "Sandbox")
register_org_permission("sandbox.allocations.manage", "Manage org sandbox allocations", "Sandbox")
register_org_permission("sandbox.allocations.view_all", "View all org sandbox allocations", "Sandbox")
register_org_permission("sandbox.instances.use_pool", "Start shared org sandbox allocations", "Sandbox")
register_org_permission("sandbox.instances.use_assigned", "Start assigned private allocations", "Sandbox")
register_org_permission("sandbox.instances.stop_own", "Stop own active org sandbox sessions", "Sandbox")
register_org_permission("sandbox.requests.submit", "Request a private sandbox allocation", "Sandbox")
register_org_permission("sandbox.requests.review", "Review member sandbox requests", "Sandbox")
register_org_permission("sandbox.billing.view", "View organization sandbox billing", "Sandbox billing")
register_org_permission("sandbox.billing.topup", "Top up organization sandbox balance", "Sandbox billing")

# Compatibility permissions retained for existing custom roles. New runtime
# authorization does not use them as a source of truth.
register_org_permission("sandbox.instances.create", "Legacy: create org instances", "Sandbox (legacy)")
register_org_permission("sandbox.instances.view_all", "Legacy: view all org instances", "Sandbox (legacy)")

# Platform staff permissions are separate from organization permissions.
register_platform_permission("platform.sandboxes.test", "Run sandbox template tests", "Sandboxes")
register_platform_permission("platform.sandboxes.publish", "Publish sandbox templates", "Sandboxes")
