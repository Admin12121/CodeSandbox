"""Package-specific exceptions."""


class AppRouterError(RuntimeError):
    """Base class for router errors."""


class AssetSecurityError(AppRouterError):
    """Raised when a local asset reference violates resolver rules."""


class CSRFError(AppRouterError):
    """Raised when CSRF validation fails."""
