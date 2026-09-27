import type { ExtractMode, ExtractResult } from './types';

// Sample API responses so the whole app can be tried before the Khatti API is deployed.
const base = { language: null, text: null, fields: {}, items: [], store: null, prompt: null, model: 'demo' };

const samples: Record<ExtractMode, Omit<ExtractResult, 'mode'>> = {
  prices: {
    ...base,
    kind: 'receipt',
    title: 'Al-Nakheel market receipt',
    summary: 'Weekly vegetables and dairy from a neighbourhood market in Baghdad.',
    language: 'ar',
    text: 'سوق النخيل\nطماطة ١ كغم  ١٬٢٥٠\nخيار ١ كغم  ١٬٠٠٠\nحليب ١ لتر  ٢٬٠٠٠\nالمجموع  ٤٬٢٥٠',
    tags: ['groceries', 'vegetables', 'dairy', 'baghdad'],
    items: [
      { name: 'طماطة', price: 1250, currency: 'IQD', quantity: 1, unit: 'kg' },
      { name: 'خيار', price: 1000, currency: 'IQD', quantity: 1, unit: 'kg' },
      { name: 'حليب', price: 2000, currency: 'IQD', quantity: 1, unit: 'L' },
    ],
    store: { name: 'سوق النخيل', location: 'Baghdad', date: new Date().toISOString().slice(0, 10), total: 4250, currency: 'IQD' },
  },
  mind: {
    ...base,
    kind: 'screenshot',
    title: 'Minimal desk setup idea',
    summary: 'A warm, minimal desk with a wooden monitor stand and a single plant. Saved as inspiration for the studio.',
    tags: ['inspiration', 'workspace', 'minimal', 'wood'],
    fields: { saved_for: 'studio redesign' },
  },
  prompt: {
    ...base,
    kind: 'scene',
    title: 'Golden hour tea stall',
    summary: 'A street tea stall glowing at sunset with steam rising from small glass cups.',
    tags: ['street', 'tea', 'golden hour', 'baghdad'],
    prompt: {
      subject: 'a street tea stall with small glass istikan cups and a brass samovar',
      style: 'documentary photography, film grain',
      mood: 'warm, nostalgic, calm',
      lighting: 'golden hour backlight, soft haze',
      colors: ['#F2A541', '#7A3E1D', '#1C2F4A'],
      composition: 'low angle, samovar in foreground, bokeh street behind',
      camera: '35mm lens, f/1.8, shallow depth of field',
      prompt:
        'A street tea stall at golden hour, steam rising from small glass cups beside a brass samovar, warm backlight and soft haze, documentary film photograph, 35mm, shallow depth of field',
      negative_prompt: 'cartoon, oversaturated, extra fingers, text',
    },
  },
  text: {
    ...base,
    kind: 'note',
    title: 'Handwritten meeting note',
    summary: 'Action items from a product meeting.',
    language: 'mixed',
    text: 'Khatti v1\n- API test on Friday\n- جمع أسعار السوق\n- Supabase logging',
    tags: ['meeting', 'todo', 'khatti'],
    fields: { due: 'Friday' },
  },
  auto: {
    ...base,
    kind: 'price_tag',
    title: 'Olive oil shelf price',
    summary: 'Shelf label for extra virgin olive oil, 1 litre.',
    language: 'ar',
    text: 'زيت زيتون بكر ممتاز\n١ لتر\n١٢٬٥٠٠ د.ع',
    tags: ['olive oil', 'groceries', 'price'],
    items: [{ name: 'زيت زيتون بكر ممتاز', price: 12500, currency: 'IQD', quantity: 1, unit: 'L' }],
  },
};

export function demoExtract(mode: ExtractMode): ExtractResult {
  return { mode, ...samples[mode] };
}
