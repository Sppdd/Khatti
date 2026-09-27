import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { ScrollView, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, FadeInUp } from 'react-native-reanimated';

import { colors, fonts, radius, space } from '../../brand/theme';
import { KhaMark, Mascot } from '../../components/Mascot';
import { Button, Card, KindBadge, PressableScale, Screen, SectionTitle, Txt } from '../../components/ui';
import { timeAgo } from '../../lib/format';
import { MODES, modeInfo } from '../../lib/modes';
import { pickPhoto } from '../../lib/pick';
import { useSettings } from '../../lib/settings';
import { isCloud, listCaptures } from '../../lib/store';
import type { Capture, ExtractMode } from '../../lib/types';

export default function CaptureScreen() {
  const { settings } = useSettings();
  const [mode, setMode] = useState<ExtractMode>('auto');
  const [recent, setRecent] = useState<Capture[]>([]);
  const info = modeInfo(mode);

  useFocusEffect(
    useCallback(() => {
      listCaptures()
        .then((c) => setRecent(c.slice(0, 8)))
        .catch(() => {});
    }, []),
  );

  const start = async (source: 'camera' | 'library') => {
    const photo = await pickPhoto(source);
    if (!photo) return;
    router.push({
      pathname: '/scan',
      params: { uri: photo.uri, width: String(photo.width), height: String(photo.height), mode },
    });
  };

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space(5), paddingBottom: 140 }} showsVerticalScrollIndicator={false}>
        <Animated.View entering={FadeInDown.duration(500)} style={{ flexDirection: 'row', alignItems: 'center', gap: space(3) }}>
          <KhaMark size={40} />
          <View style={{ flex: 1 }}>
            <Txt style={{ fontFamily: fonts.brand, fontSize: 22, lineHeight: 28 }}>
              Khatti <Txt style={{ fontFamily: fonts.brand, fontSize: 20, color: colors.violetSoft }}>خطّي</Txt>
            </Txt>
            <Txt variant="caption">Every photo, turned into data</Txt>
          </View>
          <StatusPill label={settings.demoMode ? 'Demo' : 'Live'} color={settings.demoMode ? colors.saffron : colors.mint} />
          <StatusPill label={isCloud ? 'Cloud' : 'Device'} color={isCloud ? colors.sky : colors.textDim} />
        </Animated.View>

        <View style={{ alignItems: 'center', marginTop: space(5) }}>
          <Animated.View
            key={mode}
            entering={FadeInUp.springify().damping(14)}
            style={{
              backgroundColor: colors.surfaceHigh,
              borderRadius: radius.lg,
              borderWidth: 1,
              borderColor: colors.line,
              paddingHorizontal: space(4),
              paddingVertical: space(3),
              maxWidth: 300,
            }}
          >
            <Txt style={{ textAlign: 'center' }}>{info.hello}</Txt>
          </Animated.View>
          <View style={{ width: 14, height: 14, backgroundColor: colors.surfaceHigh, transform: [{ rotate: '45deg' }], marginTop: -8, borderRightWidth: 1, borderBottomWidth: 1, borderColor: colors.line }} />
          <Mascot size={140} mood="idle" />
        </View>

        <View style={{ height: space(4) }} />
        <SectionTitle>What should I look for?</SectionTitle>
        <View style={{ flexDirection: 'row', gap: space(2) }}>
          {MODES.map((m, i) => {
            const active = m.id === mode;
            return (
              <Animated.View key={m.id} entering={FadeInDown.delay(70 * i).springify()} style={{ flex: 1 }}>
                <PressableScale
                  onPress={() => setMode(m.id)}
                  accessibilityLabel={`${m.label} mode: ${m.blurb}`}
                  style={{
                    alignItems: 'center',
                    gap: 2,
                    borderRadius: radius.md,
                    borderWidth: 1.5,
                    borderColor: active ? m.color : colors.line,
                    backgroundColor: active ? m.color + '1F' : colors.surface,
                    paddingVertical: space(3),
                  }}
                >
                  <Ionicons name={m.icon} size={22} color={active ? m.color : colors.textDim} />
                  <Txt variant="label" style={{ color: active ? colors.text : colors.textDim, fontSize: 12 }}>
                    {m.label}
                  </Txt>
                  <Txt style={{ fontFamily: fonts.brandMedium, fontSize: 11, lineHeight: 16, color: active ? m.color : colors.textMute }}>{m.ar}</Txt>
                </PressableScale>
              </Animated.View>
            );
          })}
        </View>
        <Animated.View key={`blurb-${mode}`} entering={FadeIn.duration(300)}>
          <Txt variant="caption" style={{ textAlign: 'center', marginTop: space(2) }}>
            {info.label} · {info.blurb}
          </Txt>
        </Animated.View>

        <Animated.View entering={FadeIn.delay(400)} style={{ flexDirection: 'row', gap: space(3), marginTop: space(4) }}>
          <Button
            title="Camera"
            icon={<Ionicons name="camera" size={20} color={colors.text} />}
            onPress={() => start('camera')}
            style={{ flex: 1 }}
          />
          <Button
            title="Photos"
            variant="ghost"
            icon={<Ionicons name="images" size={20} color={colors.text} />}
            onPress={() => start('library')}
            style={{ flex: 1 }}
          />
        </Animated.View>

        <View style={{ marginTop: space(8) }}>
          <SectionTitle
            right={
              recent.length > 0 && (
                <PressableScale onPress={() => router.navigate('/library')}>
                  <Txt variant="label" style={{ color: colors.violetSoft }}>
                    See all
                  </Txt>
                </PressableScale>
              )
            }
          >
            Recent
          </SectionTitle>
          {recent.length === 0 ? (
            <Card style={{ alignItems: 'center', paddingVertical: space(6) }}>
              <Txt variant="label">Nothing yet. Your first capture will land here.</Txt>
            </Card>
          ) : (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space(3) }}>
              {recent.map((c, i) => (
                <Animated.View key={c.id} entering={FadeInDown.delay(60 * i)}>
                  <PressableScale
                    onPress={() => router.push(`/capture/${c.id}`)}
                    style={{ width: 150, borderRadius: radius.md, overflow: 'hidden', backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }}
                  >
                    <Image source={c.image_url ? { uri: c.image_url } : undefined} style={{ width: 150, height: 110, backgroundColor: colors.surfaceHigh }} contentFit="cover" transition={250} />
                    <View style={{ padding: space(2), gap: 4 }}>
                      <KindBadge kind={c.kind} />
                      <Txt variant="label" numberOfLines={1} style={{ color: colors.text }}>
                        {c.title}
                      </Txt>
                      <Txt variant="caption">{timeAgo(c.created_at)}</Txt>
                    </View>
                  </PressableScale>
                </Animated.View>
              ))}
            </ScrollView>
          )}
        </View>
      </ScrollView>
    </Screen>
  );
}

function StatusPill({ label, color }: { label: string; color: string }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8, paddingVertical: 3, borderRadius: radius.pill, backgroundColor: color + '1F' }}>
      <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color }} />
      <Txt variant="caption" style={{ color, fontFamily: fonts.bodyMedium, fontSize: 11 }}>
        {label}
      </Txt>
    </View>
  );
}
