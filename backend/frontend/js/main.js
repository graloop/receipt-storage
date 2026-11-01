// The page's entry point (loaded by index.html). Each screen's code is in
// views/; what they share is in core/. This file only decides which screen an
// address opens.

import { translatePage } from "./core/i18n.js";
import { go, setRouteHandler } from "./core/router.js";
import { getCurrentProfile, loadProfiles } from "./core/session.js";
import { showStatus, showView } from "./core/ui.js";
import { openDetail } from "./views/detail.js";
import { loadList } from "./views/list.js";
import "./views/profiles.js";
import "./views/settings.js";
import "./views/upload.js";

translatePage();

function handleRoute() {
  // Nobody chosen yet: ask first. The address stays, so a /receipt/5 link still opens afterwards.
  if (!getCurrentProfile()) {
    showView("profile-view");
    return;
  }
  const match = location.pathname.match(/^\/receipt\/(\d+)$/);
  if (match) {
    openDetail(match[1]);
  } else {
    showView("list-view");
    loadList();
  }
}

document.getElementById("home-link").addEventListener("click", (e) => {
  e.preventDefault();
  go("/");
});

setRouteHandler(handleRoute);

try {
  await loadProfiles();
  handleRoute();
} catch (err) {
  showStatus(document.getElementById("app-error"), err.message, true);
}
