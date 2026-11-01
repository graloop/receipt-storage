// Every call to the server goes through api(): it turns an error answer into a
// thrown Error with a readable message and the HTTP status (err.status).

import { t } from "./i18n.js";

export async function api(path, options = {}) {
  const headers = { ...options.headers };
  // A file upload (FormData) sets its own Content-Type.
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(path, { ...options, headers });
  } catch (_) {
    throw new Error(t("Can't reach the app. Check that it's running."));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      if (res.status >= 500)
        detail = t("The server didn't answer (error {status}). Try again in a minute.", { status: res.status });
    }
    if (!detail) detail = t("Error {status}", { status: res.status });
    if (typeof detail !== "string") detail = detail.map((d) => d.msg).join("; "); // validation errors
    const error = new Error(t(detail));
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}

export function json(method, body) {
  return { method, body: JSON.stringify(body) };
}
