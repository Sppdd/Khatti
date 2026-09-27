import type { Ionicons } from '@expo/vector-icons';

import { colors } from '../brand/theme';
import type { ExtractMode } from './types';

export interface ModeInfo {
  id: ExtractMode;
  label: string;
  ar: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  blurb: string;
  hello: string; // what the mascot says when this mode is picked
}

export const MODES: ModeInfo[] = [
  {
    id: 'auto',
    label: 'Auto',
    ar: 'تلقائي',
    icon: 'sparkles',
    color: colors.violetSoft,
    blurb: 'I decide what matters',
    hello: 'Show me anything. I’ll figure out what it is.',
  },
  {
    id: 'prices',
    label: 'Prices',
    ar: 'أسعار',
    icon: 'pricetags',
    color: colors.saffron,
    blurb: 'Receipts, shelf tags, menus',
    hello: 'Snap a receipt or a price tag. I’ll track every price.',
  },
  {
    id: 'mind',
    label: 'Mind',
    ar: 'ذاكرة',
    icon: 'bulb',
    color: colors.mint,
    blurb: 'Save it, find it later',
    hello: 'Anything worth remembering? I’ll file it for you.',
  },
  {
    id: 'prompt',
    label: 'Prompt',
    ar: 'برومبت',
    icon: 'color-palette',
    color: colors.rose,
    blurb: 'Real world → AI prompt',
    hello: 'Point me at a scene. I’ll write the prompt to recreate it.',
  },
  {
    id: 'text',
    label: 'Text',
    ar: 'نص',
    icon: 'document-text',
    color: colors.sky,
    blurb: 'Exact transcription',
    hello: 'Arabic, English, handwriting. I’ll copy every word.',
  },
];

export const modeInfo = (id: string) => MODES.find((m) => m.id === id) ?? MODES[0];
