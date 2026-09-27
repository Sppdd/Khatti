// Mirrors khatti/extract.py (ExtractResult). Keep the two in sync.
export type ExtractMode = 'auto' | 'prices' | 'mind' | 'prompt' | 'text';

export type Kind =
  | 'receipt'
  | 'price_tag'
  | 'menu'
  | 'document'
  | 'note'
  | 'screenshot'
  | 'product'
  | 'scene'
  | 'other';

export interface PriceItem {
  name: string;
  price: number | null;
  currency: string | null;
  quantity: number | null;
  unit: string | null;
}

export interface StoreInfo {
  name: string | null;
  location: string | null;
  date: string | null;
  total: number | null;
  currency: string | null;
}

export interface VisualPrompt {
  subject: string;
  style: string;
  mood: string;
  lighting: string;
  colors: string[];
  composition: string;
  camera: string;
  prompt: string;
  negative_prompt: string;
}

export interface ExtractResult {
  mode: ExtractMode;
  kind: Kind;
  title: string;
  summary: string;
  language: string | null;
  text: string | null;
  tags: string[];
  fields: Record<string, string>;
  items: PriceItem[];
  store: StoreInfo | null;
  prompt: VisualPrompt | null;
  model: string | null;
}

export interface Capture {
  id: string;
  created_at: string;
  mode: ExtractMode;
  kind: Kind;
  title: string;
  summary: string;
  language: string | null;
  text: string | null;
  tags: string[];
  data: ExtractResult;
  image_path: string | null;
  // Resolved for display: a signed URL (Supabase) or a local file URI.
  image_url?: string | null;
  model: string | null;
  latency_ms: number | null;
  favorite: boolean;
}

export interface PriceObservation {
  id: string;
  capture_id: string;
  observed_at: string;
  name: string;
  normalized_name: string;
  price: number | null;
  currency: string | null;
  quantity: number | null;
  unit: string | null;
  store: string | null;
}

export interface ApiLog {
  id: string | number;
  created_at: string;
  endpoint: string;
  status: number | null;
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  meta: Record<string, unknown>;
}

export interface KycCaseResult {
  decision: 'approve' | 'human_review' | 'reject';
  confidence: number;
  rationale?: string;
  [key: string]: unknown;
}
