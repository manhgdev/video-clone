/**
 * Project session helpers — localStorage keys & idle status.
 * Full poll/upload logic still lives in app/App.tsx; extract incrementally.
 */
export {
  SETTINGS_LS,
  SESSION_LS,
  SIDEBAR_W_LS,
  THEME_LS,
  loadSettings,
  persistSettings,
  persistSession,
  loadTheme,
  loadSidebarWidth,
  defaultSettings,
  applyEngineProfile,
  snapshotEngineProfile,
} from './appSettings'
