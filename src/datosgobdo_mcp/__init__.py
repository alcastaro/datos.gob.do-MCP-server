"""datosgobdo-mcp — Servidor MCP para datos.gob.do."""

import warnings

__version__ = "0.12.0"
USER_AGENT = f"datosgobdo-mcp/{__version__} (MCP Server)"

# The mcp SDK's settings model trips a pydantic_settings warning at import
# time ("Field 'lifespan' has an incomplete definition…"). It is not ours, it
# is harmless, and it prints to stderr on every start — which means every
# person who installs this server sees a warning as their first impression,
# and support reads it as the cause of whatever they wrote in about. Filtered
# by module and message so nothing else pydantic_settings might warn about is
# hidden with it.
warnings.filterwarnings(
    "ignore",
    message=r".*'lifespan' has an incomplete definition.*",
    module=r"pydantic_settings.*",
)
