// One receipt: the original image or PDF, and its fields to correct.

import { api, json } from "../core/api.js";
import { fillCategorySelect } from "../core/categories.js";
import { formatDate } from "../core/format.js";
import { t } from "../core/i18n.js";
import { go } from "../core/router.js";
import { canAutoRead, getProfiles } from "../core/session.js";
import { el, hideStatus, showStatus, showView } from "../core/ui.js";
import { listAddress } from "./list.js";

const content = document.getElementById("detail-content");
const errorLine = document.getElementById("detail-error");
const status = document.getElementById("detail-status");
const itemRows = document.getElementById("field-items");
const readButton = document.getElementById("detail-read");
const saveNextButton = document.getElementById("detail-save-next");
const form = document.getElementById("detail-form");
const actions = document.getElementById("detail-actions");
const deleteConfirm = document.getElementById("delete-confirm");
const deleteYes = document.getElementById("delete-yes");
const fields = {
  merchant: document.getElementById("field-merchant"),
  purchased_at: document.getElementById("field-date"),
  category: document.getElementById("field-category"),
  total: document.getElementById("field-total"),
  currency: document.getElementById("field-currency"),
  profile_id: document.getElementById("field-profile"),
  raw_text: document.getElementById("field-raw-text"),
};

fillCategorySelect(fields.category);

let current = null;
let latest = 0; // Going from one receipt to another: only the last one opened is shown.

function addItemRow(item = { name: "", qty: null, price: null }) {
  const name = el("input", { type: "text", placeholder: t("Item name"), "aria-label": t("Item name") });
  name.value = item.name;
  const qty = el("input", { type: "number", step: "any", placeholder: t("Qty"), "aria-label": t("Qty") });
  qty.value = item.qty ?? "";
  const price = el("input", { type: "number", step: "0.01", placeholder: t("Price"), "aria-label": t("Price") });
  price.value = item.price ?? "";
  const remove = el(
    "button",
    {
      type: "button",
      class: "btn-ghost item-remove",
      title: t("Remove this item"),
      "aria-label": t("Remove this item"),
    },
    "×",
  );
  const row = el("div", { class: "item-row" }, name, qty, price, remove);
  remove.addEventListener("click", () => row.remove());
  itemRows.append(row);
}

function readItems() {
  const numberOrNull = (input) => (input.value === "" ? null : Number(input.value));
  return Array.from(itemRows.querySelectorAll(".item-row"))
    .map((row) => {
      const [name, qty, price] = row.querySelectorAll("input");
      return { name: name.value.trim(), qty: numberOrNull(qty), price: numberOrNull(price) };
    })
    .filter((item) => item.name);
}

function showFile(receipt) {
  const box = document.getElementById("detail-file");
  const link = document.getElementById("detail-file-link");
  const url = `/api/receipts/${receipt.id}/image`;
  const label = t("Receipt from {merchant}", { merchant: receipt.merchant || t("Unknown merchant") });
  box.replaceChildren();
  if (receipt.file_type === "image") box.append(el("img", { class: "detail-image", src: url, alt: label }));
  if (receipt.file_type === "pdf") box.append(el("iframe", { class: "detail-pdf", src: url, title: label }));
  link.classList.toggle("hidden", !receipt.file_type);
  link.href = url;
  const noFile = document.getElementById("detail-no-file");
  noFile.classList.toggle("hidden", Boolean(receipt.file_type));
  noFile.textContent =
    receipt.source === "email" ? t("This receipt came by email. Its text is below.") : t("This receipt has no file.");
}

// Delete asks on the page, not in a browser pop-up: the receipt stays in view, and
// a pop-up would also stop the Claude extension, which can't answer one.
function askBeforeDeleting(asking) {
  deleteConfirm.classList.toggle("hidden", !asking);
  actions.classList.toggle("hidden", asking);
}

