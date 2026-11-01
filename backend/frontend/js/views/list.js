// The main screen's list: search, filters, and one row per receipt.

import { api } from "../core/api.js";
import { categoryLabel, fillCategorySelect } from "../core/categories.js";
import { formatDate, formatMoney } from "../core/format.js";
import { isSingular, t } from "../core/i18n.js";
import { go } from "../core/router.js";
import { getCurrentProfile, getProfiles } from "../core/session.js";
import { el, showStatus } from "../core/ui.js";

const list = document.getElementById("receipt-list");
const status = document.getElementById("list-status");
const fields = {
  q: document.getElementById("search-input"),
  scope: document.getElementById("filter-scope"),
  merchant: document.getElementById("filter-merchant"),
  category: document.getElementById("filter-category"),
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
};

fillCategorySelect(fields.category, t("All categories"));

function searchParams() {
  const params = new URLSearchParams();
  for (const key of ["q", "merchant", "category", "from", "to"]) {
    if (fields[key].value.trim()) params.set(key, fields[key].value.trim());
  }
  if (fields.scope.value === "mine") params.set("profile_id", getCurrentProfile().id);
  return params;
}

function isFiltered() {
  return ["q", "merchant", "category", "from", "to"].some((key) => fields[key].value.trim());
}

function row(receipt) {
  const owner = getProfiles().find((p) => p.id === receipt.profile_id);
  const meta = el("span", { class: "receipt-meta" }, formatDate(receipt.purchased_at));
  meta.append(" · ", categoryLabel(receipt.category));
  if (fields.scope.value === "all") {
    const dot = el("span", { class: "owner-dot", "aria-hidden": "true" });
    if (owner) dot.style.background = owner.color;
    meta.append(" · ", dot, receipt.profile_name);
  }
  const link = el(
    "a",
    { class: `receipt-row${receipt.needs_review ? " needs-review" : ""}`, href: `/receipt/${receipt.id}` },
    el("span", { class: "receipt-merchant" }, receipt.merchant || t("Unknown merchant")),
    el("span", { class: "receipt-total" }, formatMoney(receipt.total, receipt.currency)),
    meta,
  );
  if (receipt.needs_review) link.append(el("span", { class: "review-badge" }, t("To review")));
  link.addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return; // Let "open in a new tab" work.
    e.preventDefault();
    go(`/receipt/${receipt.id}`);
  });
  return el("li", {}, link);
}

let latest = 0; // A slow, older search must not overwrite a newer one's results.

export async function loadList() {
  const request = ++latest;
  if (!list.children.length) showStatus(status, t("Loading..."));
  try {
    const receipts = await api(`/api/receipts?${searchParams()}`);
    if (request !== latest) return;
    list.replaceChildren(...receipts.map(row));
    const n = receipts.length;
    if (n) showStatus(status, isSingular(n) ? t("{n} receipt", { n }) : t("{n} receipts", { n }));
    else if (isFiltered()) showStatus(status, t("No receipts match this search."));
    else showStatus(status, t("No receipts yet. Take a photo or upload a file to start."));
  } catch (err) {
    if (request === latest) showStatus(status, err.message, true);
  }
}

let typing;
function loadSoon() {
  clearTimeout(typing);
  typing = setTimeout(loadList, 250);
}

document.getElementById("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  clearTimeout(typing);
  loadList();
});
fields.q.addEventListener("input", loadSoon);
fields.merchant.addEventListener("input", loadSoon);
for (const key of ["scope", "category", "from", "to"]) fields[key].addEventListener("change", loadList);
document.getElementById("filter-clear").addEventListener("click", () => {
  for (const key of ["merchant", "category", "from", "to"]) fields[key].value = "";
  fields.scope.value = "mine";
  loadList();
});
