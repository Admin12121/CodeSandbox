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
    # js.stripe.com: Stripe's embedded CardElement (billing top-up). Stripe
    # requires loading their script directly from this host (no self-hosting
    # / SRI — it's part of their PCI-DSS SAQ A compliance story), it opens a
    # frame for the actual card input (frame-src), and confirmCardPayment
    # calls out to their API directly from the browser (connect-src).
    "script-src 'self' https://cdn.jsdelivr.net https://esm.sh https://js.stripe.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "frame-src https://js.stripe.com; "
    "connect-src 'self' https://api.stripe.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

router = AppRouter(web_bp, csp=TAILWIND_CDN_CSP)
