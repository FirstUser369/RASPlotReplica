const assert = require("node:assert/strict");
const path = require("node:path");
const { loadConfigFromFile } = require("vite");

(async () => {
  const configPath = path.resolve(__dirname, "..", "vite.config.ts");
  const loaded = await loadConfigFromFile(
    { command: "serve", mode: "test" },
    configPath,
  );
  assert.ok(loaded, "Vite configuration must load");

  const apiProxy = loaded.config.server?.proxy?.["/api"];
  assert.equal(typeof apiProxy, "object");
  assert.equal(apiProxy.target, "http://127.0.0.1:8765");
  assert.equal(apiProxy.changeOrigin, true);
  assert.equal(typeof apiProxy.configure, "function");

  let proxyRequestHandler;
  apiProxy.configure({
    on(event, handler) {
      if (event === "proxyReq") proxyRequestHandler = handler;
    },
  });
  assert.equal(typeof proxyRequestHandler, "function");

  function forward(headers) {
    const originalHeaders = structuredClone(headers);
    const written = [];
    proxyRequestHandler(
      { setHeader(name, value) { written.push([name.toLowerCase(), value]); } },
      { headers },
    );
    assert.deepEqual(headers, originalHeaders, "incoming security headers must remain unchanged");
    return written;
  }

  for (const origin of ["http://127.0.0.1:5173", "http://localhost:5173"]) {
    assert.deepEqual(forward({ origin }), [["origin", "http://127.0.0.1:8765"]]);
  }

  for (const origin of [
    "https://foreign.example",
    "http://127.0.0.1:5174",
    "not a valid origin",
  ]) {
    assert.deepEqual(forward({ origin }), [], `must not trust ${origin}`);
  }
  assert.deepEqual(forward({}), [], "missing Origin must not be synthesized");
  assert.deepEqual(forward({ origin: ["http://127.0.0.1:5173"] }), [], "ambiguous Origin must not be trusted");

  const crossSite = { origin: "http://127.0.0.1:5173", "sec-fetch-site": "cross-site" };
  assert.deepEqual(
    forward(crossSite),
    [["origin", "http://127.0.0.1:8765"]],
    "trusted proxy origin may be normalized without hiding the cross-site signal",
  );
  assert.equal(crossSite["sec-fetch-site"], "cross-site");

  for (const route of [
    "/api/source/browse",
    "/api/project/browse-open",
    "/api/project/browse-save",
    "/api/project/session-id/save",
  ]) {
    assert.ok(route.startsWith("/api"), `${route} must use the secured development proxy`);
  }

  console.log("PASS: 12 secure Vite development proxy contracts");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
