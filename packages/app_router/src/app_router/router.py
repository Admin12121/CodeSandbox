"""Core app-router implementation."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import wraps
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    g,
    has_app_context,
    has_request_context,
    jsonify,
    make_response,
    redirect,
    request,
    send_file,
)
from flask.typing import ResponseReturnValue
from jinja2 import ChoiceLoader, PackageLoader, TemplateNotFound, nodes, pass_context
from jinja2.ext import Extension
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException
from werkzeug.routing import BuildError, RequestRedirect

from .assets import AssetManager, AssetRoute, combine_asset_lists
from .csrf import csrf_input, csrf_token, validate_csrf
from .exceptions import AppRouterError, CSRFError
from .helpers import (
    cn,
    html_attrs,
    is_safe_redirect_url,
    is_unsafe_method,
    parse_tree_header,
    quote_attr,
)
from .responses import RedirectResult

DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)
DEFAULT_ASSET_MAX_AGE = 31_536_000
PARTIAL_HEADER = "X-Flask-Router"
CURRENT_PATH_HEADER = "X-Flask-Current-Path"
CURRENT_TREE_HEADER = "X-Flask-Current-Tree"
CLIENT_STATE_PATH_META = "app-router-path"
CLIENT_STATE_TREE_META = "app-router-tree"
CLIENT_SCRIPT_NONCE_META = "app-router-script-nonce"
APP_SCRIPT_G_KEY = "_app_router_inline_scripts"
APP_SCRIPT_SEEN_G_KEY = "_app_router_inline_script_keys"
APP_SCRIPT_NONCE_G_KEY = "_app_router_script_nonce"

PageLoader = Callable[..., object]
ApiLoader = Callable[..., object]
Target = Flask | Blueprint


@dataclass(frozen=True)
class LayoutSpec:
    boundary: str
    template: str | None
    root: bool = False


@dataclass
class RenderBundle:
    html: str
    scripts: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)


@dataclass
class PageRoute:
    rule: str
    endpoint: str
    loader: PageLoader
    methods: tuple[str, ...]
    template: str
    template_explicit: bool
    options: dict[str, Any]
    csrf: bool
    asset_private: bool
    asset_max_age: int

    def asset_route(self) -> AssetRoute:
        return AssetRoute(
            endpoint=self.endpoint,
            asset_private=self.asset_private,
            asset_max_age=self.asset_max_age,
        )


@dataclass
class ApiRoute:
    rule: str
    endpoint: str
    loader: ApiLoader
    methods: tuple[str, ...]
    options: dict[str, Any]
    csrf: bool


@dataclass(frozen=True)
class InlineScript:
    key: str
    code: str


class AppScriptExtension(Extension):
    """Collect colocated component scripts from Jinja templates."""

    tags = {"app_script"}

    def parse(self, parser: Any) -> nodes.CallBlock:
        lineno = next(parser.stream).lineno
        if parser.stream.current.test("block_end"):
            parser.fail("app_script requires a unique script key.", lineno)
        key = parser.parse_expression()
        body = parser.parse_statements(("name:end_app_script",), drop_needle=True)
        return nodes.CallBlock(
            self.call_method("_capture", [key]),
            [],
            [],
            body,
        ).set_lineno(lineno)

    @pass_context
    def _capture(self, context: Any, key: object, caller: Callable[[], str]) -> str:
        if not isinstance(key, str) or not key:
            raise AppRouterError("app_script key must be a non-empty string.")
        _capture_app_script(key, caller())
        return ""


class AppRouter:
    """A server-driven, Flask-native app router.

    The same class can register directly on a Flask app or on a Blueprint. Page
    decorators are the source of truth; template folders only resolve matching
    ``page.html`` and ``layout.html`` files.
    """

    def __init__(
        self,
        target: Target | None = None,
        *,
        asset_url_path: str = "/_app/assets",
        client_url_path: str = "/_app/router.js",
        partial_header: str = PARTIAL_HEADER,
        security_headers: bool = True,
        csp: str | None = DEFAULT_CSP,
        csrf: bool = True,
        build_dir: str | Path = ".app-router",
    ) -> None:
        self.asset_url_path = asset_url_path.rstrip("/")
        self.client_url_path = client_url_path
        self.partial_header = partial_header
        self.security_headers = security_headers
        self.csp = csp
        self.csrf = csrf
        self.build_dir = Path(build_dir)
        self.assets = AssetManager(url_prefix=self.asset_url_path)

        self._target: Target | None = None
        self._page_routes: dict[str, PageRoute] = {}
        self._api_routes: dict[str, ApiRoute] = {}
        self._registered: set[tuple[int, str, str]] = set()

        if target is not None:
            self.bind(target)

    def bind(self, target: Target) -> None:
        """Bind this router to a Flask app or Blueprint."""

        self._target = target
        if isinstance(target, Flask):
            self.init_app(target)
        else:
            target.record_once(lambda state: self.init_app(cast(Flask, state.app)))

        for page_route in self._page_routes.values():
            self._register_page(target, page_route)
        for api_route in self._api_routes.values():
            self._register_api(target, api_route)

    def init_app(self, app: Flask) -> None:
        """Install package templates, globals, assets, client JS, and headers."""

        state = app.extensions.setdefault(
            "app_router",
            {
                "routers": [],
                "routes_installed": False,
                "loader_installed": False,
                "headers_installed": False,
                "errors_installed": False,
            },
        )
        routers = state["routers"]
        if self not in routers:
            routers.append(self)

        self._install_template_loader(app, state)
        self._install_template_globals(app)
        self._load_build_manifest(app)
        self._install_internal_routes(app, state)
        self._install_security_headers(app, state)
        self._install_error_handlers(app, state)

        if self._target is None:
            self._target = app
            for page_route in self._page_routes.values():
                self._register_page(app, page_route)
            for api_route in self._api_routes.values():
                self._register_api(app, api_route)

    def page(
        self,
        rule: str,
        *,
        methods: Sequence[str] | None = None,
        endpoint: str | None = None,
        template: str | None = None,
        csrf: bool | None = None,
        private_assets: bool = False,
        asset_max_age: int = DEFAULT_ASSET_MAX_AGE,
        **options: Any,
    ) -> Callable[[PageLoader], PageLoader]:
        """Register a page route whose loader returns template data."""

        def decorator(func: PageLoader) -> PageLoader:
            route_methods = _normalize_methods(methods)
            route_endpoint = endpoint or func.__name__
            route = PageRoute(
                rule=rule,
                endpoint=route_endpoint,
                loader=func,
                methods=route_methods,
                template=template or route_to_template(rule),
                template_explicit=template is not None,
                options=dict(options),
                csrf=self.csrf
                and (csrf if csrf is not None else _methods_need_csrf(route_methods)),
                asset_private=private_assets,
                asset_max_age=asset_max_age,
            )
            self._page_routes[route_endpoint] = route
            if self._target is not None:
                self._register_page(self._target, route)
            return func

        return decorator

    def api(
        self,
        rule: str,
        *,
        methods: Sequence[str] | None = None,
        endpoint: str | None = None,
        csrf: bool | None = None,
        **options: Any,
    ) -> Callable[[ApiLoader], ApiLoader]:
        """Register a JSON/API route. Partial navigation headers are ignored."""

        def decorator(func: ApiLoader) -> ApiLoader:
            route_methods = _normalize_methods(methods)
            route_endpoint = endpoint or func.__name__
            route = ApiRoute(
                rule=rule,
                endpoint=route_endpoint,
                loader=func,
                methods=route_methods,
                options=dict(options),
                csrf=self.csrf
                and (csrf if csrf is not None else _methods_need_csrf(route_methods)),
            )
            self._api_routes[route_endpoint] = route
            if self._target is not None:
                self._register_api(self._target, route)
            return func

        return decorator

    def url_for_asset(self, template_name: str, local_ref: str, route_endpoint: str) -> str:
        """Resolve a simple ``./asset`` reference to an opaque manifest URL."""

        route = self._page_routes[route_endpoint]
        return self.assets.asset_url(
            current_app.jinja_env,
            template_name,
            local_ref,
            route.asset_route(),
        )

    def build(
        self,
        app: Flask | None = None,
        *,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Build route and asset metadata without pre-rendering pages."""

        target_app = self._resolve_build_app(app)
        output_path = Path(output_dir) if output_dir is not None else self.build_dir
        output_path.mkdir(parents=True, exist_ok=True)

        with target_app.app_context():
            page_routes: list[dict[str, Any]] = []
            asset_routes: list[tuple[AssetRoute, list[str]]] = []

            for endpoint, route in sorted(self._page_routes.items()):
                resolved_template = self._resolve_page_template(route)
                template_exists = resolved_template is not None
                layouts = self._layout_chain(resolved_template) if resolved_template else []
                template_names = [
                    resolved_template or route.template,
                    *(layout.template for layout in layouts if layout.template is not None),
                ]
                if template_exists:
                    asset_routes.append((route.asset_route(), template_names))

                page_routes.append(
                    {
                        "endpoint": endpoint,
                        "rule": route.rule,
                        "methods": list(route.methods),
                        "template": resolved_template or route.template,
                        "template_exists": template_exists,
                        "layouts": [
                            {
                                "boundary": layout.boundary,
                                "template": layout.template,
                                "root": layout.root,
                            }
                            for layout in layouts
                        ],
                        "tree": self._layout_tree(layouts) if layouts else [],
                        "private_assets": route.asset_private,
                        "asset_max_age": route.asset_max_age,
                        "render_mode": "dynamic",
                        "prerender": False,
                    }
                )

            routes_manifest = {
                "version": 1,
                "rendering": "dynamic",
                "pages": page_routes,
                "apis": [
                    {
                        "endpoint": endpoint,
                        "rule": route.rule,
                        "methods": list(route.methods),
                        "csrf": route.csrf,
                    }
                    for endpoint, route in sorted(self._api_routes.items())
                ],
            }
            asset_manifest = self.assets.build_manifest(
                target_app.jinja_env,
                routes=asset_routes,
                output_dir=output_path,
            )
            (output_path / "routes.json").write_text(
                json.dumps(routes_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        return {
            "manifest": asset_manifest,
            "routes": routes_manifest,
            "output_dir": str(output_path),
        }

    def _register_page(self, target: Target, route: PageRoute) -> None:
        key = (id(target), route.endpoint, "page")
        if key in self._registered:
            return

        @wraps(route.loader)
        def view(**kwargs: Any) -> ResponseReturnValue:
            return self._handle_page(route, **kwargs)

        target.add_url_rule(
            route.rule,
            endpoint=route.endpoint,
            view_func=view,
            methods=list(route.methods),
            **route.options,
        )
        self._registered.add(key)

    def _resolve_build_app(self, app: Flask | None) -> Flask:
        if app is not None:
            return app
        if isinstance(self._target, Flask):
            return self._target
        if has_app_context():
            return current_app
        raise AppRouterError("AppRouter.build() requires a Flask app or app context.")

    def _register_api(self, target: Target, route: ApiRoute) -> None:
        key = (id(target), route.endpoint, "api")
        if key in self._registered:
            return

        @wraps(route.loader)
        def view(**kwargs: Any) -> ResponseReturnValue:
            return self._handle_api(route, **kwargs)

        target.add_url_rule(
            route.rule,
            endpoint=route.endpoint,
            view_func=view,
            methods=list(route.methods),
            **route.options,
        )
        self._registered.add(key)

    def _handle_page(self, route: PageRoute, **kwargs: Any) -> ResponseReturnValue:
        template_name = self._resolve_page_template(route)
        if template_name is None:
            current_app.logger.info(
                "app-router page template not found for route %s: %s",
                route.rule,
                route.template,
            )
            return self._not_found("Not found.")

        if route.csrf and is_unsafe_method(request.method):
            self._validate_csrf()

        result = route.loader(**kwargs)
        response = self._coerce_direct_response(result)
        if response is not None:
            return response

        data = self._coerce_page_data(result)
        redirect_result = self._extract_redirect(data)
        if redirect_result is not None:
            return self._redirect_response(redirect_result)

        meta = _metadata_from_data(data)
        cache_enabled = bool(data.get("_cache", False))
        ttl = int(data.get("_ttl", 0) or 0)
        context = self._template_context(data)
        layouts = self._layout_chain(template_name)
        _reset_app_scripts()

        if self._is_partial_request():
            return self._partial_response(route, template_name, layouts, context, meta, cache_enabled)

        bundle = self._render_full(route, template_name, layouts, context)
        html = self._apply_document_features(
            bundle.html,
            meta,
            layouts,
            request.full_path.rstrip("?"),
            _collected_app_scripts(),
        )
        response = make_response(html)
        self._apply_cache_headers(response, cache_enabled, ttl)
        return response

    def _handle_api(self, route: ApiRoute, **kwargs: Any) -> ResponseReturnValue:
        if route.csrf and is_unsafe_method(request.method):
            self._validate_csrf()

        result = route.loader(**kwargs)
        response = self._coerce_direct_response(result)
        if response is not None:
            return response

        response = _jsonify_result(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _partial_response(
        self,
        route: PageRoute,
        template_name: str,
        layouts: list[LayoutSpec],
        context: dict[str, Any],
        meta: dict[str, str],
        cache_enabled: bool,
    ) -> Response:
        current_route = self._current_route_from_headers()
        if current_route is None:
            return self._reload_json()

        client_tree = parse_tree_header(request.headers.get(CURRENT_TREE_HEADER))
        current_template = self._resolve_page_template(current_route)
        if current_template is None:
            return self._reload_json()
        current_tree = self._layout_tree(self._layout_chain(current_template))
        next_tree = self._layout_tree(layouts)
        if client_tree != current_tree:
            return self._reload_json()

        boundary = _patch_boundary(current_tree, next_tree)
        if boundary not in next_tree:
            return self._reload_json()

        bundle = self._render_outlet(route, template_name, layouts, context, boundary)
        body = {
            "mode": "patch",
            "url": request.full_path.rstrip("?"),
            "boundary": boundary,
            "html": bundle.html,
            "tree": next_tree,
            "meta": meta,
            "cache": cache_enabled,
            "scripts": bundle.scripts,
            "styles": bundle.styles,
            "inlineScripts": [script.__dict__ for script in _collected_app_scripts()],
        }
        response = jsonify(body)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = (
            f"{self.partial_header}, {CURRENT_PATH_HEADER}, {CURRENT_TREE_HEADER}"
        )
        return response

    def _render_full(
        self,
        route: PageRoute,
        template_name: str,
        layouts: list[LayoutSpec],
        context: dict[str, Any],
    ) -> RenderBundle:
        root = layouts[0]
        outlet = self._render_outlet(route, template_name, layouts, context, "root")
        root_context = {**context, "children": Markup(self._boundary("root", outlet.html))}
        if root.template is None:
            return RenderBundle(
                html=self._render_document_shell(root_context["children"]),
                scripts=outlet.scripts,
                styles=outlet.styles,
            )
        root_bundle = self._render_template(root.template, root_context, route)
        return RenderBundle(
            html=root_bundle.html,
            scripts=combine_asset_lists(root_bundle.scripts, outlet.scripts),
            styles=combine_asset_lists(root_bundle.styles, outlet.styles),
        )

    def _render_outlet(
        self,
        route: PageRoute,
        template_name: str,
        layouts: list[LayoutSpec],
        context: dict[str, Any],
        boundary: str,
    ) -> RenderBundle:
        start_index = next(
            index for index, layout in enumerate(layouts) if layout.boundary == boundary
        )
        page = self._render_template(template_name, context, route)
        html = page.html
        scripts = list(page.scripts)
        styles = list(page.styles)

        for layout in reversed(layouts[start_index + 1 :]):
            if layout.template is None:
                continue
            layout_context = {**context, "children": Markup(self._boundary(layout.boundary, html))}
            rendered = self._render_template(layout.template, layout_context, route)
            html = rendered.html
            scripts = combine_asset_lists(rendered.scripts, scripts)
            styles = combine_asset_lists(rendered.styles, styles)

        return RenderBundle(html=html, scripts=scripts, styles=styles)

    def _render_template(
        self,
        template_name: str,
        context: Mapping[str, Any],
        route: PageRoute,
    ) -> RenderBundle:
        rendered = current_app.jinja_env.get_template(template_name).render(dict(context))
        rewritten = self.assets.rewrite_html(
            rendered,
            env=current_app.jinja_env,
            template_name=template_name,
            route=route.asset_route(),
        )
        return RenderBundle(
            html=rewritten.html,
            scripts=rewritten.scripts,
            styles=rewritten.styles,
        )

    def _layout_chain(self, template_name: str) -> list[LayoutSpec]:
        root_template = "layout.html" if self._template_exists("layout.html") else None
        layouts = [LayoutSpec(boundary="root", template=root_template, root=True)]
        parts = template_name.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            directory = "/".join(parts[:index])
            template = f"{directory}/layout.html"
            if self._template_exists(template):
                layouts.append(LayoutSpec(boundary=directory, template=template))
        return layouts

    def _layout_tree(self, layouts: Sequence[LayoutSpec]) -> list[str]:
        return [layout.boundary for layout in layouts]

    def _current_route_from_headers(self) -> PageRoute | None:
        current_path = request.headers.get(CURRENT_PATH_HEADER)
        if not current_path:
            return None
        path = urlsplit(current_path).path
        adapter = current_app.url_map.bind_to_environ(request.environ)
        try:
            endpoint, _ = adapter.match(path_info=path, method="GET")
        except (HTTPException, RequestRedirect, BuildError, Exception):
            return None
        return self._route_by_endpoint(str(endpoint))

    def _route_by_endpoint(self, endpoint: str) -> PageRoute | None:
        if endpoint in self._page_routes:
            return self._page_routes[endpoint]
        suffix = f".{endpoint.split('.')[-1]}"
        matches = [
            route for name, route in self._page_routes.items() if endpoint.endswith(f".{name}")
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
        for name, route in self._page_routes.items():
            if suffix == f".{name}":
                return route
        return None

    def _template_context(self, data: Mapping[str, Any]) -> dict[str, Any]:
        context = dict(data)
        context.setdefault("router", self)
        context.setdefault("app_router", self)
        return context

    def _template_exists(self, template_name: str) -> bool:
        try:
            current_app.jinja_env.get_template(template_name)
        except TemplateNotFound:
            return False
        return True

    def _resolve_page_template(self, route: PageRoute) -> str | None:
        if self._template_exists(route.template):
            return route.template
        if route.template_explicit:
            return None

        matches = [
            template
            for template in self._page_template_names()
            if _strip_route_groups(template) == route.template
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise AppRouterError(
                "Route template is ambiguous after ignoring route groups for "
                f"{route.rule}: {', '.join(sorted(matches))}. Pass template= explicitly."
            )
        return matches[0]

    def _page_template_names(self) -> list[str]:
        try:
            names = current_app.jinja_env.list_templates()
        except TypeError:
            return []
        return sorted(name for name in names if name.endswith("/page.html") or name == "page.html")

    def _is_partial_request(self) -> bool:
        return request.headers.get(self.partial_header) == "partial"

    def _reload_json(self) -> Response:
        response = jsonify({"mode": "reload", "url": request.full_path.rstrip("?")})
        response.headers["Cache-Control"] = "no-store"
        return response

    def _redirect_response(self, result: RedirectResult) -> ResponseReturnValue:
        if not is_safe_redirect_url(result.url, request.host_url):
            abort(400, description="Unsafe redirect URL.")
        if self._is_partial_request():
            response = jsonify(
                {
                    "mode": "redirect",
                    "url": result.url,
                    "message": result.message,
                }
            )
            response.status_code = 200
            response.headers["Cache-Control"] = "no-store"
            return response
        return redirect(result.url, code=result.status_code)

    def _extract_redirect(self, data: Mapping[str, Any]) -> RedirectResult | None:
        redirect_url = data.get("_redirect")
        if not isinstance(redirect_url, str):
            return None
        status_code = int(data.get("_status", 303) or 303)
        message = data.get("_message")
        return RedirectResult(
            url=redirect_url,
            status_code=status_code,
            message=message if isinstance(message, str) else None,
        )

    def _coerce_page_data(self, result: object) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, Mapping):
            return dict(result)
        if isinstance(result, RedirectResult):
            return {
                "_redirect": result.url,
                "_status": result.status_code,
                "_message": result.message,
            }
        raise AppRouterError("Page loaders must return a dict, RedirectResult, Response, or None.")

    def _coerce_direct_response(self, result: object) -> ResponseReturnValue | None:
        if isinstance(result, RedirectResult):
            return self._redirect_response(result)
        if isinstance(result, Response):
            return result
        return None

    def _validate_csrf(self) -> None:
        try:
            validate_csrf(raise_error=True)
        except CSRFError as exc:
            abort(400, description=str(exc))

    def _boundary(self, name: str, html: str) -> str:
        return f'<div data-router-boundary="{escape(name)}">{html}</div>'

    def _not_found(self, message: str) -> Response:
        response = self._render_error_page(404, message=message)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _render_error_page(
        self,
        status_code: int,
        *,
        message: str,
        error: BaseException | None = None,
    ) -> Response:
        template = f"{status_code}.html"
        context = {
            "status_code": status_code,
            "message": message,
            "error": error,
            "router": self,
            "app_router": self,
        }
        try:
            html = current_app.jinja_env.get_template(template).render(context)
        except TemplateNotFound:
            html = self._render_document_shell(
                Markup(f"<h1>{escape(status_code)}</h1><p>{escape(message)}</p>")
            )
        response = make_response(html, status_code)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _render_document_shell(self, children: object) -> str:
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "  <head>\n"
            '    <meta charset="utf-8">\n'
            '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "  </head>\n"
            f"  <body>\n    {children}\n  </body>\n"
            "</html>"
        )

    def _apply_cache_headers(self, response: Response, cache_enabled: bool, ttl: int) -> None:
        response.headers["Vary"] = self.partial_header
        if cache_enabled:
            response.headers["Cache-Control"] = f"public, max-age={max(ttl, 0)}"
        else:
            response.headers["Cache-Control"] = "no-store"

    def _apply_document_features(
        self,
        html: str,
        meta: Mapping[str, str],
        layouts: Sequence[LayoutSpec],
        path: str,
        inline_scripts: Sequence[InlineScript],
    ) -> str:
        html = _inject_or_replace_metadata(html, meta)
        html = _inject_or_replace_router_state(html, path, self._layout_tree(layouts))
        nonce = _ensure_app_script_nonce() if self.csp else None
        if nonce:
            html = _replace_or_inject_meta(html, CLIENT_SCRIPT_NONCE_META, nonce)
        html = _inject_inline_app_scripts(html, inline_scripts, nonce)
        html = _inject_client_script(html, self.client_url_path)
        return html

    def _install_template_loader(self, app: Flask, state: dict[str, Any]) -> None:
        if state["loader_installed"]:
            return
        app.jinja_env.add_extension(AppScriptExtension)
        package_loader = PackageLoader(_package_name(), "templates")
        app.jinja_env.loader = ChoiceLoader([app.create_global_jinja_loader(), package_loader])
        state["loader_installed"] = True

    def _install_template_globals(self, app: Flask) -> None:
        app.jinja_env.globals.update(
            {
                "cn": cn,
                "html_attrs": html_attrs,
                "csrf_token": csrf_token,
                "csrf_input": csrf_input,
                "app_router": self,
            }
        )

    def _load_build_manifest(self, app: Flask) -> None:
        manifest_path = self._build_manifest_path(app)
        self.assets.load_manifest(manifest_path)

    def _build_manifest_path(self, app: Flask) -> Path:
        candidates: list[Path] = []
        configured = app.config.get("APP_ROUTER_BUILD_DIR")
        if configured:
            candidates.append(Path(str(configured)) / "manifest.json")
        candidates.append(self.build_dir / "manifest.json")
        if not self.build_dir.is_absolute():
            candidates.append(Path(app.root_path) / self.build_dir / "manifest.json")

        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def _install_internal_routes(self, app: Flask, state: dict[str, Any]) -> None:
        if state["routes_installed"]:
            return

        def serve_client() -> Response:
            resource = files(_package_name()).joinpath("static/router.js")
            with as_file(resource) as path:
                response = send_file(path, mimetype="text/javascript; charset=utf-8")
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        def serve_asset(asset_id: str) -> Response:
            for router in cast(list[AppRouter], state["routers"]):
                if router.assets.get(asset_id) is not None:
                    return router.assets.serve(asset_id)
            abort(404)

        app.add_url_rule(
            self.client_url_path,
            endpoint="app_router.client",
            view_func=serve_client,
        )
        app.add_url_rule(
            f"{self.asset_url_path}/<path:asset_id>",
            endpoint="app_router.asset",
            view_func=serve_asset,
        )
        state["routes_installed"] = True

    def _install_security_headers(self, app: Flask, state: dict[str, Any]) -> None:
        if not self.security_headers or state["headers_installed"]:
            return

        @app.after_request
        def add_security_headers(response: Response) -> Response:
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            if self.csp and "Content-Security-Policy" not in response.headers:
                response.headers["Content-Security-Policy"] = _csp_with_script_nonce(
                    self.csp,
                    _current_app_script_nonce(),
                )
            return response

        state["headers_installed"] = True

    def _install_error_handlers(self, app: Flask, state: dict[str, Any]) -> None:
        if state["errors_installed"]:
            return

        @app.errorhandler(404)
        def handle_not_found(error: HTTPException) -> Response:
            return self._render_error_page(404, message="Not found.", error=error)

        @app.errorhandler(500)
        def handle_server_error(error: BaseException) -> Response:
            return self._render_error_page(
                500,
                message="An internal server error occurred.",
                error=error,
            )

        state["errors_installed"] = True


def route_to_template(rule: str) -> str:
    """Map an explicit Flask route rule to a Next-style page template."""

    parts = [part for part in rule.strip("/").split("/") if part]
    if not parts:
        return "page.html"
    mapped = [_route_segment_to_template(part) for part in parts]
    return "/".join([*mapped, "page.html"])


def _route_segment_to_template(segment: str) -> str:
    if segment.startswith("<") and segment.endswith(">"):
        inner = segment[1:-1]
        if ":" in inner:
            converter, name = inner.split(":", 1)
            if converter == "path":
                raise ValueError("Catch-all path converters are not supported for page routes.")
        else:
            name = inner
        if not name or "/" in name:
            raise ValueError(f"Invalid route variable segment: {segment}")
        return f"[{name}]"
    if "<" in segment or ">" in segment:
        raise ValueError(f"Mixed static/dynamic route segments are not supported: {segment}")
    return segment


def _strip_route_groups(template_name: str) -> str:
    parts = [part for part in template_name.split("/") if not _is_route_group(part)]
    return "/".join(parts)


def _is_route_group(segment: str) -> bool:
    return segment.startswith("(") and segment.endswith(")") and len(segment) > 2


def _normalize_methods(methods: Sequence[str] | None) -> tuple[str, ...]:
    if methods is None:
        return ("GET",)
    normalized = tuple(dict.fromkeys(method.upper() for method in methods))
    return normalized or ("GET",)


def _methods_need_csrf(methods: Sequence[str]) -> bool:
    return any(is_unsafe_method(method) for method in methods)


def _metadata_from_data(data: Mapping[str, Any]) -> dict[str, str]:
    raw = data.get("_meta")
    if not isinstance(raw, Mapping):
        return {}
    meta: dict[str, str] = {}
    for key in ("title", "description", "image"):
        value = raw.get(key)
        if value is not None:
            meta[key] = str(value)
    return meta


def _patch_boundary(current_tree: Sequence[str], next_tree: Sequence[str]) -> str:
    common: list[str] = []
    for current, next_item in zip(current_tree, next_tree, strict=False):
        if current != next_item:
            break
        common.append(current)
    if not common:
        return "root"
    return common[-1]


def _jsonify_result(result: object) -> Response:
    status = 200
    headers: Mapping[str, str] | None = None
    payload = result
    if isinstance(result, tuple):
        payload = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status = result[1]
        if len(result) > 2 and isinstance(result[2], Mapping):
            headers = {str(key): str(value) for key, value in result[2].items()}
    response = make_response(jsonify(payload), status)
    if headers:
        response.headers.update(headers)
    return response


def _inject_or_replace_metadata(html: str, meta: Mapping[str, str]) -> str:
    title = meta.get("title")
    if title:
        title_tag = f"<title>{quote_attr(title)}</title>"
        if re.search(r"<title>.*?</title>", html, flags=re.IGNORECASE | re.DOTALL):
            html = re.sub(
                r"<title>.*?</title>",
                title_tag,
                html,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        else:
            html = _inject_head(html, title_tag)

    description = meta.get("description")
    if description:
        html = _replace_or_inject_meta(html, "description", description)

    image = meta.get("image")
    if image:
        html = _replace_or_inject_property(html, "og:image", image)
    return html


def _inject_or_replace_router_state(html: str, path: str, tree: Sequence[str]) -> str:
    html = _replace_or_inject_meta(html, CLIENT_STATE_PATH_META, path)
    html = _replace_or_inject_meta(html, CLIENT_STATE_TREE_META, ",".join(tree))
    return html


def _replace_or_inject_meta(html: str, name: str, content: str) -> str:
    escaped_name = re.escape(name)
    pattern = rf'<meta\s+name=["\']{escaped_name}["\'][^>]*>'
    tag = f'<meta name="{quote_attr(name)}" content="{quote_attr(content)}">'
    if re.search(pattern, html, flags=re.IGNORECASE):
        return re.sub(pattern, tag, html, count=1, flags=re.IGNORECASE)
    return _inject_head(html, tag)


def _replace_or_inject_property(html: str, name: str, content: str) -> str:
    escaped_name = re.escape(name)
    pattern = rf'<meta\s+property=["\']{escaped_name}["\'][^>]*>'
    tag = f'<meta property="{quote_attr(name)}" content="{quote_attr(content)}">'
    if re.search(pattern, html, flags=re.IGNORECASE):
        return re.sub(pattern, tag, html, count=1, flags=re.IGNORECASE)
    return _inject_head(html, tag)


def _inject_head(html: str, fragment: str) -> str:
    if re.search(r"</head>", html, flags=re.IGNORECASE):
        return re.sub(r"</head>", f"{fragment}\n</head>", html, count=1, flags=re.IGNORECASE)
    return f"{fragment}\n{html}"


def _inject_client_script(html: str, client_url_path: str) -> str:
    if "data-app-router-client" in html:
        return html
    script = (
        f'<script type="module" src="{quote_attr(client_url_path)}" '
        "data-app-router-client></script>"
    )
    if re.search(r"</body>", html, flags=re.IGNORECASE):
        return re.sub(r"</body>", f"{script}\n</body>", html, count=1, flags=re.IGNORECASE)
    return f"{html}\n{script}"


def _reset_app_scripts() -> None:
    if not has_request_context():
        return
    setattr(g, APP_SCRIPT_G_KEY, [])
    setattr(g, APP_SCRIPT_SEEN_G_KEY, set())


def _capture_app_script(key: str, code: str) -> None:
    if not has_request_context():
        return
    scripts: list[InlineScript] = getattr(g, APP_SCRIPT_G_KEY, [])
    seen: set[str] = getattr(g, APP_SCRIPT_SEEN_G_KEY, set())
    if key in seen:
        return
    seen.add(key)
    scripts.append(InlineScript(key=key, code=code.strip()))
    setattr(g, APP_SCRIPT_G_KEY, scripts)
    setattr(g, APP_SCRIPT_SEEN_G_KEY, seen)


def _collected_app_scripts() -> list[InlineScript]:
    if not has_request_context():
        return []
    return list(getattr(g, APP_SCRIPT_G_KEY, []))


def _ensure_app_script_nonce() -> str:
    nonce = _current_app_script_nonce()
    if nonce is None:
        nonce = secrets.token_urlsafe(16)
        setattr(g, APP_SCRIPT_NONCE_G_KEY, nonce)
    return nonce


def _current_app_script_nonce() -> str | None:
    if not has_request_context():
        return None
    nonce = getattr(g, APP_SCRIPT_NONCE_G_KEY, None)
    return nonce if isinstance(nonce, str) else None


def _inject_inline_app_scripts(
    html: str,
    scripts: Sequence[InlineScript],
    nonce: str | None,
) -> str:
    if not scripts:
        return html

    fragments: list[str] = []
    nonce_attr = f' nonce="{quote_attr(nonce)}"' if nonce else ""
    for script in scripts:
        code = script.code.replace("</script", "<\\/script")
        fragments.append(
            f'<script type="module"{nonce_attr} '
            f'data-app-router-inline-script="{quote_attr(script.key)}">\n'
            f"{code}\n"
            "</script>"
        )
    block = "\n".join(fragments)

    if re.search(r"</body>", html, flags=re.IGNORECASE):
        return re.sub(r"</body>", f"{block}\n</body>", html, count=1, flags=re.IGNORECASE)
    return f"{html}\n{block}"


def _csp_with_script_nonce(csp: str, nonce: str | None) -> str:
    if not nonce:
        return csp
    nonce_source = f"'nonce-{nonce}'"
    if nonce_source in csp:
        return csp

    script_src = re.search(r"(script-src\s+)([^;]+)", csp)
    if script_src:
        start, end = script_src.span(2)
        return f"{csp[:end]} {nonce_source}{csp[end:]}"

    separator = "" if csp.rstrip().endswith(";") else ";"
    return f"{csp}{separator} script-src 'self' {nonce_source}"


def _package_name() -> str:
    if __package__:
        return __package__
    return "app_router"
