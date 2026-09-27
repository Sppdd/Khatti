import { Ionicons } from '@expo/vector-icons';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshControl, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, LinearTransition, useAnimatedStyle, useSharedValue, withDelay, withSpring } from 'react-native-reanimated';

import { colors, fonts, radius, space } from '../../brand/theme';
import { Mascot } from '../../components/Mascot';
import { Button, Card, PressableScale, Screen, Txt } from '../../components/ui';
import { formatMoney, normalizeName, timeAgo } from '../../lib/format';
import { listPriceItems } from '../../lib/store';
import type { PriceObservation } from '../../lib/types';

interface Product {
  key: string;
  name: string;
  currency: string | null;
  latest: PriceObservation;
  min: PriceObservation;
  max: PriceObservation;
  history: PriceObservation[];
  stores: number;
}

// Group observations by product (and currency, so IQD and USD never mix).
function groupProducts(rows: PriceObservation[]): Product[] {
  const map = new Map<string, PriceObservation[]>();
  for (const r of rows) {
    if (r.price == null) continue;
    const key = `${r.normalized_name}|${r.currency ?? ''}`;
    map.set(key, [...(map.get(key) ?? []), r]);
  }
  return [...map.entries()]
    .map(([key, list]) => {
      const history = [...list].sort((a, b) => b.observed_at.localeCompare(a.observed_at));
      const byPrice = [...list].sort((a, b) => (a.price ?? 0) - (b.price ?? 0));
      return {
        key,
        name: history[0].name,
        currency: history[0].currency,
        latest: history[0],
        min: byPrice[0],
        max: byPrice[byPrice.length - 1],
        history,
        stores: new Set(list.map((r) => r.store ?? '?')).size,
      };
    })
    .sort((a, b) => b.latest.observed_at.localeCompare(a.latest.observed_at));
}

export default function PricesScreen() {
  const [rows, setRows] = useState<PriceObservation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listPriceItems());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const products = useMemo(() => {
    const all = groupProducts(rows);
    const q = normalizeName(query);
    return q ? all.filter((p) => p.key.includes(q)) : all;
  }, [rows, query]);

  const storeCount = useMemo(() => new Set(rows.map((r) => r.store).filter(Boolean)).size, [rows]);

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ padding: space(5), paddingBottom: 140, gap: space(4) }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.saffron} />}
        keyboardShouldPersistTaps="handled"
      >
        <View>
          <Txt variant="display" style={{ fontSize: 30 }}>
            Prices
          </Txt>
          <Txt variant="label">Every price you capture, compared across stores and time.</Txt>
        </View>

        <View style={{ flexDirection: 'row', gap: space(3) }}>
          <Stat label="Products" value={products.length} color={colors.saffron} />
          <Stat label="Observations" value={rows.length} color={colors.violetSoft} />
          <Stat label="Stores" value={storeCount} color={colors.mint} />
        </View>

        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(2), backgroundColor: colors.surface, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line, paddingHorizontal: space(4) }}>
          <Ionicons name="search" size={18} color={colors.textMute} />
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Find a product… طماطة"
            placeholderTextColor={colors.textMute}
            style={{ flex: 1, color: colors.text, fontFamily: fonts.body, fontSize: 15, paddingVertical: 12 }}
          />
        </View>

        {error && (
          <Txt variant="label" style={{ color: colors.rose }}>
            {error}
          </Txt>
        )}

        {products.length === 0 && !loading && !error ? (
          <Card style={{ alignItems: 'center', gap: space(3), paddingVertical: space(8) }}>
            <Mascot size={110} mood="idle" />
            <Txt variant="heading">No prices yet</Txt>
            <Txt variant="label" style={{ textAlign: 'center' }}>
              Snap receipts and shelf tags in Prices mode. Khatti tracks each item so you can see who is cheaper and when prices move.
            </Txt>
            <Button title="Capture a receipt" onPress={() => router.navigate('/')} />
          </Card>
        ) : (
          products.map((p, i) => (
            <Animated.View key={p.key} entering={FadeInDown.delay(Math.min(i, 10) * 50).springify().damping(16)} layout={LinearTransition}>
              <ProductCard p={p} open={open === p.key} onToggle={() => setOpen(open === p.key ? null : p.key)} />
            </Animated.View>
          ))
        )}
      </ScrollView>
    </Screen>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <Card style={{ flex: 1, padding: space(3), gap: 2 }}>
      <Txt style={{ fontFamily: fonts.brand, fontSize: 24, lineHeight: 32, color }}>{value}</Txt>
      <Txt variant="caption">{label}</Txt>
    </Card>
  );
}

