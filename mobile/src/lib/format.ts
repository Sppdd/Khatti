import type { Capture, VisualPrompt } from './types';

const DIACRITICS = /[ؐ-ًؚ-ٰٟۖ-ۭ]/g;
const ARABIC_DIGITS = '٠١٢٣٤٥٦٧٨٩';

// Same folding as khatti/arabic.py, so "طماطة" and "طماطه" group together in Prices.
export function normalizeName(name: string): string {
  return name
    .replace(DIACRITICS, '')
    .replace(/ـ/g, '')
    .replace(/[أإآٱ]/g, 'ا')
    .replace(/ى/g, 'ي')
    .replace(/ة/g, 'ه')
    .replace(/[٠-٩]/g, (d) => String(ARABIC_DIGITS.indexOf(d)))
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

export function formatMoney(value: number | null | undefined, currency?: string | null): string {
  if (value == null) return '—';
  const digits = Number.isInteger(value) ? 0 : 2;
  const n = value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: 2 });
  return currency ? `${n} ${currency}` : n;
}

export function timeAgo(iso: string): string {
  const s = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

export const kindLabel: Record<string, string> = {
  receipt: 'Receipt',
  price_tag: 'Price tag',
  menu: 'Menu',
  document: 'Document',
  note: 'Note',
  screenshot: 'Screenshot',
  product: 'Product',
  scene: 'Scene',
  other: 'Other',
};

// Compact, self-describing JSON to paste into any AI chat or pipeline.
export function toAIContext(c: Capture): string {
  const d = c.data;
  const payload: Record<string, unknown> = {
    source: 'khatti',
    captured_at: c.created_at,
    kind: d.kind,
    title: d.title,
    summary: d.summary,
    language: d.language,
    tags: d.tags,
  };
  if (d.text) payload.text = d.text;
  if (Object.keys(d.fields).length) payload.fields = d.fields;
  if (d.items.length) payload.items = d.items;
  if (d.store) payload.store = d.store;
  if (d.prompt) payload.visual_prompt = d.prompt;
  return JSON.stringify(payload, null, 2);
}

export interface PromptVariant {
  id: string;
  label: string;
  text: string;
}

// The same scene, phrased for the generators people actually paste into.
export function promptVariants(p: VisualPrompt): PromptVariant[] {
  const look = [p.style, p.mood, p.lighting, p.colors.length ? `palette ${p.colors.join(', ')}` : '']
    .filter(Boolean)
    .join(', ');
  return [
    { id: 'universal', label: 'Universal', text: p.prompt },
    {
      id: 'midjourney',
      label: 'Midjourney',
      text: `${p.prompt}${look ? `, ${look}` : ''} --ar 4:5 --style raw`,
    },
    {
      id: 'sd',
      label: 'Stable Diffusion',
      text: `${p.prompt}${p.camera ? `, ${p.camera}` : ''}, highly detailed\nNegative prompt: ${
        p.negative_prompt || 'blurry, low quality, watermark, text artifacts'
      }`,
    },
    {
      id: 'video',
      label: 'Video',
      text: `${p.subject || p.prompt}. ${p.composition ? `${p.composition}. ` : ''}Camera: ${
        p.camera || 'slow push-in'
      }, gentle parallax. Look: ${look || p.style}. 5 seconds, cinematic.`,
    },
  ];
}
