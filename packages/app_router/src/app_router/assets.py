"""Manifest-backed route asset handling."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from flask import Response, abort, send_file
from jinja2 import Environment, TemplateNotFound

from .exceptions import AssetSecurityError

ALLOWED_ASSET_EXTENSIONS = frozenset(
    {
        ".js",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
        ".woff",
        ".woff2",
    }
)
LOCAL_ASSET_RE = re.compile(r"^\./([A-Za-z0-9][A-Za-z0-9._-]*)$")
ALIAS_ASSET_RE = re.compile(r"^@/([A-Za-z0-9][A-Za-z0-9._/-]*)$")
SOURCE_ASSET_RE = re.compile(r"""(?:src|href)\s*=\s*["']((?:\./|@/)[^"']+)["']""")


@dataclass(frozen=True)
class AssetRecord:
    id: str
    path: Path
    mimetype: str
    private: bool
    max_age: int


@dataclass
class RewriteResult:
    html: str
    scripts: list[str]
    styles: list[str]


@dataclass(frozen=True)
class AssetRoute:
    endpoint: str
    asset_private: bool
    asset_max_age: int


class AssetManager:
    """Register and serve only manifest-approved local route assets."""

    def __init__(self, *, url_prefix: str = "/_app/assets") -> None:
        self.url_prefix = url_prefix.rstrip("/")
        self._records: dict[str, AssetRecord] = {}
        self._source_index: dict[str, str] = {}

    @property
    def records(self) -> dict[str, AssetRecord]:
        return self._records

    def get(self, asset_id: str) -> AssetRecord | None:
        return self._records.get(asset_id)

    def build_manifest(
        self,
        env: Environment,
        *,
        routes: Iterable[tuple[AssetRoute, Iterable[str]]],
        output_dir: Path,
    ) -> dict[str, Any]:
        """Build a route asset manifest without rendering page loaders."""

        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        manifest_assets: dict[str, dict[str, Any]] = {}
        route_assets: dict[str, list[str]] = {}
        route_styles: dict[str, list[str]] = {}
        route_scripts: dict[str, list[str]] = {}

        for route, template_names in routes:
            for template_name in template_names:
                try:
                    source, _, _ = self._template_source(env, template_name)
                except TemplateNotFound:
                    continue
                for raw_url in SOURCE_ASSET_RE.findall(source):
                    asset_url = self.asset_url(env, template_name, raw_url, route)
                    asset_id = asset_url.rsplit("/", 1)[-1]
                    record = self._records[asset_id]
                    target = assets_dir / asset_id
                    if not target.exists():
                        shutil.copy2(record.path, target)

                    key = self._source_key(route.endpoint, template_name, raw_url)
                    self._source_index[key] = asset_id
                    route_assets.setdefault(route.endpoint, [])
                    if asset_id not in route_assets[route.endpoint]:
                        route_assets[route.endpoint].append(asset_id)
                    if asset_id.endswith(".js"):
                        route_scripts.setdefault(route.endpoint, [])
                        if asset_url not in route_scripts[route.endpoint]:
                            route_scripts[route.endpoint].append(asset_url)
                    if asset_id.endswith(".css"):
                        route_styles.setdefault(route.endpoint, [])
                        if asset_url not in route_styles[route.endpoint]:
                            route_styles[route.endpoint].append(asset_url)

                    manifest_assets[asset_id] = {
                        "id": asset_id,
                        "url": asset_url,
                        "file": f"assets/{asset_id}",
                        "source": str(record.path),
                        "mimetype": record.mimetype,
                        "private": record.private,
                        "max_age": record.max_age,
                        "endpoint": route.endpoint,
                        "template": template_name,
                        "import": raw_url,
                    }

        manifest = {
            "version": 1,
            "url_prefix": self.url_prefix,
            "assets": dict(sorted(manifest_assets.items())),
            "source_index": dict(sorted(self._source_index.items())),
            "routes": {
                endpoint: {
                    "assets": asset_ids,
                    "scripts": route_scripts.get(endpoint, []),
                    "styles": route_styles.get(endpoint, []),
                }
                for endpoint, asset_ids in sorted(route_assets.items())
            },
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest

    def load_manifest(self, manifest_path: Path) -> None:
        if not manifest_path.is_file():
            return

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assets_dir = manifest_path.parent
        for asset_id, data in manifest.get("assets", {}).items():
            asset_path = (assets_dir / data["file"]).resolve(strict=True)
            self._records[asset_id] = AssetRecord(
                id=asset_id,
                path=asset_path,
                mimetype=str(data["mimetype"]),
                private=bool(data["private"]),
                max_age=int(data["max_age"]),
            )
        self._source_index.update(
            {str(key): str(value) for key, value in manifest.get("source_index", {}).items()}
        )

    def rewrite_html(
        self,
        html: str,
        *,
        env: Environment,
        template_name: str,
        route: AssetRoute,
    ) -> RewriteResult:
        rewriter = _AssetHTMLRewriter(self, env=env, template_name=template_name, route=route)
        rewriter.feed(html)
        rewriter.close()
        return RewriteResult(
            html=rewriter.output,
            scripts=rewriter.scripts,
            styles=rewriter.styles,
        )

    def asset_url(
        self,
        env: Environment,
        template_name: str,
        raw_url: str,
        route: AssetRoute,
    ) -> str:
        indexed = self._source_index.get(self._source_key(route.endpoint, template_name, raw_url))
        if indexed:
            return f"{self.url_prefix}/{indexed}"

        target = self._resolve_asset_path(env, template_name, raw_url)
        extension = target.suffix.lower()
        if extension not in ALLOWED_ASSET_EXTENSIONS:
            raise AssetSecurityError(f"Asset extension is not allowed: {extension}")

        content = target.read_bytes()
        digest = hashlib.sha256()
        digest.update(route.endpoint.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(target.resolve(strict=True)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        asset_id = f"{digest.hexdigest()[:20]}{extension}"

        mimetype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._records[asset_id] = AssetRecord(
            id=asset_id,
            path=target.resolve(strict=True),
            mimetype=mimetype,
            private=route.asset_private,
            max_age=route.asset_max_age,
        )
        return f"{self.url_prefix}/{asset_id}"

    def source_imports(self, env: Environment, template_name: str) -> list[str]:
        source, _, _ = self._template_source(env, template_name)
        return SOURCE_ASSET_RE.findall(source)

    def serve(self, asset_id: str) -> Response:
        record = self._records.get(asset_id)
        if record is None:
            abort(404)

        response = send_file(
            record.path,
            mimetype=record.mimetype,
            conditional=True,
            etag=True,
            max_age=None if record.private else record.max_age,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        if record.private:
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Cache-Control"] = f"public, max-age={record.max_age}, immutable"
        return response

    def _resolve_asset_path(self, env: Environment, template_name: str, raw_url: str) -> Path:
        if raw_url.startswith("@/"):
            return self._resolve_alias_ref(env, raw_url)

        filename = self._validate_local_ref(raw_url)
        template_path = self._template_path(env, template_name)
        template_dir = template_path.parent.resolve(strict=True)
        target = (template_dir / filename).resolve(strict=False)

        if target.parent != template_dir:
            raise AssetSecurityError(f"Asset reference escapes template directory: {raw_url}")
        if not target.is_file():
            raise AssetSecurityError(f"Asset file does not exist: {raw_url}")
        if target.resolve(strict=True).parent != template_dir:
            raise AssetSecurityError(f"Asset symlink escapes template directory: {raw_url}")
        return target.resolve(strict=True)

    def _validate_local_ref(self, raw_url: str) -> str:
        if raw_url.startswith("../") or "/../" in raw_url:
            raise AssetSecurityError("Parent directory asset imports are not allowed.")
        match = LOCAL_ASSET_RE.match(raw_url)
        if not match:
            raise AssetSecurityError(f"Only ./file or @/path asset imports are allowed: {raw_url}")
        filename = match.group(1)
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_ASSET_EXTENSIONS:
            raise AssetSecurityError(f"Asset extension is not allowed: {extension}")
        return filename

    def _resolve_alias_ref(self, env: Environment, raw_url: str) -> Path:
        if raw_url.startswith("@/../") or "/../" in raw_url or "\\" in raw_url:
            raise AssetSecurityError("Parent directory asset imports are not allowed.")

        match = ALIAS_ASSET_RE.match(raw_url)
        if not match:
            raise AssetSecurityError(f"Only ./file or @/path asset imports are allowed: {raw_url}")

        template_name = match.group(1)
        extension = Path(template_name).suffix.lower()
        if extension not in ALLOWED_ASSET_EXTENSIONS:
            raise AssetSecurityError(f"Asset extension is not allowed: {extension}")

        target = self._template_path(env, template_name)
        if not target.is_file():
            raise AssetSecurityError(f"Asset file does not exist: {raw_url}")
        if self._has_symlink_part(target):
            raise AssetSecurityError(f"Asset symlink escapes template directory: {raw_url}")
        return target.resolve(strict=True)

    def _template_path(self, env: Environment, template_name: str) -> Path:
        if env.loader is None:
            raise AssetSecurityError("Cannot resolve local assets without a Jinja loader.")
        _, filename, _ = self._template_source(env, template_name)
        if not filename:
            raise AssetSecurityError(f"Cannot resolve local assets for template: {template_name}")
        return Path(filename)

    def _template_source(self, env: Environment, template_name: str) -> tuple[str, str | None, Any]:
        if env.loader is None:
            raise AssetSecurityError("Cannot resolve local assets without a Jinja loader.")
        return env.loader.get_source(env, template_name)

    def _source_key(self, endpoint: str, template_name: str, raw_url: str) -> str:
        return f"{endpoint}\0{template_name}\0{raw_url}"

    def _has_symlink_part(self, path: Path) -> bool:
        current = path
        while current != current.parent:
            if current.is_symlink():
                return True
            current = current.parent
        return False


class _AssetHTMLRewriter(HTMLParser):
    def __init__(
        self,
        manager: AssetManager,
        *,
        env: Environment,
        template_name: str,
        route: AssetRoute,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.manager = manager
        self.env = env
        self.template_name = template_name
        self.route = route
        self.parts: list[str] = []
        self.scripts: list[str] = []
        self.styles: list[str] = []

    @property
    def output(self) -> str:
        return "".join(self.parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self._render_tag(tag, attrs, closed=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self._render_tag(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<![{data}]>")

    def _render_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, closed: bool) -> str:
        rendered_attrs: list[str] = []
        for name, value in attrs:
            next_value = value
            if value and self._should_rewrite(tag, name, value):
                next_value = self.manager.asset_url(self.env, self.template_name, value, self.route)
                if next_value.endswith(".js"):
                    self.scripts.append(next_value)
                elif next_value.endswith(".css"):
                    self.styles.append(next_value)
            elif value and self._is_forbidden_relative_asset(tag, name, value):
                self.manager._validate_local_ref(value)

            if next_value is None:
                rendered_attrs.append(html_escape(name, quote=True))
            else:
                rendered_attrs.append(
                    f'{html_escape(name, quote=True)}="{html_escape(next_value, quote=True)}"'
                )

        suffix = " /" if closed else ""
        if rendered_attrs:
            return f"<{tag} {' '.join(rendered_attrs)}{suffix}>"
        return f"<{tag}{suffix}>"

    def _should_rewrite(self, tag: str, attr: str, value: str) -> bool:
        tag = tag.lower()
        attr = attr.lower()
        if not (value.startswith("./") or value.startswith("@/")):
            return False
        if tag == "script" and attr == "src":
            return True
        if tag == "link" and attr == "href":
            return True
        return tag in {"img", "source"} and attr == "src"

    def _is_forbidden_relative_asset(self, tag: str, attr: str, value: str) -> bool:
        tag = tag.lower()
        attr = attr.lower()
        if not (value.startswith("./") or value.startswith("../") or value.startswith("@/")):
            return False
        return (tag == "script" and attr == "src") or (
            tag in {"link", "img", "source"} and attr in {"href", "src"}
        )


def combine_asset_lists(*groups: Iterable[str]) -> list[str]:
    combined: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for url in group:
            if url not in seen:
                seen.add(url)
                combined.append(url)
    return combined
