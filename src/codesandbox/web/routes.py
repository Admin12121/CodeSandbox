from codesandbox.web.blueprint import router


@router.page("/")
def home():
    return {
        "_meta": {
            "title": "Flask App Router UI",
            "description": "Jinja macro components cloned from the demo UI set.",
        },
        "stats": [
            {"label": "Components", "value": "54"},
            {"label": "Mode", "value": "Jinja"},
            {"label": "Assets", "value": "CDN + local CSS"},
        ],
    }

@router.page("/login")
def login():
    return {
        "_meta": {
            "title": "Room24 Login",
            "description": "Sign in to Room24.",
        },
        "mode": "signin",
        "error": None,
    }


@router.page("/dashboard")
def dashboard():
    return {
        "_meta": {
            "title": "Room24 Dashboard",
            "description": "Admin overview for labs, runtime, telemetry, and cases.",
        },
        "user": {
            "name": "Platform Admin",
            "email": "admin@example.com",
            "role": "system_admin",
        },
        "metrics": [
            {"label": "Organizations", "value": "0", "change": "Ready for seed data"},
            {"label": "Running sandboxes", "value": "0", "change": "Runtime worker pending"},
            {"label": "Open cases", "value": "0", "change": "Case workflow pending"},
            {"label": "Telemetry events", "value": "0", "change": "Ingestion pending"},
        ],
        "nav": [
            {"label": "Dashboard", "href": "/dashboard", "active": True},
            {"label": "Organizations", "href": "#", "active": False},
            {"label": "Labs", "href": "#", "active": False},
            {"label": "Runtime", "href": "#", "active": False},
            {"label": "Cases", "href": "#", "active": False},
            {"label": "RBAC", "href": "#", "active": False},
        ],
    }
