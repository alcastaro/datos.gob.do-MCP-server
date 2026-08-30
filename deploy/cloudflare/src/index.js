import { Container, getContainer } from "@cloudflare/containers";

/**
 * The container that actually speaks MCP.
 *
 * Every environment variable here has a counterpart in the Dockerfile. They are
 * repeated rather than inherited because the two files are deployed by
 * different people at different times: the Dockerfile is what Cloud Run or Fly
 * would use, this is what Cloudflare uses, and a value that exists in only one
 * of them is a difference nobody notices until the numbers disagree.
 */
export class DatosgobdoContainer extends Container {
  defaultPort = 8080;
  // The catalog is not a chat backend; sessions arrive in bursts and then stop.
  // Sleeping after ten idle minutes is what keeps the bill proportional to use.
  sleepAfter = "10m";

  envVars = {
    DATOSGOBDO_TRANSPORT: "streamable-http",
    DATOSGOBDO_HOST: "0.0.0.0",
    DATOSGOBDO_PORT: "8080",
    DATOSGOBDO_NETGUARD: "public-only",
    DATOSGOBDO_CACHE_DIR: "/cache",
    DATOSGOBDO_CACHE_MAX_BYTES: "536870912",
    DATOSGOBDO_DUCKDB_MEMORY: "512MB",
    DATOSGOBDO_DUCKDB_THREADS: "2",
    // 0 (the default) means no limit, which is right locally and wrong here:
    // the SQL arrives from a model and nothing else bounds how long it runs.
    DATOSGOBDO_QUERY_TIMEOUT: "30",
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // OpenAI verifies the domain by reading a token from the same host that
    // serves the MCP endpoint. Answering it here, in the Worker, is why the
    // Worker exists at all — the container never needs to know about it.
    if (url.pathname === "/.well-known/openai-apps") {
      const token = env.OPENAI_VERIFICATION_TOKEN;
      if (!token) return new Response("not configured", { status: 404 });
      return new Response(token, {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    // A plain browser hitting the root gets a signpost instead of a protocol
    // error. The MCP endpoint answers POST; a person pasting the domain into a
    // browser is not making a mistake worth a 400.
    if (url.pathname === "/" && request.method === "GET") {
      return new Response(
        "datosgobdo-mcp — MCP endpoint at /mcp\n" +
          "https://github.com/alcastaro/datos.gob.do-MCP-server\n",
        { headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }

    return getContainer(env.DATOSGOBDO).fetch(request);
  },
};
