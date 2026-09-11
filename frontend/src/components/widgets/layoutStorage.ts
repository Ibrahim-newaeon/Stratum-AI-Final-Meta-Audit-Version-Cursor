import { availableWidgets, defaultWidgets, WidgetConfig, WidgetType } from './index';

export const DASHBOARD_LAYOUT_KEY = 'stratum-dashboard-layout';

const KNOWN_WIDGET_TYPES = new Set<string>(availableWidgets.map((widget) => widget.type));

/** react-grid-layout accepts `Infinity` for y ("place at the bottom"), JSON does not. */
function finiteOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function optionalFinite(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/**
 * Turn one persisted entry into a usable widget, or `null` when it cannot be
 * trusted. A layout saved before a widget type was renamed or removed would
 * otherwise render an "Unknown widget" panel forever.
 */
export function sanitizeWidget(raw: unknown): WidgetConfig | null {
  if (typeof raw !== 'object' || raw === null) {
    return null;
  }
  const entry = raw as Record<string, unknown>;

  if (typeof entry.id !== 'string' || entry.id === '') {
    return null;
  }
  if (typeof entry.type !== 'string' || !KNOWN_WIDGET_TYPES.has(entry.type)) {
    return null;
  }

  const definition = availableWidgets.find((widget) => widget.type === entry.type);
  const title = typeof entry.title === 'string' && entry.title !== '' ? entry.title : undefined;

  return {
    id: entry.id,
    type: entry.type as WidgetType,
    title: title ?? definition?.title ?? entry.type,
    x: finiteOr(entry.x, 0),
    y: finiteOr(entry.y, 0),
    w: finiteOr(entry.w, definition?.defaultSize.w ?? 3),
    h: finiteOr(entry.h, definition?.defaultSize.h ?? 2),
    minW: optionalFinite(entry.minW),
    minH: optionalFinite(entry.minH),
    maxW: optionalFinite(entry.maxW),
    maxH: optionalFinite(entry.maxH),
  };
}

/**
 * Read the saved layout. Any failure - unavailable storage, malformed JSON, a
 * layout that no longer names a real widget - falls back to the defaults rather
 * than throwing out of a `useState` initialiser and blanking the whole route.
 */
export function loadSavedWidgets(): WidgetConfig[] {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(DASHBOARD_LAYOUT_KEY);
  } catch {
    return defaultWidgets;
  }
  if (!saved) {
    return defaultWidgets;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(saved);
  } catch {
    return defaultWidgets;
  }
  if (!Array.isArray(parsed)) {
    return defaultWidgets;
  }

  const widgets = parsed
    .map(sanitizeWidget)
    .filter((widget): widget is WidgetConfig => widget !== null);

  return widgets.length > 0 ? widgets : defaultWidgets;
}

/** Persist the layout, normalising the non-finite `y` of a freshly added widget. */
export function saveWidgets(widgets: WidgetConfig[]): void {
  const serialisable = widgets.map((widget, index) => ({
    ...widget,
    x: finiteOr(widget.x, 0),
    y: finiteOr(widget.y, index),
  }));
  try {
    localStorage.setItem(DASHBOARD_LAYOUT_KEY, JSON.stringify(serialisable));
  } catch {
    // A full or disabled store must not break the editor.
  }
}

export function clearSavedWidgets(): void {
  try {
    localStorage.removeItem(DASHBOARD_LAYOUT_KEY);
  } catch {
    // Nothing to recover from.
  }
}
