import fs from "node:fs";
import path from "node:path";
import http from "node:http";

const repoRoot = "C:/projects/sites/blueprint-rec-2";
const nextStaticRoot = path.join(repoRoot, "apps", "web", ".next", "static");
const publicRoot = path.join(repoRoot, "apps", "web", "public");
const proxyHost = "127.0.0.1";
const upstreamPort = Number(process.env.BLUEPRINT_UPSTREAM_PORT || "3010");
const listenPort = Number(process.env.BLUEPRINT_PUBLIC_PROXY_PORT || "3020");

const contentTypes = new Map([
  [".js", "application/javascript; charset=UTF-8"],
  [".css", "text/css; charset=UTF-8"],
  [".json", "application/json; charset=UTF-8"],
  [".ico", "image/x-icon"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".svg", "image/svg+xml"],
  [".webp", "image/webp"],
  [".txt", "text/plain; charset=UTF-8"],
  [".xml", "application/xml; charset=UTF-8"],
]);

function safeJoin(root, relativePath) {
  const normalized = path.normalize(relativePath).replace(/^(\.\.[\\/])+/, "");
  const resolved = path.resolve(root, normalized);
  const rootResolved = path.resolve(root);
  if (!resolved.startsWith(rootResolved)) {
    return null;
  }
  return resolved;
}

function sendNotFound(res) {
  res.writeHead(404, { "content-type": "text/plain; charset=UTF-8" });
  res.end("Not found");
}

function sendError(res, statusCode, message) {
  res.writeHead(statusCode, { "content-type": "text/plain; charset=UTF-8" });
  res.end(message);
}

function serveFile(filePath, req, res, extraHeaders = {}) {
  fs.stat(filePath, (statError, stats) => {
    if (statError || !stats.isFile()) {
      sendNotFound(res);
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    const headers = {
      "content-type": contentTypes.get(ext) || "application/octet-stream",
      "content-length": String(stats.size),
      ...extraHeaders,
    };
    res.writeHead(200, headers);
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    const stream = fs.createReadStream(filePath);
    stream.on("error", () => {
      if (!res.headersSent) {
        sendError(res, 500, "File stream failed");
      } else {
        res.destroy();
      }
    });
    stream.pipe(res);
  });
}

function maybeServeStatic(req, res) {
  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  const pathname = decodeURIComponent(url.pathname);

  if (pathname.startsWith("/_next/static/")) {
    const relativePath = pathname.replace("/_next/static/", "");
    const filePath = safeJoin(nextStaticRoot, relativePath);
    if (!filePath) {
      sendNotFound(res);
      return true;
    }
    serveFile(filePath, req, res, {
      "cache-control": "public, max-age=31536000, immutable",
      "accept-ranges": "bytes",
    });
    return true;
  }

  if (!pathname.startsWith("/api/") && !pathname.startsWith("/storage/")) {
    const publicRelative = pathname === "/" ? null : pathname.slice(1);
    if (publicRelative) {
      const publicPath = safeJoin(publicRoot, publicRelative);
      if (publicPath && fs.existsSync(publicPath) && fs.statSync(publicPath).isFile()) {
        serveFile(publicPath, req, res, {
          "cache-control": "public, max-age=3600",
          "accept-ranges": "bytes",
        });
        return true;
      }
    }
  }

  return false;
}

function proxyRequest(req, res) {
  const requestOptions = {
    hostname: proxyHost,
    port: upstreamPort,
    path: req.url,
    method: req.method,
    headers: {
      ...req.headers,
      host: `${proxyHost}:${upstreamPort}`,
      connection: "close",
    },
  };

  const upstream = http.request(requestOptions, (upstreamRes) => {
    const headers = { ...upstreamRes.headers };
    if (!headers["cache-control"] && (req.url || "").startsWith("/_next/")) {
      headers["cache-control"] = "public, max-age=31536000, immutable";
    }
    res.writeHead(upstreamRes.statusCode || 502, headers);
    upstreamRes.pipe(res);
  });

  upstream.on("error", (error) => {
    sendError(res, 502, `Upstream request failed: ${String(error)}`);
  });

  req.pipe(upstream);
}

const server = http.createServer((req, res) => {
  if (maybeServeStatic(req, res)) {
    return;
  }
  proxyRequest(req, res);
});

server.keepAliveTimeout = 5_000;
server.headersTimeout = 15_000;

server.listen(listenPort, proxyHost, () => {
  console.log(`Blueprint public proxy listening on http://${proxyHost}:${listenPort} -> ${upstreamPort}`);
});
