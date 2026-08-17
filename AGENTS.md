# UI localization rule

Every user-visible UI change must support Vietnamese and English in the same
change set.

- Do not add hard-coded one-language labels, hints, placeholders, titles,
  empty states, status messages, or button text.
- Use `localize(locale, vietnamese, english)` (or a local `t` wrapper) for
  component UI. Use the typed `translate` keys for shared application chrome.
- Add legacy/static strings that use `LocaleTextSync` to
  `frontend/src/app/ui.en.json` with a non-empty English value.
- Update `tests/i18n.catalog.test.mjs` whenever a new UI group needs a
  regression check, then run `npm run test:i18n` and `npm run build`.
- Data supplied by the backend must be localized by a stable identifier in the
  frontend; do not rely on a Vietnamese backend display string as a UI label.
