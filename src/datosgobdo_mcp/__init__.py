"""datosgobdo-mcp — Servidor MCP para datos.gob.do."""

import warnings

__version__ = "0.14.0"
USER_AGENT = f"datosgobdo-mcp/{__version__} (MCP Server)"

# The mcp SDK's settings model trips a pydantic_settings warning at import
# time ("Field 'lifespan' has an incomplete definition…"). It is not ours, it
# is harmless, and it prints to stderr on every start — which means every
# person who installs this server sees a warning as their first impression,
# and support reads it as the cause of whatever they wrote in about. Matched on
# the message alone, which is specific enough to hide nothing else.
#
# It also required module=pydantic_settings.* until now, and that is why a
# Windows tester saw this warning on their first run while a test asserted it was
# filtered: `module` matches the module a warning is *attributed* to, which
# depends on the stacklevel the emitting library passes — here the mcp module
# defining the settings class, not pydantic_settings itself. The test proved the
# regex against a synthetic warning it attributed by hand, so the code and the
# test agreed with each other and neither agreed with a real start-up.
warnings.filterwarnings(
    "ignore",
    message=r".*'lifespan' has an incomplete definition.*",
)
