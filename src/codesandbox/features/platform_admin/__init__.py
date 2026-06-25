from codesandbox.shared.permissions import register_platform_permission

register_platform_permission("platform.users.read",           "View Users",                 "Users")
register_platform_permission("platform.users.edit",           "Edit User Profile",          "Users")
register_platform_permission("platform.users.status",         "Change User Status",         "Users")
register_platform_permission("platform.users.roles",          "Change User Platform Role",  "Users")
register_platform_permission("platform.staff.read",           "View Staff",                 "Staff")
register_platform_permission("platform.staff.manage",         "Manage Staff",               "Staff")
register_platform_permission("platform.roles.read",           "View Roles",                 "Roles")
register_platform_permission("platform.roles.manage",         "Manage Roles",               "Roles")
register_platform_permission("platform.organizations.read",   "View Organizations",         "Organizations")
register_platform_permission("platform.organizations.edit",   "Edit Organizations",         "Organizations")
register_platform_permission("platform.organizations.status", "Change Organization Status", "Organizations")
