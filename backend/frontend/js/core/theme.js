// Night mode: dark colours for the whole page, from the switch in the top bar.
// Until the switch is used, it follows the phone's or computer's own setting;
// then this browser remembers the choice. index.html sets it before the page is
// drawn, so this only keeps the switch in step and changes it.

const KEY = "rv_theme";
const button = document.getElementById("theme-switch");

function saved() {
  try {
    return localStorage.getItem(KEY);
  } catch (_) {
    return null;
  }
}

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  button.setAttribute("aria-checked", String(theme === "dark"));
}

apply(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

button.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  apply(next);
  try {
    localStorage.setItem(KEY, next);
  } catch (_) {
    // Private browsing: the choice lasts until the page is reloaded.
  }
});

// No choice made yet: follow the device when it changes by itself, at sunset for instance.
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  if (!saved()) apply(e.matches ? "dark" : "light");
});
