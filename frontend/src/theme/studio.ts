/**
 * Stratum Studio design tokens
 * Light: Pearl & Indigo | Dark: Navy & Periwinkle
 * Reference: docs/design/stratum-studio-*.png
 */

export const studioLight = {
  bg: '#F6F7FB',
  sidebar: '#FFFFFF',
  card: '#FFFFFF',
  border: '#DDE2EC',
  text: '#182235',
  textSecondary: '#526078',
  accent: '#4F46E5',
  accentContrast: '#FFFFFF',
  navActiveWash: '#EEF2FF',
  searchBg: '#F1F3F8',
  success: '#059669',
  successWash: '#ECFDF5',
} as const;

export const studioDark = {
  bg: '#0F1424',
  sidebar: '#131A2C',
  card: '#1B243B',
  border: '#33415F',
  text: '#F1F5FF',
  textSecondary: '#B7C3DA',
  accent: '#A5B4FC',
  accentContrast: '#0F1424',
  navActiveWash: '#293456',
  searchBg: '#1B243B',
  success: '#34D399',
  successWash: 'rgba(52, 211, 153, 0.12)',
} as const;

export type StudioTokens = typeof studioLight;

export const studioRadius = '10px';
export const studioSidebarWidth = 220;
