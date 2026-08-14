/**
 * Narrow compatibility boundary around pi-tui's fast-moving 0.x API.
 * Domain code imports this module rather than pi-tui directly.
 */
export {
  CombinedAutocompleteProvider,
  Editor,
  hyperlink,
  Key,
  ProcessTerminal,
  ScrollView,
  TuiAltScreen,
  VStack,
  matchesKey,
  truncateToWidth,
  visibleWidth,
  wrapTextWithAnsi,
} from "@earendil-works/pi-tui";

export type {
  AutocompleteItem,
  AutocompleteProvider,
  AutocompleteSuggestions,
  Component,
  EditorTheme,
  Focusable,
  SlashCommand,
  Terminal,
  TUI,
} from "@earendil-works/pi-tui";

export const AUTOCTX_EDITOR_THEME = {
  borderColor: (text: string) => text,
  selectList: {
    selectedPrefix: (text: string) => text,
    selectedText: (text: string) => text,
    description: (text: string) => text,
    scrollInfo: (text: string) => text,
    noMatch: (text: string) => text,
  },
} as const;
