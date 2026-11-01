// Dates and amounts, in the page's language.

import { locale, t } from "./i18n.js";

// "2026-03-14" is a calendar day. new Date("2026-03-14") would read it as UTC,
// which is still the 13th in Montreal, so it's built from its parts instead.
export function formatDate(isoDate) {
  if (!isoDate) return t("No date");
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(locale(), {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatMoney(amount, currency) {
  if (amount === null || amount === undefined) return t("No total");
  try {
    return new Intl.NumberFormat(locale(), { style: "currency", currency }).format(amount);
  } catch (_) {
    return `${amount.toFixed(2)} ${currency}`;
  }
}
