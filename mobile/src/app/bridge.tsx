import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { router } from 'expo-router';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ActivityIndicator, Alert, KeyboardAvoidingView, Platform, RefreshControl, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, LinearTransition } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, fonts, radius, space } from '../brand/theme';
import { Mascot } from '../components/Mascot';
import { Button, Card, Chip, PressableScale, SectionTitle, Txt } from '../components/ui';
import {
  chatOnce,
  endpointAction,
  generateImage,
  getModels,
  getProviders,
  hostModel,
  listEndpoints,
  type BridgeModel,
  type BridgeProvider,
  type HfEndpoint,
} from '../lib/api';
import { DEMO_ENDPOINTS, DEMO_MODELS, DEMO_PROVIDERS } from '../lib/demo';
import { useSettings } from '../lib/settings';

const isImageModel = (id: string) => /flux|sdxl|stable-diffusion|sd3|imagen|dall-e/i.test(id);
const isVisionModel = (id: string) => /vl\b|-vl-|vision|llava|pixtral|gemma-3|internvl|molmo|qwen2\.5-vl|nemotron-nano-vl/i.test(id);
const isEmbedding = (id: string) => /bge|e5-|embed|gte-/i.test(id);

const INSTANCES = ['nvidia-l4', 'nvidia-a10g', 'nvidia-l40s', 'nvidia-a100'];

const STATE_COLOR: Record<string, string> = {
  running: colors.mint,
  initializing: colors.saffron,
  pending: colors.saffron,
  updating: colors.saffron,
  paused: colors.textDim,
  scaledToZero: colors.sky,
  failed: colors.rose,
  updateFailed: colors.rose,
};

