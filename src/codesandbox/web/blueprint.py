from flask import Blueprint
from app_router import AppRouter

from codesandbox.web.csrf import install_csrf_protection

web_bp = Blueprint(
    "web",
    __name__,
    template_folder="../templates",
)


install_csrf_protection(web_bp)

TAILWIND_CDN_CSP = (
    "default-src 'self'; "
    # esm.sh: CodeMirror 6's editor is loaded as plain ES modules (dynamic
    # import()) from here — unlike jsdelivr's `+esm`, esm.sh deduplicates
    # shared sub-dependencies (@codemirror/state, @codemirror/view, ...)
    # consistently across separate import() calls, which jsdelivr does not:
    # each jsdelivr `+esm` request resolves its own dependency tree
    # independently, so CodeMirror's core bundle and its language/theme
    # packages end up with different, incompatible copies of the same
    # class (e.g. two non-identical `EditorState`) and the editor silently
    # fails to mount.
    "script-src 'self' https://cdn.jsdelivr.net https://esm.sh; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

router = AppRouter(web_bp, csp=TAILWIND_CDN_CSP)
