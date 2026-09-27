import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshControl, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeInDown, LinearTransition } from 'react-native-reanimated';

import { colors, fonts, radius, space } from '../../brand/theme';
import { Mascot } from '../../components/Mascot';
import { Chip, KindBadge, PressableScale, Screen, Txt } from '../../components/ui';
import { kindLabel, timeAgo } from '../../lib/format';
import { listCaptures } from '../../lib/store';
import type { Capture } from '../../lib/types';

const FILTERS = ['all', 'favorites', 'receipt', 'price_tag', 'document', 'note', 'screenshot', 'product', 'scene'] as const;

export default function LibraryScreen() {
  const params = useLocalSearchParams<{ q?: string }>();
  const [query, setQuery] = useState(params.q ?? '');
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all');
  const [items, setItems] = useState<Capture[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A tag tapped on a capture opens the library searching for it.
  const [lastQ, setLastQ] = useState(params.q);
  if (params.q !== lastQ) {
    setLastQ(params.q);
    if (params.q) setQuery(params.q);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const kind = filter === 'all' || filter === 'favorites' ? undefined : filter;
      const rows = await listCaptures({ search: query, kind });
      setItems(filter === 'favorites' ? rows.filter((r) => r.favorite) : rows);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [query, filter]);

  // Debounce typing; reload whenever the tab regains focus.
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  // Two-column masonry: place each card in the currently shorter column.
  const columns = useMemo(() => {
    const cols: [Capture[], Capture[]] = [[], []];
    const h = [0, 0];
    for (const c of items) {
      const est = 150 + (c.id.charCodeAt(0) % 3) * 40 + (c.summary ? 40 : 0);
      const i = h[0] <= h[1] ? 0 : 1;
      cols[i].push(c);
      h[i] += est;
    }
    return cols;
  }, [items]);

  return (
    <Screen>
      <View style={{ paddingHorizontal: space(5), paddingTop: space(3), gap: space(3) }}>
        <Txt variant="display" style={{ fontSize: 30 }}>
          Library
        </Txt>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(2), backgroundColor: colors.surface, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line, paddingHorizontal: space(4) }}>
          <Ionicons name="search" size={18} color={colors.textMute} />
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Search text, titles, tags… ابحث"
            placeholderTextColor={colors.textMute}
            style={{ flex: 1, color: colors.text, fontFamily: fonts.body, fontSize: 15, paddingVertical: 12 }}
            returnKeyType="search"
            autoCorrect={false}
          />
          {!!query && (
            <PressableScale onPress={() => setQuery('')}>
              <Ionicons name="close-circle" size={18} color={colors.textMute} />
            </PressableScale>
          )}
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space(2), paddingRight: space(5) }}>
          {FILTERS.map((f) => (
            <Chip
              key={f}
              label={f === 'all' ? 'All' : f === 'favorites' ? 'Favorites' : kindLabel[f]}
              icon={f === 'favorites' ? <Ionicons name="heart" size={12} color={colors.rose} /> : undefined}
              active={filter === f}
              onPress={() => setFilter(f)}
            />
          ))}
        </ScrollView>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: space(5), paddingBottom: 140 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.violet} />}
      >
        {error ? (
          <Txt variant="label" style={{ color: colors.rose }}>
            {error}
          </Txt>
        ) : items.length === 0 && !loading ? (
          <View style={{ alignItems: 'center', paddingTop: space(10), gap: space(2) }}>
            <Mascot size={120} mood={query ? 'sad' : 'idle'} />
            <Txt variant="heading">{query ? 'Nothing matches that' : 'Your memory is empty'}</Txt>
            <Txt variant="label" style={{ textAlign: 'center' }}>
              {query ? 'Try another word, in Arabic or English.' : 'Capture a photo and it will live here, searchable forever.'}
            </Txt>
          </View>
        ) : (
          <View style={{ flexDirection: 'row', gap: space(3) }}>
            {columns.map((col, ci) => (
              <View key={ci} style={{ flex: 1, gap: space(3) }}>
                {col.map((c, i) => (
                  <Animated.View key={c.id} entering={FadeInDown.delay((i * 2 + ci) * 50).springify().damping(16)} layout={LinearTransition.springify()}>
                    <MemoryCard c={c} tall={c.id.charCodeAt(0) % 3} />
                  </Animated.View>
                ))}
              </View>
            ))}
          </View>
        )}
      </ScrollView>
    </Screen>
  );
}

function MemoryCard({ c, tall }: { c: Capture; tall: number }) {
  return (
    <PressableScale
      onPress={() => router.push(`/capture/${c.id}`)}
      style={{ borderRadius: radius.lg, overflow: 'hidden', backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }}
    >
      <Image
        source={c.image_url ? { uri: c.image_url } : undefined}
        style={{ width: '100%', height: 130 + tall * 40, backgroundColor: colors.surfaceHigh }}
        contentFit="cover"
        transition={250}
      />
      {c.favorite && (
        <View style={{ position: 'absolute', top: 8, right: 8, backgroundColor: colors.ink + 'B3', borderRadius: 12, padding: 5 }}>
          <Ionicons name="heart" size={12} color={colors.rose} />
        </View>
      )}
      <View style={{ padding: space(3), gap: 6 }}>
        <KindBadge kind={c.kind} />
        <Txt variant="heading" style={{ fontSize: 14, lineHeight: 20 }} numberOfLines={2}>
          {c.title}
        </Txt>
        {!!c.summary && (
          <Txt variant="caption" numberOfLines={2}>
            {c.summary}
          </Txt>
        )}
        <Txt variant="caption" style={{ color: colors.textMute }}>
          {timeAgo(c.created_at)}
        </Txt>
      </View>
    </PressableScale>
  );
}
