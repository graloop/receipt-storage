// The page's address: "/" is the list and "/receipt/5" one receipt, without
// reloading the page. main.js says what to do for each address.

let handler = () => {};

export function setRouteHandler(fn) {
  handler = fn;
  window.addEventListener("popstate", () => handler());
}

export function go(path) {
  if (location.pathname !== path) history.pushState({}, "", path);
  handler();
}

export function route() {
  handler();
}
