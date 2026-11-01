// "Who's this?" on the first visit, and the badge in the top bar to switch profile.

import { api, json } from "../core/api.js";
import { t } from "../core/i18n.js";
import { closeMenus, registerMenu } from "../core/menus.js";
import { route } from "../core/router.js";
import { getCurrentProfile, getProfiles, loadProfiles, onProfilesChange, setCurrentProfile } from "../core/session.js";
import { avatarFor, el, hideStatus, showStatus } from "../core/ui.js";

const grid = document.getElementById("profile-grid");
const switchList = document.getElementById("profile-switch-list");

function choose(id) {
  closeMenus();
  setCurrentProfile(id);
  route();
}

function renderGrid() {
  const profiles = getProfiles();
  grid.replaceChildren(
    ...profiles.map((profile) => {
      const card = el("button", { type: "button", class: "profile-card" }, avatarFor(profile), profile.name);
      card.addEventListener("click", () => choose(profile.id));
      return card;
    }),
  );
  grid.classList.toggle("empty", !profiles.length);
  if (!profiles.length) grid.append(el("p", { class: "status" }, t("No profiles yet. Add the first one.")));
}

function renderBadge() {
  const current = getCurrentProfile();
  document.getElementById("profile-menu").classList.toggle("hidden", !current);
  if (!current) return;
  document.getElementById("profile-name").textContent = current.name;
  // On a phone the name is hidden: the button still has to say what it is.
  document.getElementById("profile-btn").setAttribute("aria-label", `${t("Switch profile")}: ${current.name}`);
  avatarFor(current, document.getElementById("profile-avatar"));
  switchList.replaceChildren(
    ...getProfiles().map((profile) => {
      const row = el("button", { type: "button", class: "profile-switch" }, avatarFor(profile), profile.name);
      if (profile.id === current.id) row.setAttribute("aria-current", "true");
      row.addEventListener("click", () => choose(profile.id));
      return row;
    }),
  );
}

onProfilesChange(() => {
  renderGrid();
  renderBadge();
});
registerMenu("profile-btn", "profile-panel");

document.getElementById("add-profile").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("new-profile-name");
  const status = document.getElementById("add-profile-status");
  hideStatus(status);
  try {
    const profile = await api("/api/profiles", json("POST", { name: input.value }));
    input.value = "";
    await loadProfiles();
    choose(profile.id);
  } catch (err) {
    showStatus(status, err.message, true);
  }
});
