/**
 * useDocumentDirection - Keeps <html> dir/lang attributes in sync with i18n language.
 *
 * Sets document direction to RTL for Arabic, LTR otherwise.
 */

import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';

const RTL_LANGUAGES = ['ar', 'he', 'fa', 'ur'];

export function useDocumentDirection() {
  const { i18n } = useTranslation();

  useEffect(() => {
    const language = i18n.language || 'en';
    const baseLang = language.split('-')[0];
    const dir = RTL_LANGUAGES.includes(baseLang) ? 'rtl' : 'ltr';

    document.documentElement.dir = dir;
    document.documentElement.lang = language;
  }, [i18n.language]);
}

export default useDocumentDirection;
