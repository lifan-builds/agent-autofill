// Runs in the selected document. Never returns field values to the popup.
function loginPage(action, expectedOrigin, username, password) {
  if (location.origin !== expectedOrigin) return {state: "origin_changed"};
  const usable = (element) => !element.disabled && !element.readOnly
    && element.getClientRects().length > 0
    && getComputedStyle(element).visibility !== "hidden";
  const passwords = [...document.querySelectorAll('input[type="password"]')].filter(usable);
  const usernames = [...document.querySelectorAll(
    'input[autocomplete="username"], input[type="email"], input[name*="user" i], input[id*="user" i]'
  )].filter(usable);
  if (passwords.length > 1 || usernames.length > 1) return {state: "ambiguous"};
  const pass = passwords[0];
  const user = usernames[0];
  if (pass?.autocomplete === "new-password") return {state: "new_password"};
  if (!pass && !user) return {state: "missing"};
  if (pass && user && pass.form !== user.form) return {state: "ambiguous"};
  const state = pass ? "password" : "username";
  if (action === "check") return {state};
  if (action === "fill" && !pass) return {state: "changed"};
  if (action === "username" && state !== "username") return {state: "changed"};
  const setValue = (element, value) => {
    if (!element || typeof value !== "string") return;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
  };
  if (user) setValue(user, username);
  if (user && (!user.isConnected || user.value !== username)) return {state: "changed"};
  // Input handlers can replace or hide the password field during username delivery.
  if (pass && (!pass.isConnected || !usable(pass))) return {state: "changed"};
  if (action === "fill") setValue(pass, password);
  if (pass && (!pass.isConnected || pass.value !== password)) return {state: "changed"};
  return {state: action === "username" ? "username_filled" : "filled"};
}