function ProductCard({ p, open, onToggle }: { p: Product; open: boolean; onToggle: () => void }) {
  const lo = p.min.price ?? 0;
  const hi = p.max.price ?? 0;
  const pos = hi > lo ? ((p.latest.price ?? 0) - lo) / (hi - lo) : 0.5;
  const change = p.history.length > 1 && p.history[1].price ? ((p.latest.price ?? 0) - p.history[1].price) / p.history[1].price : 0;

  return (
    <PressableScale onPress={onToggle} scaleTo={0.98}>
      <Card style={{ gap: space(3) }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: space(3) }}>
          <View style={{ flex: 1 }}>
            <Txt variant="heading">{p.name}</Txt>
            <Txt variant="caption">
              {p.history.length} price{p.history.length === 1 ? '' : 's'} · {p.stores} store{p.stores === 1 ? '' : 's'} · {timeAgo(p.latest.observed_at)}
            </Txt>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Txt variant="heading" style={{ color: colors.saffron }}>
              {formatMoney(p.latest.price, p.currency)}
            </Txt>
            {change !== 0 && (
              <Txt variant="caption" style={{ color: change > 0 ? colors.rose : colors.mint }}>
                {change > 0 ? '▲' : '▼'} {Math.abs(change * 100).toFixed(0)}%
              </Txt>
            )}
          </View>
        </View>

        {p.history.length > 1 && <RangeBar pos={pos} low={formatMoney(lo)} high={formatMoney(hi)} />}

        {p.min.store && p.history.length > 1 && (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Ionicons name="trophy" size={14} color={colors.mint} />
            <Txt variant="caption" style={{ color: colors.mint }}>
              Cheapest at {p.min.store} ({formatMoney(p.min.price, p.currency)})
            </Txt>
          </View>
        )}

        {open && (
          <Animated.View entering={FadeIn} style={{ gap: space(2), borderTopWidth: 1, borderColor: colors.line, paddingTop: space(3) }}>
            {p.history.map((h) => (
              <PressableScale key={h.id} onPress={() => router.push(`/capture/${h.capture_id}`)} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <View>
                  <Txt variant="label" style={{ color: colors.text }}>
                    {h.store ?? 'Unknown store'}
                  </Txt>
                  <Txt variant="caption">{new Date(h.observed_at).toLocaleDateString()}</Txt>
                </View>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Txt variant="label" style={{ color: h.id === p.min.id ? colors.mint : colors.text }}>
                    {formatMoney(h.price, h.currency)}
                  </Txt>
                  <Ionicons name="chevron-forward" size={14} color={colors.textMute} />
                </View>
              </PressableScale>
            ))}
          </Animated.View>
        )}
      </Card>
    </PressableScale>
  );
}

function RangeBar({ pos, low, high }: { pos: number; low: string; high: string }) {
  const [w, setW] = useState(0);
  const x = useSharedValue(0);
  useEffect(() => {
    x.value = withDelay(200, withSpring(pos * Math.max(0, w - 14), { damping: 14 }));
  }, [pos, w, x]);
  const dot = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  return (
    <View style={{ gap: 4 }}>
      <View onLayout={(e) => setW(e.nativeEvent.layout.width)} style={{ height: 14, justifyContent: 'center' }}>
        <View style={{ height: 6, borderRadius: 3, backgroundColor: colors.surfaceHigh, overflow: 'hidden', flexDirection: 'row' }}>
          <View style={{ flex: 1, backgroundColor: colors.mint + '66' }} />
          <View style={{ flex: 1, backgroundColor: colors.saffron + '66' }} />
          <View style={{ flex: 1, backgroundColor: colors.rose + '66' }} />
        </View>
        <Animated.View style={[{ position: 'absolute', width: 14, height: 14, borderRadius: 7, backgroundColor: colors.text, borderWidth: 3, borderColor: colors.saffron }, dot]} />
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <Txt variant="caption">Low {low}</Txt>
        <Txt variant="caption">High {high}</Txt>
      </View>
    </View>
  );
}
