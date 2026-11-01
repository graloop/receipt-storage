// Who is using this browser: the list of profiles and the chosen one, remembered
// in localStorage. There's no login: choosing a profile is all it takes.

import { api } from "./api.js";

const PROFILE_KEY = "rv_profile";

let profiles = [];
const listeners = [];

export function getProfiles() {
  return profiles;
}

function savedId() {
  try {
    return Number(localStorage.getItem(PROFILE_KEY));
  } catch (_) {
    return 0;
  }
}

// The chosen profile, or null if none is chosen or it was removed since.
export function getCurrentProfile() {
  return profiles.find((profile) => profile.id === savedId()) ?? null;
}

export function setCurrentProfile(id) {
  try {
    localStorage.setItem(PROFILE_KEY, String(id));
  } catch (_) {
    // Private browsing: the choice lasts until the page is reloaded.
  }
  notify();
}

export async function loadProfiles() {
  profiles = await api("/api/profiles");
  notify();
  return profiles;
}

// `listener` runs whenever the list or the chosen profile changes.
export function onProfilesChange(listener) {
  listeners.push(listener);
}

function notify() {
  for (const listener of listeners) listener();
}
