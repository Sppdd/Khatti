import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import { Image } from 'expo-image';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState, type ReactNode } from 'react';
import { ActivityIndicator, Alert, ScrollView, Share, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, ZoomIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, fonts, kindColor, radius, space } from '../../brand/theme';
import { Mascot } from '../../components/Mascot';
import { Button, Card, Chip, KindBadge, PressableScale, SectionTitle, Txt, tap } from '../../components/ui';
import { formatMoney, promptVariants, timeAgo, toAIContext } from '../../lib/format';
import { modeInfo } from '../../lib/modes';
import { deleteCapture, getCapture, setFavorite } from '../../lib/store';
import type { Capture } from '../../lib/types';

const isArabic = (s: string | null | undefined) => !!s && /[؀-ۿ]/.test(s);

export default function CaptureDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const insets = useSafeAreaInsets();
  const [capture, setCapture] = useState<Capture | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [variant, setVariant] = useState('universal');
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    getCapture(id)
      .then((c) => (c ? setCapture(c) : setError('This capture no longer exists.')))
      .catch((e) => setError(e.message));
  }, [id]);

  const copy = async (key: string, text: string) => {
    await Clipboard.setStringAsync(text);
    tap();
    setCopied(key);
    setTimeout(() => setCopied((k) => (k === key ? null : k)), 1600);
  };

  if (!capture) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.ink, alignItems: 'center', justifyContent: 'center', gap: space(3) }}>
        {error ? (
          <>
            <Mascot size={110} mood="sad" />
            <Txt variant="label">{error}</Txt>
            <Button title="Back" variant="ghost" onPress={() => router.back()} />
          </>
        ) : (
          <ActivityIndicator color={colors.violet} />
        )}
      </View>
    );
  }

  const d = capture.data;
  const mode = modeInfo(capture.mode);
  const accent = kindColor[d.kind] ?? colors.violet;
  const variants = d.prompt ? promptVariants(d.prompt) : [];
  const activeVariant = variants.find((v) => v.id === variant) ?? variants[0];
  const fields = Object.entries(d.fields);

  const toggleFavorite = async () => {
    const next = !capture.favorite;
    setCapture({ ...capture, favorite: next });
    try {
      await setFavorite(capture.id, next);
    } catch (e) {
      setCapture({ ...capture, favorite: !next });
      Alert.alert('Could not update', (e as Error).message);
    }
  };

  const remove = () =>
    Alert.alert('Delete capture?', 'The photo and its data will be removed.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            await deleteCapture(capture);
            router.back();
          } catch (e) {
            Alert.alert('Could not delete', (e as Error).message);
          }
        },
      },
    ]);

  const at = (slot: number) => slot * 70; // stagger sections top to bottom

  return (
    <View style={{ flex: 1, backgroundColor: colors.ink }}>
      <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + space(10) }} showsVerticalScrollIndicator={false}>
        <Animated.View entering={FadeIn.duration(400)}>
          <Image
            source={capture.image_url ? { uri: capture.image_url } : undefined}
            style={{ width: '100%', height: 340, backgroundColor: colors.surface }}
            contentFit="cover"
            transition={300}
          />
          <View style={{ position: 'absolute', top: insets.top + space(2), left: space(4), right: space(4), flexDirection: 'row', justifyContent: 'space-between' }}>
            <RoundIcon icon="chevron-back" onPress={() => router.back()} />
            <View style={{ flexDirection: 'row', gap: space(2) }}>
              <RoundIcon icon={capture.favorite ? 'heart' : 'heart-outline'} color={capture.favorite ? colors.rose : colors.text} onPress={toggleFavorite} />
              <RoundIcon icon="share-outline" onPress={() => Share.share({ message: toAIContext(capture) })} />
              <RoundIcon icon="trash-outline" onPress={remove} />
            </View>
          </View>
        </Animated.View>

        <View style={{ padding: space(5), gap: space(5), marginTop: -space(8), borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, backgroundColor: colors.ink }}>
          <Animated.View entering={FadeInDown.delay(at(1))} style={{ gap: space(2) }}>
            <View style={{ flexDirection: 'row', gap: space(2), alignItems: 'center' }}>
              <KindBadge kind={d.kind} />
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <Ionicons name={mode.icon} size={12} color={mode.color} />
                <Txt variant="caption" style={{ color: mode.color }}>
                  {mode.label}
                </Txt>
              </View>
            </View>
            <Txt variant="title" style={{ writingDirection: isArabic(capture.title) ? 'rtl' : 'ltr' }}>
              {capture.title}
            </Txt>
            {!!d.summary && <Txt style={{ color: colors.textDim }}>{d.summary}</Txt>}
            <Txt variant="caption">
              {timeAgo(capture.created_at)}
              {capture.latency_ms != null ? ` · ${(capture.latency_ms / 1000).toFixed(1)}s` : ''}
              {d.model ? ` · ${d.model}` : ''}
              {d.language ? ` · ${d.language}` : ''}
            </Txt>
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(at(2))} style={{ flexDirection: 'row', gap: space(3) }}>
            <Button
              title={copied === 'ai' ? 'Copied!' : 'Copy for AI'}
              icon={<Ionicons name={copied === 'ai' ? 'checkmark' : 'sparkles'} size={18} color={colors.text} />}
              onPress={() => copy('ai', toAIContext(capture))}
              style={{ flex: 1 }}
            />
            {!!d.text && (
              <Button
                title={copied === 'text' ? 'Copied!' : 'Copy text'}
                variant="ghost"
                icon={<Ionicons name={copied === 'text' ? 'checkmark' : 'copy-outline'} size={18} color={colors.text} />}
                onPress={() => copy('text', d.text ?? '')}
                style={{ flex: 1 }}
              />
            )}
          </Animated.View>

          {d.items.length > 0 && (
            <Section title="Prices" delay={at(3)}>
              <Card style={{ padding: 0, overflow: 'hidden' }}>
                {d.store && (
                  <View style={{ padding: space(4), borderBottomWidth: 1, borderColor: colors.line, flexDirection: 'row', alignItems: 'center', gap: space(3) }}>
                    <Ionicons name="storefront" size={20} color={colors.saffron} />
                    <View style={{ flex: 1 }}>
                      <Txt variant="heading">{d.store.name ?? 'Unknown store'}</Txt>
                      <Txt variant="caption">{[d.store.location, d.store.date].filter(Boolean).join(' · ') || '—'}</Txt>
                    </View>
                  </View>
                )}
                {d.items.map((it, i) => (
                  <Animated.View
                    key={`${it.name}-${i}`}
                    entering={FadeInDown.delay(at(3) + i * 50)}
                    style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: space(4), paddingVertical: space(3), borderTopWidth: i ? 1 : 0, borderColor: colors.line }}
                  >
                    <View style={{ flex: 1 }}>
                      <Txt>{it.name}</Txt>
                      {(it.quantity != null || it.unit) && (
                        <Txt variant="caption">
                          {[it.quantity, it.unit].filter((x) => x != null && x !== '').join(' ')}
                        </Txt>
                      )}
                    </View>
                    <Txt variant="heading" style={{ color: colors.saffron }}>
                      {formatMoney(it.price, it.currency ?? d.store?.currency)}
                    </Txt>
                  </Animated.View>
                ))}
                {d.store?.total != null && (
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', padding: space(4), backgroundColor: colors.saffron + '14' }}>
                    <Txt variant="heading">Total</Txt>
                    <Txt variant="heading" style={{ color: colors.saffron }}>
                      {formatMoney(d.store.total, d.store.currency)}
                    </Txt>
                  </View>
                )}
              </Card>
            </Section>
          )}

          {d.prompt && activeVariant && (
            <Section title="Prompt studio" delay={at(4)}>
              <Card style={{ gap: space(3) }}>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space(2) }}>
                  {variants.map((v) => (
                    <Chip key={v.id} label={v.label} active={v.id === activeVariant.id} color={colors.rose} onPress={() => setVariant(v.id)} />
                  ))}
                </ScrollView>
                <Animated.View key={activeVariant.id} entering={FadeIn.duration(250)}>
                  <Txt selectable style={{ lineHeight: 24 }}>
                    {activeVariant.text}
                  </Txt>
                </Animated.View>
                {d.prompt.colors.length > 0 && (
                  <View style={{ flexDirection: 'row', gap: space(2) }}>
                    {d.prompt.colors.map((c, i) => (
                      <Animated.View key={c + i} entering={ZoomIn.delay(i * 80).springify()} style={{ alignItems: 'center', gap: 4 }}>
                        <View style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: c.startsWith('#') ? c : colors.surfaceHigh, borderWidth: 1, borderColor: colors.line }} />
                        <Txt variant="caption" style={{ fontSize: 10 }}>
                          {c}
                        </Txt>
                      </Animated.View>
                    ))}
                  </View>
                )}
                {[
                  ['Subject', d.prompt.subject],
                  ['Style', d.prompt.style],
                  ['Mood', d.prompt.mood],
                  ['Lighting', d.prompt.lighting],
                  ['Composition', d.prompt.composition],
                  ['Camera', d.prompt.camera],
                ]
                  .filter(([, v]) => v)
                  .map(([k, v]) => (
                    <Row key={k} k={k} v={v} />
                  ))}
                <Button
                  title={copied === 'prompt' ? 'Copied!' : `Copy ${activeVariant.label} prompt`}
                  variant="ghost"
                  icon={<Ionicons name={copied === 'prompt' ? 'checkmark' : 'copy-outline'} size={18} color={colors.text} />}
                  onPress={() => copy('prompt', activeVariant.text)}
                />
              </Card>
            </Section>
          )}

          {fields.length > 0 && (
            <Section title="Details" delay={at(5)}>
              <Card style={{ gap: space(2) }}>
                {fields.map(([k, v]) => (
                  <Row key={k} k={k.replace(/_/g, ' ')} v={v} />
                ))}
              </Card>
            </Section>
          )}

          {!!d.text && (
            <Section title="Text" delay={at(6)}>
              <Card>
                <Txt selectable style={{ textAlign: isArabic(d.text) ? 'right' : 'left', writingDirection: isArabic(d.text) ? 'rtl' : 'ltr' }}>
                  {d.text}
                </Txt>
              </Card>
            </Section>
          )}

          {d.tags.length > 0 && (
            <Section title="Tags" delay={at(7)}>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space(2) }}>
                {d.tags.map((t) => (
                  <Chip key={t} label={`#${t}`} color={accent} onPress={() => router.navigate({ pathname: '/library', params: { q: t } })} />
                ))}
              </View>
            </Section>
          )}

          <Section title="Structured data" delay={at(8)}>
            <Card style={{ gap: space(3) }}>
              <PressableScale onPress={() => setShowJson((s) => !s)} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Txt variant="label">{showJson ? 'Hide' : 'Show'} API response</Txt>
                <Ionicons name={showJson ? 'chevron-up' : 'chevron-down'} size={18} color={colors.textDim} />
              </PressableScale>
              {showJson && (
                <Animated.View entering={FadeIn}>
                  <Txt variant="mono" selectable>
                    {JSON.stringify(d, null, 2)}
                  </Txt>
                </Animated.View>
              )}
            </Card>
          </Section>
        </View>
      </ScrollView>
    </View>
  );
}

function Section({ title, delay, children }: { title: string; delay: number; children: ReactNode }) {
  return (
    <Animated.View entering={FadeInDown.delay(delay).springify().damping(16)}>
      <SectionTitle>{title}</SectionTitle>
      {children}
    </Animated.View>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <View style={{ flexDirection: 'row', gap: space(3) }}>
      <Txt variant="label" style={{ width: 100, textTransform: 'capitalize' }}>
        {k}
      </Txt>
      <Txt selectable style={{ flex: 1, fontFamily: fonts.bodyMedium }}>
        {v}
      </Txt>
    </View>
  );
}

function RoundIcon({ icon, onPress, color = colors.text }: { icon: keyof typeof Ionicons.glyphMap; onPress: () => void; color?: string }) {
  return (
    <PressableScale onPress={onPress} style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.ink + 'B3', alignItems: 'center', justifyContent: 'center' }}>
      <Ionicons name={icon} size={20} color={color} />
    </PressableScale>
  );
}
