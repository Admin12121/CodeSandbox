from __future__ import annotations

from collections.abc import Callable
import json

from flask import Blueprint, Response, abort, current_app, request
from app_router import CSRFError, csrf_token, validate_csrf

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

type ExemptRule = Callable[[], bool]

_exempt_rules: list[ExemptRule] = []


def exempt_when(rule: ExemptRule) -> ExemptRule:
    """Register a request-time rule that skips web CSRF validation."""
    _exempt_rules.append(rule)
    return rule


def csrf_exempt(view: Callable) -> Callable:
    """Mark a specific Flask view as exempt from web CSRF validation."""
    setattr(view, "_csrf_exempt", True)
    return view


def install_csrf_protection(blueprint: Blueprint) -> None:
    """Protect all unsafe requests on a blueprint from one central hook."""

    @blueprint.get("/csrf-fetch.js")
    def _csrf_fetch_script():
        token = json.dumps(csrf_token())
        body = f"""(function(){{
  'use strict';
  if(window.__codesandboxCsrfFetchInstalled) return;
  window.__codesandboxCsrfFetchInstalled = true;

  var CSRF_TOKEN = {token};
  var UNSAFE = {{POST:true, PUT:true, PATCH:true, DELETE:true}};
  var nativeFetch = window.fetch;

  window.fetch = function(input, init){{
    init = init || {{}};
    var method = (init.method || (input && input.method) || 'GET').toUpperCase();
    if(UNSAFE[method]){{
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var sameOrigin = !url || url.charAt(0) === '/' || url.indexOf(window.location.origin) === 0;
      if(sameOrigin){{
        var headers = new Headers(init.headers || (input && input.headers) || {{}});
        if(!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', CSRF_TOKEN);
        init.headers = headers;
      }}
    }}
    return nativeFetch(input, init);
  }};
}})();
"""
        response = Response(body, mimetype="text/javascript")
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.before_request
    def _protect_unsafe_methods():
        if not should_validate_csrf():
            return None
        try:
            validate_csrf(raise_error=True)
        except CSRFError as exc:
            abort(400, description=str(exc))
        return None


def should_validate_csrf() -> bool:
    if request.method.upper() not in UNSAFE_METHODS:
        return False
    if request.endpoint and _view_is_exempt(request.endpoint):
        return False
    return not any(rule() for rule in _exempt_rules)


def _view_is_exempt(endpoint: str) -> bool:
    view = current_app.view_functions.get(endpoint)
    return bool(view and getattr(view, "_csrf_exempt", False))
