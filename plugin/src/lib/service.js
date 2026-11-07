export function normalizeServiceUrl(value) {
  const parsed = new URL(String(value || "").trim());
  if (parsed.protocol !== "https:") {
    throw new Error("The signup service URL must use HTTPS.");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("Enter the HTTPS origin only, without credentials, query or fragment.");
  }
  if (parsed.pathname !== "/" && parsed.pathname !== "") {
    throw new Error("Enter the service origin only, without a path.");
  }
  return parsed.origin;
}

export function parseResponse(response) {
  let body = response && response.body;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch {
      body = { detail: body };
    }
  }
  if (!response || !response.ok) {
    const message = body && (body.detail || body.error || body.message);
    throw new Error(message || (response && response.proxyError) || ("HTTP " + (response ? response.status : "unknown")));
  }
  return body || {};
}