export default function BridgeScreen() {
  const insets = useSafeAreaInsets();
  const { settings, update } = useSettings();
  const demo = settings.demoMode;

  const [providers, setProviders] = useState<BridgeProvider[]>([]);
  const [hosting, setHosting] = useState(false);
  const [models, setModels] = useState<BridgeModel[]>([]);
  const [modelErrors, setModelErrors] = useState<Record<string, string>>({});
  const [endpoints, setEndpoints] = useState<HfEndpoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [tryModel, setTryModel] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (demo) {
        setProviders(DEMO_PROVIDERS);
        setModels(DEMO_MODELS);
        setEndpoints(DEMO_ENDPOINTS);
        setHosting(false);
        return;
      }
      const [p, m] = await Promise.all([getProviders(), getModels()]);
      setProviders(p.data.providers);
      setHosting(p.data.hosting);
      setModels(m.data.data);
      setModelErrors(m.data.errors);
      if (p.data.providers.some((x) => x.kind === 'hf-endpoints')) {
        setEndpoints((await listEndpoints()).data.items);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [demo]);

  useEffect(() => {
    // load() sets state only after awaiting the API.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return models.filter((m) => (!providerFilter || m.provider === providerFilter) && (!q || m.id.toLowerCase().includes(q))).slice(0, 80);
  }, [models, query, providerFilter]);

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1, backgroundColor: colors.ink }}>
      <ScrollView
        contentContainerStyle={{ paddingTop: insets.top + space(2), paddingHorizontal: space(5), paddingBottom: insets.bottom + space(10), gap: space(6) }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.violet} />}
        keyboardShouldPersistTaps="handled"
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(3) }}>
          <PressableScale onPress={() => router.back()} style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' }}>
            <Ionicons name="chevron-back" size={20} color={colors.text} />
          </PressableScale>
          <View style={{ flex: 1 }}>
            <Txt variant="display" style={{ fontSize: 28, lineHeight: 36 }}>
              Model bridge
            </Txt>
            <Txt variant="caption">Nebius Token Factory · Hugging Face · your own GPUs</Txt>
          </View>
        </View>

        {demo && (
          <Card style={{ flexDirection: 'row', gap: space(3), alignItems: 'center', borderColor: colors.saffron + '66' }}>
            <Ionicons name="flask" size={18} color={colors.saffron} />
            <Txt variant="caption" style={{ flex: 1 }}>
              Demo mode: sample providers and models. Turn demo mode off in API Lab to use your server.
            </Txt>
          </Card>
        )}
        {error && (
          <Card style={{ gap: space(2), alignItems: 'center' }}>
            <Mascot size={90} mood="sad" />
            <Txt variant="label" style={{ color: colors.rose, textAlign: 'center' }}>
              {error}
            </Txt>
          </Card>
        )}

        <View>
          <SectionTitle>Providers</SectionTitle>
          <View style={{ gap: space(2) }}>
            {providers.map((p, i) => (
              <Animated.View key={p.name} entering={FadeInDown.delay(i * 60)}>
                <Card style={{ gap: space(2) }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(2) }}>
                    <Ionicons name={p.kind === 'hf-endpoints' ? 'server' : p.name === 'nebius' ? 'planet' : p.name === 'hf' ? 'happy' : 'hardware-chip'} size={18} color={colors.violetSoft} />
                    <Txt variant="heading" style={{ flex: 1, fontSize: 15 }}>
                      {p.label}
                    </Txt>
                    <Txt variant="mono">{p.name}/</Txt>
                  </View>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    {p.features.map((f) => (
                      <View key={f} style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: radius.pill, backgroundColor: colors.surfaceHigh }}>
                        <Txt variant="caption" style={{ fontSize: 11 }}>
                          {f.replace('_', ' ')}
                        </Txt>
                      </View>
                    ))}
                    {p.default && (
                      <View style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: radius.pill, backgroundColor: colors.mint + '22' }}>
                        <Txt variant="caption" style={{ fontSize: 11, color: colors.mint }}>
                          default
                        </Txt>
                      </View>
                    )}
                  </View>
                  {modelErrors[p.name] && (
                    <Txt variant="caption" style={{ color: colors.rose }} numberOfLines={2}>
                      {modelErrors[p.name]}
                    </Txt>
                  )}
                </Card>
              </Animated.View>
            ))}
            {!providers.length && !loading && !error && (
              <Txt variant="label">No providers configured. Set KHATTI_TOKEN_FACTORY_KEY or HF_TOKEN on the server.</Txt>
            )}
          </View>
        </View>

        <View>
          <SectionTitle>Capture model</SectionTitle>
          <Card style={{ gap: space(2) }}>
            <Txt variant="caption">Photos are turned into data with:</Txt>
            <Txt variant="heading" style={{ fontSize: 15 }} selectable>
              {settings.extractModel || 'Server default (KHATTI_EXTRACTOR)'}
            </Txt>
            {!!settings.extractModel && <Button title="Use server default" variant="ghost" onPress={() => update({ extractModel: '' })} />}
          </Card>
        </View>

        <View>
          <SectionTitle>Models · {models.length}</SectionTitle>
          <View style={{ gap: space(3) }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(2), backgroundColor: colors.surface, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line, paddingHorizontal: space(4) }}>
              <Ionicons name="search" size={18} color={colors.textMute} />
              <TextInput
                value={query}
                onChangeText={setQuery}
                placeholder="qwen, llama, flux, vl…"
                placeholderTextColor={colors.textMute}
                autoCapitalize="none"
                autoCorrect={false}
                style={{ flex: 1, color: colors.text, fontFamily: fonts.body, fontSize: 15, paddingVertical: 12 }}
              />
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space(2) }}>
              <Chip label="All" active={!providerFilter} onPress={() => setProviderFilter(null)} />
              {providers.map((p) => (
                <Chip key={p.name} label={p.name} active={providerFilter === p.name} onPress={() => setProviderFilter(p.name)} />
              ))}
            </ScrollView>
            <Card style={{ padding: 0 }}>
              {shown.map((m, i) => (
                <ModelRow
                  key={m.id}
                  m={m}
                  first={i === 0}
                  selected={settings.extractModel === m.id}
                  onUse={() => update({ extractModel: m.id })}
                  onTry={() => setTryModel(m.id)}
                />
              ))}
              {!shown.length && (
                <Txt variant="label" style={{ padding: space(4) }}>
                  {loading ? 'Loading models…' : 'No models match.'}
                </Txt>
              )}
            </Card>
          </View>
        </View>

        <Playground model={tryModel} onModel={setTryModel} demo={demo} />

        <Hosting endpoints={endpoints} hosting={hosting} demo={demo} onChanged={load} onUse={(id) => update({ extractModel: id })} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <View style={{ paddingHorizontal: 7, paddingVertical: 1, borderRadius: radius.pill, backgroundColor: color + '22' }}>
      <Txt variant="caption" style={{ fontSize: 10, color, fontFamily: fonts.bodyMedium }}>
        {label}
      </Txt>
    </View>
  );
}