function fill(receipt) {
  current = receipt;
  askBeforeDeleting(false);
  fields.profile_id.replaceChildren(...getProfiles().map((p) => el("option", { value: p.id }, p.name)));
  fields.merchant.value = receipt.merchant ?? "";
  fields.purchased_at.value = receipt.purchased_at ?? "";
  fields.category.value = receipt.category;
  fields.total.value = receipt.total ?? "";
  fields.currency.value = receipt.currency;
  fields.profile_id.value = receipt.profile_id;
  fields.raw_text.value = receipt.raw_text ?? "";
  itemRows.replaceChildren();
  receipt.items.forEach(addItemRow);
  document.getElementById("detail-review").classList.toggle("hidden", !receipt.needs_review || receipt.unread);
  const unreadNote = document.getElementById("detail-unread");
  unreadNote.classList.toggle("hidden", !receipt.unread);
  unreadNote.textContent =
    receipt.source === "email"
      ? t(
          'This receipt came by email and hasn\'t been read yet. Its text is under "Text read from the receipt": keep it there, and copy the merchant, date, total and items into the fields.',
        )
      : t(
          'This receipt hasn\'t been read yet. Copy what the original shows into the fields, including all its text under "Text read from the receipt".',
        );
  readButton.classList.toggle("hidden", !receipt.unread || !canAutoRead());
  saveNextButton.classList.toggle("hidden", !receipt.unread);
  // Open when the text is what's being asked for: a closed section is easy to miss.
  document.querySelector(".raw-text").open = receipt.unread;
  // The tab's name: tells apart several open receipts, for a person or for the Claude extension.
  document.title = `${receipt.merchant || t("Unknown merchant")}, ${formatDate(receipt.purchased_at)} - Receipt Vault`;
}

export async function openDetail(id) {
  const request = ++latest;
  showView("detail-view");
  content.classList.add("hidden");
  hideStatus(errorLine);
  hideStatus(status);
  try {
    const receipt = await api(`/api/receipts/${id}`);
    if (request !== latest) return;
    fill(receipt);
    showFile(receipt);
    content.classList.remove("hidden");
    window.scrollTo(0, 0);
  } catch (err) {
    if (request === latest) showStatus(errorLine, err.message, true);
  }
}

document.getElementById("detail-back").addEventListener("click", () => go(listAddress()));
readButton.addEventListener("click", async () => {
  showStatus(status, t("Reading the receipt..."));
  readButton.disabled = true;
  try {
    fill(await api(`/api/receipts/${current.id}/read`, { method: "POST" }));
    hideStatus(status);
  } catch (err) {
    showStatus(status, err.message, true);
  } finally {
    readButton.disabled = false;
  }
});
document.getElementById("add-item").addEventListener("click", () => addItemRow());

// Answers whether it worked; if not, the reason is on the page.
async function save() {
  hideStatus(status);
  try {
    await api(
      `/api/receipts/${current.id}`,
      json("PATCH", {
        merchant: fields.merchant.value.trim() || null,
        purchased_at: fields.purchased_at.value || null,
        category: fields.category.value,
        total: fields.total.value === "" ? null : Number(fields.total.value),
        currency: fields.currency.value,
        profile_id: Number(fields.profile_id.value),
        raw_text: fields.raw_text.value,
        items: readItems(),
        needs_review: false, // Someone just looked at it.
      }),
    );
    return true;
  } catch (err) {
    showStatus(status, err.message, true);
    return false;
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (await save()) go(listAddress());
});

saveNextButton.addEventListener("click", async () => {
  if (!form.reportValidity() || !(await save())) return;
  try {
    const [next] = await api("/api/receipts?unread=1");
    go(next ? `/receipt/${next.id}` : "/?unread=1&scope=all");
  } catch (err) {
    showStatus(status, err.message, true);
  }
});

document.getElementById("detail-delete").addEventListener("click", () => {
  hideStatus(status);
  askBeforeDeleting(true);
  document.getElementById("delete-cancel").focus(); // The safe answer comes first.
});
document.getElementById("delete-cancel").addEventListener("click", () => askBeforeDeleting(false));
deleteYes.addEventListener("click", async () => {
  deleteYes.disabled = true; // A second tap would ask to delete it twice.
  try {
    await api(`/api/receipts/${current.id}`, { method: "DELETE" });
    go(listAddress());
  } catch (err) {
    askBeforeDeleting(false);
    showStatus(status, err.message, true);
  } finally {
    deleteYes.disabled = false;
  }
});
