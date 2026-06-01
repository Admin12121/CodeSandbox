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
