from codesandbox.shared.permissions import register_org_permission

register_org_permission("org.members.invite", "Invite Members", "Members")
register_org_permission("org.members.remove", "Remove Members", "Members")
register_org_permission("org.roles.assign",   "Assign Roles to Members",        "Roles")
register_org_permission("org.roles.manage",   "Create and Delete Custom Roles", "Roles")
register_org_permission("org.settings.edit",  "Edit Organization Settings",     "Settings")
