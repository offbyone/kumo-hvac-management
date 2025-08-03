"""Version information for hvac-stability."""

try:
    from importlib.metadata import version
except ImportError:
    # Python < 3.8 fallback
    from importlib_metadata import version  # type: ignore

__version__ = version("hvac-stability")