function ModelRow({ m, first, selected, onUse, onTry }: { m: BridgeModel; first: boolean; selected: boolean; onUse: () => void; onTry: () => void }) {
  const [provider, ...rest] = m.id.split('/');
  return (
    <Animated.View layout={LinearTransition} style={{ padding: space(3), gap: 6, borderTopWidth: first ? 0 : 1, borderColor: colors.line }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <Txt variant="caption" style={{ color: colors.violetSoft }}>
          {provider}/
        </Txt>
        <Txt variant="label" style={{ color: colors.text, flexShrink: 1 }} numberOfLines={1}>
          {rest.join('/')}
        </Txt>
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        {isVisionModel(m.id) && <Badge label="vision" color={colors.mint} />}
        {isImageModel(m.id) && <Badge label="image" color={colors.rose} />}
        {isEmbedding(m.id) && <Badge label="embeddings" color={colors.sky} />}
        {m.state && <Badge label={m.state} color={STATE_COLOR[m.state] ?? colors.textDim} />}
        {!!m.owned_by && m.provider === 'hfe' && (
          <Txt variant="caption" numberOfLines={1} style={{ flexShrink: 1 }}>
            {m.owned_by}
          </Txt>
        )}
        <View style={{ flex: 1 }} />
        <PressableScale onPress={onTry} style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line }}>
          <Txt variant="caption" style={{ color: colors.text }}>
            Try
          </Txt>
        </PressableScale>
        {!isImageModel(m.id) && !isEmbedding(m.id) && (
          <PressableScale
            onPress={onUse}
            style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill, backgroundColor: selected ? colors.mint + '33' : colors.violet + '33' }}
          >
            <Txt variant="caption" style={{ color: selected ? colors.mint : colors.violetSoft }}>
              {selected ? 'Capturing ✓' : 'Use for capture'}
            </Txt>
          </PressableScale>
        )}
      </View>
    </Animated.View>
  );
}

