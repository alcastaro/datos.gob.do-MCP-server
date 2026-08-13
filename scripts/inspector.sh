#!/bin/sh
# Open the MCP Inspector against this server.
#
#   ./scripts/inspector.sh                       # the published package
#   ./scripts/inspector.sh dist/…-py3-none-any.whl   # a local build
#   ./scripts/inspector.sh --cli --method tools/list --format json
#
# Why this exists: the Inspector treats everything after the server command as
# its own flags, so `uvx --from ./dist/….whl dominican-open-data-mcp` fails
# with "Connection closed" — it swallows `--from`. Testing a local wheel needs
# a launcher with no flags of its own, which is also what a real client config
# looks like. Against the published package no launcher is needed at all.
#
# Requires Node 22.19+ (for npx) and uv.

set -eu

INSPECTOR="@modelcontextprotocol/inspector"
WHEEL=""

# A first argument that looks like a wheel selects a local build; anything else
# is passed through to the Inspector (--cli, --method, …).
case "${1:-}" in
    *.whl)
        WHEEL="$1"
        shift
        ;;
esac

if [ -n "$WHEEL" ]; then
    [ -f "$WHEEL" ] || { echo "No such wheel: $WHEEL" >&2; exit 1; }
    WHEEL_ABS=$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")
    LAUNCHER=$(mktemp -t datosgobdo-inspector)
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec uvx --from "$WHEEL_ABS" dominican-open-data-mcp
EOF
    chmod +x "$LAUNCHER"
    trap 'rm -f "$LAUNCHER"' EXIT INT TERM
    echo "Inspecting local build: $WHEEL_ABS" >&2

    # Mode flags are only recognised at the front of the command line, so any
    # of ours have to precede the launcher; the rest follow it.
    case "${1:-}" in
        --cli|--tui|--web)
            MODE="$1"
            shift
            exec npx -y "$INSPECTOR" "$MODE" "$LAUNCHER" "$@"
            ;;
    esac
    exec npx -y "$INSPECTOR" "$LAUNCHER" "$@"
fi

echo "Inspecting the published package (uvx dominican-open-data-mcp)" >&2
case "${1:-}" in
    --cli|--tui|--web)
        MODE="$1"
        shift
        exec npx -y "$INSPECTOR" "$MODE" uvx dominican-open-data-mcp "$@"
        ;;
esac
exec npx -y "$INSPECTOR" uvx dominican-open-data-mcp "$@"
