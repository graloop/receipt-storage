// Small page helpers every view uses: which screen is shown, status lines, and
// building elements. Text always goes in as text, never as HTML, so what a
// receipt says can't turn into code on the page.

import { closeMenus } from "./menus.js";

const VIEWS = ["profile-view", "list-view", "detail-view"];

export function showView(id) {
  // Refreshing the same screen keeps a menu open: renaming in Settings shouldn't close Settings.
  if (document.body.dataset.view !== id) closeMenus();
  for (const view of VIEWS) document.getElementById(view).classList.toggle("hidden", view !== id);
  document.body.dataset.view = id;
}

export function showStatus(element, text, isError = false) {
  element.textContent = text;
  element.classList.remove("hidden", "ok");
  element.classList.toggle("error", isError);
  element.classList.toggle("status", !isError);
}

export function hideStatus(element) {
  element.classList.add("hidden");
}

// el("a", { class: "x", href: "/" }, "text", otherElement)
export function el(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value !== null && value !== undefined) element.setAttribute(name, value);
  }
  element.append(...children);
  return element;
}

// The round badge with a profile's colour and first letter.
export function avatarFor(profile, element = el("span", { class: "avatar", "aria-hidden": "true" })) {
  element.textContent = Array.from(profile.name)[0]?.toUpperCase() ?? "";
  element.style.background = profile.color;
  return element;
}
