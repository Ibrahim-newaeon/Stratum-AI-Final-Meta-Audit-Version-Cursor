import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defaultWidgets } from './index';
import {
  clearSavedWidgets,
  DASHBOARD_LAYOUT_KEY,
  loadSavedWidgets,
  sanitizeWidget,
  saveWidgets,
} from './layoutStorage';

describe('loadSavedWidgets', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('returns the defaults when nothing is stored', () => {
    expect(loadSavedWidgets()).toEqual(defaultWidgets);
  });

  it('returns the defaults instead of throwing on malformed JSON', () => {
    localStorage.setItem(DASHBOARD_LAYOUT_KEY, '{not json');
    expect(loadSavedWidgets()).toEqual(defaultWidgets);
  });

  it('returns the defaults when the stored value is not an array', () => {
    localStorage.setItem(DASHBOARD_LAYOUT_KEY, JSON.stringify({ id: 'kpi-spend' }));
    expect(loadSavedWidgets()).toEqual(defaultWidgets);
  });

  it('returns the defaults when storage itself throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    expect(loadSavedWidgets()).toEqual(defaultWidgets);
  });

  it('drops an entry naming a widget type that no longer exists', () => {
    localStorage.setItem(
      DASHBOARD_LAYOUT_KEY,
      JSON.stringify([
        {
          id: 'gone',
          type: 'retired-widget',
          title: 'Gone',
          x: 0,
          y: 0,
          w: 3,
          h: 2,
        },
        {
          id: 'kpi-roas',
          type: 'kpi-roas',
          title: 'ROAS',
          x: 0,
          y: 0,
          w: 3,
          h: 2,
        },
      ]),
    );
    expect(loadSavedWidgets()).toEqual([
      expect.objectContaining({ id: 'kpi-roas', type: 'kpi-roas' }),
    ]);
  });

  it('returns the defaults when no stored entry survives validation', () => {
    localStorage.setItem(
      DASHBOARD_LAYOUT_KEY,
      JSON.stringify([{ id: 'x', type: 'retired' }]),
    );
    expect(loadSavedWidgets()).toEqual(defaultWidgets);
  });

  it('repairs a null y left behind by a serialised Infinity', () => {
    localStorage.setItem(
      DASHBOARD_LAYOUT_KEY,
      JSON.stringify([
        {
          id: 'alerts-1',
          type: 'alerts',
          title: 'Alerts',
          x: 0,
          y: null,
          w: 4,
          h: 3,
        },
      ]),
    );
    expect(loadSavedWidgets()[0].y).toBe(0);
  });

  it('round-trips every default widget', () => {
    saveWidgets(defaultWidgets);
    expect(loadSavedWidgets().map((w) => w.type)).toEqual(
      defaultWidgets.map((w) => w.type),
    );
  });
});

describe('sanitizeWidget', () => {
  it('rejects a non-object', () => {
    expect(sanitizeWidget(null)).toBeNull();
    expect(sanitizeWidget('kpi-spend')).toBeNull();
  });

  it('rejects an entry with no id', () => {
    expect(sanitizeWidget({ type: 'alerts' })).toBeNull();
  });

  it('falls back to the catalogue title when none is stored', () => {
    expect(sanitizeWidget({ id: 'a', type: 'alerts' })?.title).toBe('Alerts');
  });

  it('falls back to the catalogue size when the stored geometry is unusable', () => {
    const widget = sanitizeWidget({
      id: 'a',
      type: 'alerts',
      w: 'wide',
      h: NaN,
    });
    expect(widget).toMatchObject({ w: 4, h: 3 });
  });
});

describe('saveWidgets', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('normalises the Infinity y of a freshly added widget', () => {
    saveWidgets([
      {
        id: 'alerts-1',
        type: 'alerts',
        title: 'Alerts',
        x: 0,
        y: Infinity,
        w: 4,
        h: 3,
      },
    ]);
    const stored = JSON.parse(
      localStorage.getItem(DASHBOARD_LAYOUT_KEY) as string,
    );
    expect(stored[0].y).toBe(0);
  });

  it('does not throw when the store is full', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(() => saveWidgets(defaultWidgets)).not.toThrow();
  });
});

describe('clearSavedWidgets', () => {
  it('removes the stored layout', () => {
    saveWidgets(defaultWidgets);
    clearSavedWidgets();
    expect(localStorage.getItem(DASHBOARD_LAYOUT_KEY)).toBeNull();
  });
});
