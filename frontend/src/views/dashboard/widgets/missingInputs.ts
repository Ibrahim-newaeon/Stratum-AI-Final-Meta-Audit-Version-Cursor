/**
 * Shared translation of the signal health "what's missing" list.
 */

import type { TFunction } from 'i18next';
import type { SignalHealthSummary } from '@/api/dashboard';

/**
 * Render the "what's missing" list in the viewer's language.
 *
 * The API sends stable `missing_input_codes` plus English `missing_inputs`
 * detail. Known codes are translated; anything the UI does not know about
 * falls back to the server's sentence, so a new backend reason still reaches
 * the user rather than disappearing.
 */
export function missingInputLabels(
  signalHealth: Pick<SignalHealthSummary, 'missing_input_codes' | 'missing_inputs'>,
  t: TFunction
): string[] {
  if (signalHealth.missing_input_codes.length > 0) {
    return signalHealth.missing_input_codes.map((code) =>
      t(`signalHealth.missing.${code}`, { defaultValue: '' }) || code
    );
  }
  return signalHealth.missing_inputs;
}