function Playground({ model, onModel, demo }: { model: string; onModel: (m: string) => void; demo: boolean }) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ text?: string; image?: string; ms: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const image = isImageModel(model);

  const run = async () => {
    setBusy(true);
    setError(null);
    setOut(null);
    try {
      if (demo) {
        await new Promise((r) => setTimeout(r, 900));
        setOut({ text: `(demo) ${model} would answer: “${prompt.slice(0, 80)}…”`, ms: 900 });
      } else if (image) {
        const r = await generateImage(model, prompt);
        setOut({ image: r.data, ms: r.latencyMs });
      } else {
        const r = await chatOnce(model, prompt);
        setOut({ text: r.data, ms: r.latencyMs });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View>
      <SectionTitle>Playground</SectionTitle>
      <Card style={{ gap: space(3) }}>
        <Field label="Model (provider/model id)">
          <TextInput value={model} onChangeText={onModel} placeholder="nebius/meta-llama/Llama-3.3-70B-Instruct" placeholderTextColor={colors.textMute} autoCapitalize="none" autoCorrect={false} style={input} />
        </Field>
        <Field label={image ? 'Image prompt' : 'Message'}>
          <TextInput
            value={prompt}
            onChangeText={setPrompt}
            placeholder={image ? 'A tea stall at golden hour, film photo' : 'Summarise what Khatti does in Arabic'}
            placeholderTextColor={colors.textMute}
            multiline
            style={[input, { minHeight: 80, textAlignVertical: 'top' }]}
          />
        </Field>
        <Button
          title={image ? 'Generate image' : 'Send'}
          loading={busy}
          disabled={!model || !prompt.trim()}
          onPress={run}
          icon={<Ionicons name={image ? 'image' : 'paper-plane'} size={18} color={colors.text} />}
        />
        {out && (
          <Animated.View entering={FadeIn} style={{ gap: space(2) }}>
            <Txt variant="caption">{out.ms} ms</Txt>
            {out.image ? (
              <Image source={{ uri: out.image }} style={{ width: '100%', aspectRatio: 1, borderRadius: radius.md }} contentFit="cover" />
            ) : (
              <Txt selectable>{out.text}</Txt>
            )}
          </Animated.View>
        )}
        {error && (
          <Txt variant="label" style={{ color: colors.rose }}>
            {error}
          </Txt>
        )}
      </Card>
    </View>
  );
}

function Hosting({
  endpoints,
  hosting,
  demo,
  onChanged,
  onUse,
}: {
  endpoints: HfEndpoint[];
  hosting: boolean;
  demo: boolean;
  onChanged: () => void;
  onUse: (id: string) => void;
}) {
  const [repo, setRepo] = useState('');
  const [instance, setInstance] = useState(INSTANCES[0]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const act = async (key: string, fn: () => Promise<unknown>) => {
    if (demo) {
      setMsg('Demo mode: nothing was changed.');
      return;
    }
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const deploy = () =>
    Alert.alert('Host this model?', `${repo} on ${instance}. Hugging Face bills the endpoint while it runs; it scales to zero after 15 idle minutes.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Deploy', onPress: () => act('deploy', () => hostModel(repo.trim(), instance)) },
    ]);

  const remove = (name: string) =>
    Alert.alert('Delete endpoint?', name, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => act(`${name}:delete`, () => endpointAction(name, 'delete')) },
    ]);

  return (
    <View>
      <SectionTitle>Host a Hugging Face model</SectionTitle>
      <Card style={{ gap: space(3) }}>
        <Field label="Hub model id">
          <TextInput value={repo} onChangeText={setRepo} placeholder="Qwen/Qwen2.5-VL-7B-Instruct" placeholderTextColor={colors.textMute} autoCapitalize="none" autoCorrect={false} style={input} />
        </Field>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space(2) }}>
          {INSTANCES.map((i) => (
            <Chip key={i} label={i.replace('nvidia-', '').toUpperCase()} active={instance === i} onPress={() => setInstance(i)} color={colors.saffron} />
          ))}
        </ScrollView>
        <Button title="Deploy endpoint" loading={busy === 'deploy'} disabled={!repo.includes('/') || (!hosting && !demo)} onPress={deploy} icon={<Ionicons name="rocket" size={18} color={colors.text} />} />
        {!hosting && !demo && <Txt variant="caption">Hosting is off on the server. Set KHATTI_ALLOW_HOSTING=1 to allow creating endpoints.</Txt>}
        {msg && <Txt variant="caption" style={{ color: colors.rose }}>{msg}</Txt>}

        {endpoints.map((e) => {
          const state = e.status?.state ?? 'unknown';
          const id = `hfe/${e.name}`;
          return (
            <Animated.View key={e.name} entering={FadeInDown} style={{ gap: space(2), borderTopWidth: 1, borderColor: colors.line, paddingTop: space(3) }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(2) }}>
                <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: STATE_COLOR[state] ?? colors.textDim }} />
                <View style={{ flex: 1 }}>
                  <Txt variant="label" style={{ color: colors.text }}>
                    {e.name}
                  </Txt>
                  <Txt variant="caption" numberOfLines={1}>
                    {e.model?.repository} · {e.compute?.instanceType} · {state}
                  </Txt>
                </View>
                {busy?.startsWith(e.name) && <ActivityIndicator color={colors.violet} />}
              </View>
              <View style={{ flexDirection: 'row', gap: space(2), flexWrap: 'wrap' }}>
                {state === 'running' && <Chip label="Use for capture" onPress={() => onUse(id)} color={colors.mint} />}
                {state === 'running' || state === 'initializing' ? (
                  <Chip label="Pause" onPress={() => act(`${e.name}:pause`, () => endpointAction(e.name, 'pause'))} />
                ) : (
                  <Chip label="Resume" onPress={() => act(`${e.name}:resume`, () => endpointAction(e.name, 'resume'))} />
                )}
                <Chip label="Delete" color={colors.rose} onPress={() => remove(e.name)} />
              </View>
            </Animated.View>
          );
        })}
      </Card>
    </View>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={{ gap: 6 }}>
      <Txt variant="caption">{label}</Txt>
      {children}
    </View>
  );
}

const input = {
  color: colors.text,
  fontFamily: fonts.body,
  fontSize: 15,
  backgroundColor: colors.surfaceHigh,
  borderRadius: radius.sm,
  borderWidth: 1,
  borderColor: colors.line,
  paddingHorizontal: space(3),
  paddingVertical: 10,
} as const;
