const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8766";
const agentInput = document.getElementById("agent");
const handleInput = document.getElementById("handle");
const bridgeInput = document.getElementById("bridge-url");
const originStatus = document.getElementById("origin");
const bridgeStatus = document.getElementById("bridge");
const status = document.getElementById("status");
const fillButton = document.getElementById("fill");
const refreshButton = document.getElementById("refresh");

let pageState = {tabId: null, origin: null, supported: false};
let bridgeReady = false;

function setStatus(message, kind = "") {
  status.className = kind ? `status-message ${kind}` : "status-message";
  status.textContent = message;
}

function setElementStatus(element, message, kind = "") {
  // Keep the presentational class chosen by the popup markup. This lets the
  // status renderer work with both the compact `status-row` cards and older
  // `status-chip` markup without changing the bridge contract.
  const baseClass = element.classList.contains("status-row")
    ? "status-row"
    : element.classList.contains("status-chip")
      ? "status-chip"
      : "status-row";
  element.className = kind ? `${baseClass} ${kind}` : baseClass;
  element.textContent = message;
}

function normalizeBridgeUrl(value) {
  const candidate = String(value || "").trim();
  let parsed;
  try {
    parsed = new URL(candidate || DEFAULT_BRIDGE_URL);
  } catch (_) {
    throw new Error("Bridge URL is invalid. Use the loopback fill-server URL.");
  }
  if (
    parsed.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsed.hostname) ||
    parsed.username ||
    parsed.password
  ) {
    throw new Error("Bridge URL must use an HTTP loopback address.");
  }
  return parsed.origin;
}

function storageGet(defaults) {
  if (!chrome.storage?.local) return Promise.resolve(defaults);
  return chrome.storage.local.get(defaults).catch(() => defaults);
}

function storageSet(values) {
  if (!chrome.storage?.local) return Promise.resolve();
  return chrome.storage.local.set(values).catch(() => undefined);
}

async function loadPreferences() {
  const values = await storageGet({agentId: "codex", bridgeUrl: DEFAULT_BRIDGE_URL});
  agentInput.value = typeof values.agentId === "string" ? values.agentId : "codex";
  try {
    bridgeInput.value = normalizeBridgeUrl(values.bridgeUrl);
  } catch (_) {
    bridgeInput.value = DEFAULT_BRIDGE_URL;
  }
}

function savePreferences() {
  const agentId = agentInput.value.trim();
  let bridgeUrl;
  try {
    bridgeUrl = normalizeBridgeUrl(bridgeInput.value);
  } catch (_) {
    return;
  }
  // Deliberately persist only nonsecret preferences. The one-time handle is
  // never passed to chrome.storage and is cleared in the fill finally block.
  void storageSet({agentId, bridgeUrl});
}

async function activeTabState() {
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  const tab = tabs?.[0];
  if (!tab?.id || !tab.url) {
    return {tabId: null, origin: null, supported: false};
  }
  try {
    const parsed = new URL(tab.url);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return {tabId: tab.id, origin: null, supported: false};
    }
    return {tabId: tab.id, origin: parsed.origin, supported: true};
  } catch (_) {
    return {tabId: tab.id, origin: null, supported: false};
  }
}

function renderOrigin() {
  if (!pageState.supported) {
    setElementStatus(
      originStatus,
      "Active origin: unavailable (open an HTTP or HTTPS page)",
      "warn",
    );
    return;
  }
  setElementStatus(originStatus, `Active origin: ${pageState.origin}`, "ok");
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 1800) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } finally {
    clearTimeout(timeout);
  }
}

