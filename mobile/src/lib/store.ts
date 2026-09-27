// Data layer. Uses Supabase when configured, otherwise keeps everything on the device,
// so the app is fully usable before the backend exists.
import AsyncStorage from '@react-native-async-storage/async-storage';
import { decode } from 'base64-arraybuffer';
import { Directory, File, Paths } from 'expo-file-system';
import { Platform } from 'react-native';

import { normalizeName } from './format';
import type { PreparedImage } from './image';
import { CAPTURE_BUCKET, supabase } from './supabase';
import type { ApiLog, Capture, ExtractResult, PriceObservation } from './types';

export const isCloud = !!supabase;

// Just-saved captures, so the detail screen opens instantly without a round trip.
const recent = new Map<string, Capture>();

const uuid = () =>
  'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });

async function userId(): Promise<string> {
  const { data } = await supabase!.auth.getSession();
  const id = data.session?.user.id;
  if (!id) throw new Error('Not signed in to Supabase yet');
  return id;
}

function priceRows(captureId: string, r: ExtractResult, observedAt: string): PriceObservation[] {
  return r.items.map((it) => ({
    id: uuid(),
    capture_id: captureId,
    observed_at: observedAt,
    name: it.name,
    normalized_name: normalizeName(it.name),
    price: it.price,
    currency: it.currency ?? r.store?.currency ?? null,
    quantity: it.quantity,
    unit: it.unit,
    store: r.store?.name ?? null,
  }));
}

// ---------- local (device) backend ----------

const K = { captures: 'khatti.captures.v1', prices: 'khatti.prices.v1', logs: 'khatti.logs.v1' };

async function readList<T>(key: string): Promise<T[]> {
  try {
    return JSON.parse((await AsyncStorage.getItem(key)) ?? '[]') as T[];
  } catch {
    return [];
  }
}
const writeList = (key: string, list: unknown[]) => AsyncStorage.setItem(key, JSON.stringify(list));

async function keepImageLocally(image: PreparedImage, id: string): Promise<string> {
  if (Platform.OS === 'web') return image.uri;
  const dir = new Directory(Paths.document, 'captures');
  if (!dir.exists) dir.create({ intermediates: true });
  const dest = new File(dir, `${id}.jpg`);
  await new File(image.uri).copy(dest);
  return dest.uri;
}

// ---------- public API ----------

export async function saveCapture(input: {
  result: ExtractResult;
  image: PreparedImage;
  latencyMs: number | null;
}): Promise<Capture> {
  const { result, image, latencyMs } = input;
  const id = uuid();
  const now = new Date().toISOString();
  const row = {
    id,
    mode: result.mode,
    kind: result.kind,
    title: result.title || 'Untitled capture',
    summary: result.summary,
    language: result.language,
    text: result.text,
    tags: result.tags,
    data: result,
    model: result.model,
    latency_ms: latencyMs,
    favorite: false,
  };

  if (supabase) {
    const uid = await userId();
    const path = `${uid}/${id}.jpg`;
    const up = await supabase.storage
      .from(CAPTURE_BUCKET)
      .upload(path, decode(image.base64), { contentType: 'image/jpeg' });
    if (up.error) throw new Error(`Image upload failed: ${up.error.message}`);
    const { data, error } = await supabase
      .from('captures')
      .insert({ ...row, image_path: path })
      .select()
      .single();
    if (error) throw new Error(`Saving capture failed: ${error.message}`);
    const prices = priceRows(id, result, data.created_at).map(({ id: _, ...p }) => p);
    if (prices.length) {
      const res = await supabase.from('price_items').insert(prices);
      if (res.error) throw new Error(`Saving prices failed: ${res.error.message}`);
    }
    const saved = { ...(data as Capture), image_url: image.uri };
    recent.set(id, saved);
    return saved;
  }

  const capture: Capture = {
    ...row,
    created_at: now,
    image_path: await keepImageLocally(image, id),
  };
  capture.image_url = capture.image_path;
  await writeList(K.captures, [capture, ...(await readList<Capture>(K.captures))]);
  await writeList(K.prices, [...priceRows(id, result, now), ...(await readList<PriceObservation>(K.prices))]);
  return capture;
}

