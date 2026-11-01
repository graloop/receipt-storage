// The categories: stored as English keys, shown in the page's language.

import { t } from "./i18n.js";
import { el } from "./ui.js";

const LABELS = {
  groceries: "Groceries",
  restaurant: "Restaurant",
  household: "Household",
  electronics: "Electronics",
  clothing: "Clothing",
  health: "Health",
  transport: "Transport",
  entertainment: "Entertainment",
  services: "Services",
  other: "Other",
};

export function categoryLabel(key) {
  return t(LABELS[key] ?? "Other");
}

// Fills a <select>; `first` adds a choice with an empty value on top.
export function fillCategorySelect(select, first = null) {
  if (first) select.append(el("option", { value: "" }, first));
  for (const key of Object.keys(LABELS)) select.append(el("option", { value: key }, categoryLabel(key)));
}