function recoveryMessage(error, fallback) {
  if (error?.name === "AbortError") {
    return "The loopback bridge timed out; restart fill-server and retry with a fresh handle.";
  }
  if (error instanceof TypeError) {
    return "The loopback bridge could not be reached; start fill-server and retry with a fresh handle.";
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

async function checkBridge() {
  let bridgeUrl;
  try {
    bridgeUrl = normalizeBridgeUrl(bridgeInput.value);
    bridgeInput.value = bridgeUrl;
  } catch (error) {
    bridgeReady = false;
    setElementStatus(bridgeStatus, error.message, "error");
    fillButton.disabled = true;
    return false;
  }
  try {
    const response = await fetchWithTimeout(`${bridgeUrl}/v1/consume`, {
      method: "OPTIONS",
      mode: "cors",
    });
    bridgeReady = response.status === 204 || response.ok;
    setElementStatus(
      bridgeStatus,
      bridgeReady
        ? `Bridge: available at ${bridgeUrl}`
        : `Bridge: returned HTTP ${response.status}; restart fill-server and retry`,
      bridgeReady ? "ok" : "warn",
    );
  } catch (_) {
    bridgeReady = false;
    setElementStatus(
      bridgeStatus,
      `Bridge: unavailable at ${bridgeUrl}. Start the loopback fill-server, then refresh.`,
      "warn",
    );
  }
  fillButton.disabled = !bridgeReady || !pageState.supported;
  return bridgeReady;
}

async function refreshState() {
  refreshButton.disabled = true;
  setStatus("Checking local bridge and active origin…");
  try {
    pageState = await activeTabState();
    renderOrigin();
    await checkBridge();
  } catch (_) {
    pageState = {tabId: null, origin: null, supported: false};
    renderOrigin();
    bridgeReady = false;
    fillButton.disabled = true;
    setElementStatus(bridgeStatus, "Bridge status could not be checked; retry.", "warn");
  } finally {
    refreshButton.disabled = false;
    if (!bridgeReady || !pageState.supported) {
      setStatus("Resolve the origin or bridge message above before consuming a handle.", "warn");
    } else {
      setStatus("");
    }
  }
}

function fillLoginForm(username, password) {
  const user = document.querySelector(
    'input[autocomplete="username"], input[type="email"], input[name*="user" i], input[id*="user" i]'
  );
  const pass = document.querySelector('input[type="password"]');
  const setValue = (element, value) => {
    if (!element) return false;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    ).set;
    setter.call(element, value);
    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
    return true;
  };
  return {
    usernameFilled: setValue(user, username),
    passwordFilled: setValue(pass, password),
  };
}

agentInput.addEventListener("input", savePreferences);
bridgeInput.addEventListener("change", () => {
  savePreferences();
  void refreshState();
});
refreshButton.addEventListener("click", () => void refreshState());

fillButton.addEventListener("click", async () => {
  const handle = handleInput.value;
  const agentId = agentInput.value.trim();
  handleInput.value = "";
  if (!handle || !agentId) {
    setStatus("Enter the agent ID and one-time handle.", "warn");
    return;
  }
  savePreferences();
  fillButton.disabled = true;
  setStatus("Checking the active origin before consuming the handle…");
  try {
    pageState = await activeTabState();
    renderOrigin();
    if (!pageState.supported) {
      throw new Error("Open the supported HTTPS page before consuming the handle.");
    }
    if (!(await checkBridge())) {
      throw new Error("The loopback bridge is unavailable; no handle was consumed.");
    }
    const bridgeUrl = normalizeBridgeUrl(bridgeInput.value);
    const response = await fetchWithTimeout(`${bridgeUrl}/v1/consume`, {
      method: "POST",
      mode: "cors",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({handle, agent_id: agentId, target_origin: pageState.origin}),
    }, 5000);
    let body;
    try {
      body = await response.json();
    } catch (_) {
      throw new Error("The bridge returned an invalid response; restart fill-server and retry.");
    }
    if (!response.ok) {
      throw new Error(body?.error || "The one-time handle was rejected; request a fresh handle.");
    }
    if (typeof body?.username !== "string" || typeof body?.password !== "string") {
      throw new Error("The bridge response did not contain fill data; request a fresh handle.");
    }
    const result = await chrome.scripting.executeScript({
      target: {tabId: pageState.tabId},
      func: fillLoginForm,
      args: [body.username, body.password],
    });
    const filled = result?.[0]?.result;
    setStatus(
      filled?.usernameFilled && filled?.passwordFilled
        ? "Username and password filled. Review the page before submitting."
        : "Handle consumed, but one or more login fields were not found.",
      filled?.usernameFilled && filled?.passwordFilled ? "ok" : "warn",
    );
  } catch (error) {
    setStatus(
      recoveryMessage(error, "Fill failed; request a fresh handle and retry."),
      "error",
    );
  } finally {
    // The handle is transient even when bridge, origin, or field detection
    // fails. It is never persisted in local storage.
    handleInput.value = "";
    fillButton.disabled = !bridgeReady || !pageState.supported;
  }
});

void (async () => {
  await loadPreferences();
  await refreshState();
})();