async function withSignedUrls(rows: Capture[]): Promise<Capture[]> {
  const paths = rows.map((r) => r.image_path).filter((p): p is string => !!p);
  if (!supabase || !paths.length) return rows;
  const { data } = await supabase.storage.from(CAPTURE_BUCKET).createSignedUrls(paths, 60 * 60);
  const byPath = new Map((data ?? []).map((d) => [d.path, d.signedUrl]));
  return rows.map((r) => ({ ...r, image_url: r.image_path ? byPath.get(r.image_path) ?? null : null }));
}

export async function listCaptures(opts: { search?: string; kind?: string } = {}): Promise<Capture[]> {
  const q = opts.search?.trim();
  if (supabase) {
    let query = supabase.from('captures').select('*').order('created_at', { ascending: false }).limit(200);
    if (opts.kind) query = query.eq('kind', opts.kind);
    if (q) {
      // Quoted PostgREST values; strip characters that would break the filter syntax.
      const like = `"%${q.replace(/[%,()"\\]/g, ' ')}%"`;
      query = query.or(`title.ilike.${like},summary.ilike.${like},text.ilike.${like}`);
    }
    const { data, error } = await query;
    if (error) throw new Error(error.message);
    return withSignedUrls((data ?? []) as Capture[]);
  }
  const needle = q?.toLowerCase();
  return (await readList<Capture>(K.captures)).filter(
    (c) =>
      (!opts.kind || c.kind === opts.kind) &&
      (!needle ||
        [c.title, c.summary, c.text ?? '', c.tags.join(' ')].some((s) => s.toLowerCase().includes(needle))),
  );
}

export async function getCapture(id: string): Promise<Capture | null> {
  const cached = recent.get(id);
  if (cached) return cached;
  if (supabase) {
    const { data, error } = await supabase.from('captures').select('*').eq('id', id).maybeSingle();
    if (error) throw new Error(error.message);
    return data ? (await withSignedUrls([data as Capture]))[0] : null;
  }
  return (await readList<Capture>(K.captures)).find((c) => c.id === id) ?? null;
}

export async function setFavorite(id: string, favorite: boolean): Promise<void> {
  if (supabase) {
    const { error } = await supabase.from('captures').update({ favorite }).eq('id', id);
    if (error) throw new Error(error.message);
    const cached = recent.get(id);
    if (cached) recent.set(id, { ...cached, favorite });
    return;
  }
  const list = await readList<Capture>(K.captures);
  await writeList(
    K.captures,
    list.map((c) => (c.id === id ? { ...c, favorite } : c)),
  );
}

export async function deleteCapture(c: Capture): Promise<void> {
  recent.delete(c.id);
  if (supabase) {
    if (c.image_path) await supabase.storage.from(CAPTURE_BUCKET).remove([c.image_path]);
    const { error } = await supabase.from('captures').delete().eq('id', c.id);
    if (error) throw new Error(error.message);
    return;
  }
  if (c.image_path && Platform.OS !== 'web') {
    const f = new File(c.image_path);
    if (f.exists) f.delete();
  }
  await writeList(K.captures, (await readList<Capture>(K.captures)).filter((x) => x.id !== c.id));
  await writeList(K.prices, (await readList<PriceObservation>(K.prices)).filter((p) => p.capture_id !== c.id));
}

export async function listPriceItems(): Promise<PriceObservation[]> {
  if (supabase) {
    const { data, error } = await supabase
      .from('price_items')
      .select('*')
      .order('observed_at', { ascending: false })
      .limit(1000);
    if (error) throw new Error(error.message);
    return (data ?? []) as PriceObservation[];
  }
  return readList<PriceObservation>(K.prices);
}

export async function logApi(entry: Omit<ApiLog, 'id' | 'created_at'>): Promise<void> {
  try {
    if (supabase) {
      if (!(await supabase.auth.getSession()).data.session) return;
      await supabase.from('api_logs').insert(entry);
      return;
    }
    const logs = await readList<ApiLog>(K.logs);
    await writeList(K.logs, [{ ...entry, id: uuid(), created_at: new Date().toISOString() }, ...logs].slice(0, 200));
  } catch {
    // Logging must never break the flow it observes.
  }
}

export async function listLogs(limit = 50): Promise<ApiLog[]> {
  if (supabase) {
    const { data } = await supabase
      .from('api_logs')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(limit);
    return (data ?? []) as ApiLog[];
  }
  return (await readList<ApiLog>(K.logs)).slice(0, limit);
}
