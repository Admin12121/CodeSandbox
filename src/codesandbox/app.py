from __future__ import annotations

import os

from flask import Flask

from codesandbox.web.blueprint import web_bp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    from codesandbox.web import routes as _routes  # noqa: F401

    app.register_blueprint(web_bp)
    return app


app = create_app()
