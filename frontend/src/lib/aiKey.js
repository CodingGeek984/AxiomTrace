const STORAGE_KEY = "axiomtrace_deepseek_key";

/**
 * Validate the shape of a DeepSeek/OpenAI-compatible API key.
 * Returns { ok, error } so callers can surface a precise message.
 */
export function validateApiKey(key) {
  const value = typeof key === "string" ? key.trim() : "";
  if (!value) {
    return { ok: false, error: "Please enter your API key." };
  }
  if (/\s/.test(value)) {
    return { ok: false, error: "The API key must not contain spaces." };
  }
  if (!value.startsWith("sk-")) {
    return {
      ok: false,
      error: "Unexpected key format - DeepSeek keys start with “sk-”.",
    };
  }
  if (value.length < 12) {
    return { ok: false, error: "The API key looks too short." };
  }
  return { ok: true, error: null };
}

export function getDeepSeekKey() {
  if (typeof window === "undefined") return null;
  const key = window.localStorage.getItem(STORAGE_KEY);
  if (typeof key !== "string") return null;
  const trimmed = key.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function setDeepSeekKey(key) {
  if (typeof window === "undefined") return;
  if (typeof key !== "string") {
    removeDeepSeekKey();
    return;
  }
  const trimmed = key.trim();
  if (trimmed.length === 0) {
    removeDeepSeekKey();
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, trimmed);
}

export function removeDeepSeekKey() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}

export function hasDeepSeekKey() {
  return Boolean(getDeepSeekKey());
}

export function maskDeepSeekKey(key) {
  const value = typeof key === "string" ? key.trim() : "";
  if (!value) return "";
  if (value.length <= 8) return "••••••••";
  return `${value.slice(0, 4)}••••••••${value.slice(-4)}`;
}
