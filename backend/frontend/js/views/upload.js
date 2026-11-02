// The two add buttons. Files go up one at a time so each row can say how it's
// going: the model takes a few seconds per receipt.

import { api } from "../core/api.js";
import { formatMoney } from "../core/format.js";
import { t } from "../core/i18n.js";
import { getCurrentProfile } from "../core/session.js";
import { el } from "../core/ui.js";
import { loadList } from "./list.js";

const progress = document.getElementById("upload-progress");

function summary(receipt) {
  return `${receipt.merchant || t("Unknown merchant")}, ${formatMoney(receipt.total, receipt.currency)}`;
}

async function uploadOne(file, source, state, row) {
  state.textContent = t("Reading the receipt...");
  const form = new FormData();
  form.append("profile_id", getCurrentProfile().id);
  form.append("source", source);
  form.append("files", file);
  try {
    const { results } = await api("/api/receipts/upload", { method: "POST", body: form });
    const result = results[0];
    if (result.status === "error") {
      row.classList.add("failed");
      state.textContent = t(result.message);
    } else if (result.message) {
      row.classList.add("review");
      state.textContent = t("Saved, but not read yet. {reason}", { reason: t(result.message) });
    } else if (result.status === "duplicate") {
      state.textContent = t("Already saved: {summary}", { summary: summary(result.receipt) });
    } else if (result.receipt.unread) {
      row.classList.add("added");
      state.textContent = t("Saved, will be read later");
    } else if (result.receipt.needs_review) {
      row.classList.add("review");
      state.textContent = t("Added, please review: {summary}", { summary: summary(result.receipt) });
    } else {
      row.classList.add("added");
      state.textContent = t("Added: {summary}", { summary: summary(result.receipt) });
    }
  } catch (err) {
    row.classList.add("failed");
    state.textContent = err.message;
  }
}

async function uploadAll(files, source) {
  progress.classList.remove("hidden");
  progress.replaceChildren();
  const rows = files.map((file) => {
    const state = el("span", { class: "upload-state" }, t("Waiting..."));
    const row = el("li", { class: "upload-row" }, el("span", { class: "upload-name" }, file.name), state);
    progress.append(row);
    return { file, state, row };
  });
  for (const { file, state, row } of rows) {
    await uploadOne(file, source, state, row);
    await loadList();
  }
}

function watch(inputId, source) {
  const input = document.getElementById(inputId);
  input.addEventListener("change", () => {
    const files = Array.from(input.files);
    input.value = ""; // So picking the same file again still counts as a change.
    if (files.length) uploadAll(files, source);
  });
}

watch("camera-input", "photo");
watch("upload-input", "screenshot"); // The server records a PDF as a PDF, whatever this says.
