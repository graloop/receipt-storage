// The settings gear: the language, and adding, renaming and removing profiles.

import { api, json } from "../core/api.js";
import { getLang, isSingular, setLang, t } from "../core/i18n.js";
import { registerMenu } from "../core/menus.js";
import { route } from "../core/router.js";
import { getProfiles, loadProfiles, onProfilesChange } from "../core/session.js";
import { avatarFor, el, hideStatus, showStatus } from "../core/ui.js";

const list = document.getElementById("settings-profiles");
const status = document.getElementById("settings-status");

for (const button of document.querySelectorAll("[data-lang]")) {
  button.setAttribute("aria-pressed", String(button.dataset.lang === getLang()));
  button.addEventListener("click", () => {
    if (button.dataset.lang !== getLang()) setLang(button.dataset.lang);
  });
}

async function rename(profile, input) {
  const name = input.value.trim();
  if (!name || name === profile.name) {
    input.value = profile.name;
    return;
  }
  hideStatus(status);
  try {
    await api(`/api/profiles/${profile.id}`, json("PATCH", { name }));
    await loadProfiles();
    route(); // The list shows owners' names.
  } catch (err) {
    input.value = profile.name;
    showStatus(status, err.message, true);
  }
}

function removalQuestion(profile) {
  const n = profile.receipt_count;
  if (n === 0) return t('Remove the profile "{name}"?', { name: profile.name });
  return isSingular(n)
    ? t('Remove the profile "{name}" and its receipt? This can\'t be undone.', { name: profile.name })
    : t('Remove the profile "{name}" and its {n} receipts? This can\'t be undone.', { name: profile.name, n });
}

async function remove(profile) {
  hideStatus(status);
  // Counted again here: another phone may have added receipts since the page loaded.
  await loadProfiles();
  const fresh = getProfiles().find((p) => p.id === profile.id);
  if (!fresh || !confirm(removalQuestion(fresh))) return;
  try {
    await api(`/api/profiles/${fresh.id}?delete_receipts=true`, { method: "DELETE" });
    await loadProfiles();
    route();
  } catch (err) {
    showStatus(status, err.message, true);
  }
}

function render() {
  list.replaceChildren(
    ...getProfiles().map((profile) => {
      const input = el("input", {
        type: "text",
        maxlength: "40",
        value: profile.name,
        "aria-label": t("{name}: rename", { name: profile.name }),
        title: t("Rename this profile"),
      });
      input.addEventListener("change", () => rename(profile, input));
      const removeBtn = el(
        "button",
        { type: "button", class: "btn-ghost item-remove", title: t("Remove this profile") },
        "×",
      );
      removeBtn.setAttribute("aria-label", t("Remove this profile"));
      removeBtn.addEventListener("click", () => remove(profile).catch((err) => showStatus(status, err.message, true)));
      return el("li", { class: "settings-profile" }, avatarFor(profile), input, removeBtn);
    }),
  );
}

onProfilesChange(render);
registerMenu("settings-btn", "settings-panel", () => hideStatus(status));

document.getElementById("settings-add-profile").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("settings-new-name");
  hideStatus(status);
  try {
    await api("/api/profiles", json("POST", { name: input.value }));
    input.value = "";
    await loadProfiles();
  } catch (err) {
    showStatus(status, err.message, true);
  }
});
