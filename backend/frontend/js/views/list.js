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
const everyone = document.getElementById("search-everyone");
const unreadOnly = document.getElementById("filter-unread");
const banner = document.getElementById("to-read-banner");
const hint = document.getElementById("to-read-hint");
const fields = {
  q: document.getElementById("search-input"),
  scope: document.getElementById("filter-scope"),
  merchant: document.getElementById("filter-merchant"),
  category: document.getElementById("filter-category"),
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
};

fillCategorySelect(fields.category, t("All categories"));

const FILTERS = ["q", "merchant", "category", "from", "to"];

function filterParams() {
  const params = new URLSearchParams();
  for (const key of FILTERS) {
    if (fields[key].value.trim()) params.set(key, fields[key].value.trim());
  }
  if (unreadOnly.checked) params.set("unread", "1");
  return params;
}

function searchParams() {
  const params = filterParams();
  if (fields.scope.value === "mine") params.set("profile_id", getCurrentProfile().id);
  return params;
}

// The address that reopens this search: /?q=headphones&scope=all
export function listAddress() {
  const params = filterParams();
  if (fields.scope.value === "all") params.set("scope", "all");
  const query = params.toString();
  return query ? `/?${query}` : "/";
}

// The other way around: an address opens its search, so a search can be linked to or reopened with Back.
export function readAddress() {
  const params = new URLSearchParams(location.search);
  for (const key of FILTERS) fields[key].value = params.get(key) ?? "";
  fields.scope.value = params.get("scope") === "all" ? "all" : "mine";
  unreadOnly.checked = params.get("unread") === "1";
  if (FILTERS.some((key) => key !== "q" && params.get(key)) || params.get("scope") === "all") {
    document.querySelector(".filters").open = true;
  }
}

function isFiltered() {
  return unreadOnly.checked || FILTERS.some((key) => fields[key].value.trim());
}

function dateLabel(receipt) {
  return receipt.purchased_at
    ? formatDate(receipt.purchased_at)
    : t("Added {date}", { date: formatDate(receipt.added_on) });
}

function itemsPreview(receipt) {
  const names = receipt.items.map((item) => item.name);
  return names.slice(0, 3).join(", ") + (names.length > 3 ? "..." : "");
}

function row(receipt) {
  const owner = getProfiles().find((p) => p.id === receipt.profile_id);
  const meta = el("span", { class: "receipt-meta" }, dateLabel(receipt));
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
  if (receipt.needs_review) {
    link.append(el("span", { class: "review-badge" }, receipt.unread ? t("To read") : t("To review")));
  }
  if (receipt.items.length) link.append(el("span", { class: "receipt-items" }, itemsPreview(receipt)));
  link.addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return; // Let "open in a new tab" work.
    e.preventDefault();
    go(`/receipt/${receipt.id}`);
  });
  return el("li", {}, link);
}

// Counts everyone's: one person can read the whole household's receipts in one go.
async function updateBanner() {
  const n = (await api("/api/receipts?unread=1")).length;
  banner.textContent = isSingular(n) ? t("{n} receipt to read", { n }) : t("{n} receipts to read", { n });
  banner.classList.toggle("hidden", n === 0 || unreadOnly.checked);
}

let latest = 0; // A slow, older search must not overwrite a newer one's results.

export async function loadList() {
  const request = ++latest;
  // On this screen only: an upload finishing after a tap on a receipt mustn't change that address.
  if (location.pathname === "/") history.replaceState({}, "", listAddress());
  everyone.classList.add("hidden");
  hint.classList.toggle("hidden", !unreadOnly.checked);
  if (!list.children.length) showStatus(status, t("Loading..."));
  try {
    const [receipts] = await Promise.all([api(`/api/receipts?${searchParams()}`), updateBanner()]);
    if (request !== latest) return;
    list.replaceChildren(...receipts.map(row));
    const n = receipts.length;
    if (n) showStatus(status, isSingular(n) ? t("{n} receipt", { n }) : t("{n} receipts", { n }));
    else if (unreadOnly.checked) {
      showStatus(status, t("Nothing left to read."));
      hint.classList.add("hidden");
    } else if (isFiltered()) {
      showStatus(status, t("No receipts match this search."));
      everyone.classList.toggle("hidden", fields.scope.value === "all");
    } else showStatus(status, t("No receipts yet. Take a photo or upload a file to start."));
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
unreadOnly.addEventListener("change", loadList);
banner.addEventListener("click", (e) => {
  e.preventDefault();
  go("/?unread=1&scope=all");
});
everyone.addEventListener("click", () => {
  fields.scope.value = "all";
  document.querySelector(".filters").open = true;
  loadList();
});
document.getElementById("filter-clear").addEventListener("click", () => {
  for (const key of ["merchant", "category", "from", "to"]) fields[key].value = "";
  fields.scope.value = "mine";
  unreadOnly.checked = false;
  loadList();
});
