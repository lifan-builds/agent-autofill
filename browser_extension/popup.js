const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8766";
const agentInput = document.getElementById("agent");
const handleInput = document.getElementById("handle");
const bridgeInput = document.getElementById("bridge-url");
const originStatus = document.getElementById("origin");
const bridgeStatus = document.getElementById("bridge");
const status = document.getElementById("status");
const fillButton = document.getElementById("fill");
const refreshButton = document.getElementById("refresh");
const requestsInput = document.getElementById("requests");
const detail = document.getElementById("request-detail");
let pendingRequests = [];

let pageState = {tabId: null, origin: null, supported: false};
let bridgeReady = false;
let filling = false;

function selectedRequest() {
  return requestsInput.value === "" ? null : pendingRequests[Number(requestsInput.value)];
}

function updateFillButton() {
  const request = selectedRequest();
  const hasRequest = Boolean(request || (handleInput.value.trim() && agentInput.value.trim()));
  fillButton.disabled = filling || !bridgeReady || !pageState.supported || !hasRequest;
}

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
  updateFillButton();
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

async function pageCheck(tabId, origin, action = "check", username, password) {
  const result = await chrome.scripting.executeScript({
    target: {tabId}, func: loginPage, args: [action, origin, username ?? null, password ?? null],
  });
  return {state: result?.[0]?.result?.state, documentId: result?.[0]?.documentId};
}

async function refreshRequests() {
  pendingRequests = [];
  requestsInput.replaceChildren(new Option("Select an approved request", ""));
  detail.textContent = "";
  await refreshState();
  if (!bridgeReady || !pageState.supported) return;
  try {
    const {pairingToken} = await chrome.storage.session.get("pairingToken");
    if (!pairingToken) { detail.textContent = "Pair this browser session, or use a handle manually."; return; }
    const response = await fetchWithTimeout(`${normalizeBridgeUrl(bridgeInput.value)}/v1/pending`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({pairing_token: pairingToken, target_origin: pageState.origin}),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Could not load approved requests.");
    pendingRequests = body.requests || [];
    pendingRequests.forEach((request, index) => {
      requestsInput.add(new Option(`${request.label} · ${request.agent_id} · ${request.purpose}`, String(index)));
    });
    detail.textContent = pendingRequests.length ? "Choose the account and purpose you approved." : "No current requests for this site. Ask your agent to request extension delivery, then refresh.";
  } catch (error) { setStatus(recoveryMessage(error, "Refresh requests failed."), "error"); }
  finally { updateFillButton(); }
}

document.getElementById("refresh-requests").addEventListener("click", refreshRequests);
requestsInput.addEventListener("change", () => {
  const request = pendingRequests[Number(requestsInput.value)];
  detail.textContent = requestsInput.value !== "" && request
    ? `${request.target_origin} · ${request.purpose} · one use · expires ${new Date(request.expires_at).toLocaleTimeString()}` : "";
  updateFillButton();
});
document.getElementById("pair").addEventListener("click", async () => {
  const token = [...crypto.getRandomValues(new Uint8Array(32))].map(x => x.toString(16).padStart(2, "0")).join("");
  await chrome.storage.session.set({pairingToken: token});
  document.getElementById("pairing-code").value = token;
  document.getElementById("pair-command").textContent = `Run agent-autofill pair-browser --agent-id YOUR_AGENT_ID --extension-id ${chrome.runtime.id}, then paste the code into its hidden prompt.`;
});
document.getElementById("forget-pairing").addEventListener("click", async () => {
  await chrome.storage.session.remove("pairingToken");
  document.getElementById("pairing-code").value = "";
  pendingRequests = [];
  requestsInput.replaceChildren(new Option("Browser pairing forgotten", ""));
  detail.textContent = "";
  updateFillButton();
});

agentInput.addEventListener("input", () => { savePreferences(); updateFillButton(); });
handleInput.addEventListener("input", updateFillButton);
bridgeInput.addEventListener("change", () => {
  savePreferences();
  void refreshState();
});
refreshButton.addEventListener("click", () => void refreshState());

fillButton.addEventListener("click", async () => {
  if (filling) return;
  const selected = selectedRequest();
  const handle = selected?.handle || handleInput.value;
  const agentId = selected?.agent_id || agentInput.value.trim();
  let consumptionStarted = false;
  if (!handle || !agentId) {
    setStatus("Choose an approved request, or enter an agent ID and one-time handle under the manual option.", "warn");
    return;
  }
  savePreferences();
  filling = true;
  fillButton.disabled = true;
  setStatus("Checking the active origin before consuming the handle…");
  try {
    pageState = await activeTabState();
    renderOrigin();
    if (selected && selected.target_origin !== pageState.origin) {
      throw new Error("The selected request belongs to another site. Refresh requests. No handle was consumed.");
    }
    if (selected && (!Number.isFinite(Date.parse(selected.expires_at)) || Date.parse(selected.expires_at) <= Date.now())) {
      throw new Error("This request expired. Ask your agent for a new request, then refresh. No fields were filled.");
    }
    if (!pageState.supported) {
      throw new Error("Open the supported HTTPS page before consuming the handle.");
    }
    if (!(await checkBridge())) {
      throw new Error("The loopback bridge is unavailable; no handle was consumed.");
    }
    const bridgeUrl = normalizeBridgeUrl(bridgeInput.value);
    const checkedPage = {...pageState};
    const preflight = await pageCheck(checkedPage.tabId, checkedPage.origin);
    if (!preflight.documentId) {
      throw new Error("The browser could not identify this document. Update Chrome and retry. No handle was consumed.");
    }
    if (preflight.state === "username") {
      if (selected?.username) {
        const result = await chrome.scripting.executeScript({
          target: {tabId: checkedPage.tabId, documentIds: [preflight.documentId]},
          func: loginPage, args: ["username", checkedPage.origin, selected.username, null],
        });
        if (result?.[0]?.result?.state !== "username_filled") throw new Error("The page changed. Refresh before trying again.");
        setStatus("Username filled. Continue on the site, then refresh here for the password step. No handle was consumed.", "ok");
      } else {
        setStatus("Enter your username and continue on the site, then try the password step. No handle was consumed.", "warn");
      }
      return;
    }
    if (preflight.state !== "password") {
      throw new Error(`Login fields are ${preflight.state || "unavailable"}. Open a single existing-account login form and retry. No handle was consumed.`);
    }
    const currentPage = await activeTabState();
    if (currentPage.tabId !== checkedPage.tabId || currentPage.origin !== checkedPage.origin) {
      throw new Error("The active page changed. Refresh requests. No handle was consumed.");
    }
    consumptionStarted = true;
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
      target: {tabId: checkedPage.tabId, documentIds: [preflight.documentId]},
      func: loginPage,
      args: ["fill", checkedPage.origin, body.username, body.password],
    });
    const filled = result?.[0]?.result;
    setStatus(
      filled?.state === "filled"
        ? "Login filled. Review the page before submitting."
        : "The page changed after delivery. Request a fresh handoff and retry; nothing was submitted.",
      filled?.state === "filled" ? "ok" : "warn",
    );
  } catch (error) {
    setStatus(
      recoveryMessage(error, "Fill failed; request a fresh handle and retry."),
      "error",
    );
  } finally {
    // Keep an unused handle in this popup for recoverable preflight failures only.
    if (consumptionStarted) {
      handleInput.value = "";
      pendingRequests = [];
      requestsInput.replaceChildren(new Option("Refresh for a new request", ""));
      detail.textContent = "";
    }
    filling = false;
    updateFillButton();
  }
});

void (async () => {
  await loadPreferences();
  await refreshRequests();
})();
