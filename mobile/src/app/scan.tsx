import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, useWindowDimensions, View } from 'react-native';
import Animated, {
  Easing,
  FadeIn,
  FadeInDown,
  FadeOut,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, radius, space } from '../brand/theme';
import { Mascot, type MascotMood } from '../components/Mascot';
import { Button, PressableScale, Txt } from '../components/ui';
import { extractPhoto } from '../lib/api';
import { prepareImage } from '../lib/image';
import { modeInfo } from '../lib/modes';
import { saveCapture } from '../lib/store';
import type { ExtractMode } from '../lib/types';

type Step = 'prepare' | 'extract' | 'save' | 'done' | 'error';

const STEP_TEXT: Record<Step, string> = {
  prepare: 'Getting the photo ready…',
  extract: 'Reading every pixel…',
  save: 'Filing it in your library…',
  done: 'Got it!',
  error: 'Hmm, that didn’t work',
};

const THINKING = ['Reading every pixel…', 'Spotting text and numbers…', 'Structuring the data…', 'Almost there…'];

export default function ScanScreen() {
  const params = useLocalSearchParams<{ uri: string; width: string; height: string; mode: ExtractMode }>();
  const { width: screenW, height: screenH } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const [step, setStep] = useState<Step>('prepare');
  const [tick, setTick] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const mode: ExtractMode = params.mode ?? 'auto';
  const info = modeInfo(mode);

  const frameH = Math.min(screenH * 0.52, screenW * 1.25);
  const sweep = useSharedValue(0);
  useEffect(() => {
    sweep.value = withRepeat(withTiming(1, { duration: 1600, easing: Easing.inOut(Easing.quad) }), -1, true);
  }, [sweep]);
  const sweepStyle = useAnimatedStyle(() => ({ transform: [{ translateY: sweep.value * (frameH - 4) }] }));

  const run = useCallback(async () => {
    try {
      const image = await prepareImage(params.uri, Number(params.width) || 0, Number(params.height) || 0);
      setStep('extract');
      const { data, latencyMs } = await extractPhoto(image, mode);
      if (!alive.current) return;
      setStep('save');
      const capture = await saveCapture({ result: data, image, latencyMs });
      if (!alive.current) return;
      setStep('done');
      if (Platform.OS !== 'web') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      setTimeout(() => alive.current && router.replace(`/capture/${capture.id}`), 1100);
    } catch (e) {
      if (!alive.current) return;
      setStep('error');
      setError((e as Error).message);
      if (Platform.OS !== 'web') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    }
  }, [params.uri, params.width, params.height, mode]);

  useEffect(() => {
    alive.current = true;
    // run() only sets state after its first await, so this does not render synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    run();
    return () => {
      alive.current = false;
    };
  }, [run]);

  const retry = () => {
    setError(null);
    setStep('prepare');
    run();
  };

  // Rotate what the mascot says while the API works.
  useEffect(() => {
    if (step !== 'extract') return;
    const t = setInterval(() => setTick((i) => i + 1), 1500);
    return () => clearInterval(t);
  }, [step]);
  const line = step === 'extract' ? THINKING[tick % THINKING.length] : STEP_TEXT[step];

  const mood: MascotMood = step === 'done' ? 'happy' : step === 'error' ? 'sad' : 'thinking';
  const busy = step === 'prepare' || step === 'extract' || step === 'save';

  return (
    <View style={{ flex: 1, backgroundColor: colors.ink, paddingTop: insets.top + space(2), paddingBottom: insets.bottom + space(4) }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: space(5) }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Ionicons name={info.icon} size={18} color={info.color} />
          <Txt variant="heading">{info.label} mode</Txt>
        </View>
        <PressableScale onPress={() => router.back()} style={{ padding: 8, borderRadius: radius.pill, backgroundColor: colors.surface }}>
          <Ionicons name="close" size={20} color={colors.text} />
        </PressableScale>
      </View>

      <Animated.View
        entering={FadeIn.duration(400)}
        style={{ marginHorizontal: space(5), marginTop: space(4), height: frameH, borderRadius: radius.xl, overflow: 'hidden', backgroundColor: colors.surface }}
      >
        <Image source={{ uri: params.uri }} style={{ flex: 1 }} contentFit="cover" />
        {busy && (
          <>
            <View style={{ ...absoluteFill, backgroundColor: colors.ink + '55' }} />
            <Animated.View style={[{ position: 'absolute', left: 0, right: 0, top: 0, height: 4 }, sweepStyle]}>
              <LinearGradient
                colors={['transparent', info.color + '66']}
                style={{ position: 'absolute', left: 0, right: 0, bottom: 2, height: 70 }}
              />
              <View style={{ height: 3, backgroundColor: info.color, shadowColor: info.color, shadowOpacity: 1, shadowRadius: 10, elevation: 6 }} />
            </Animated.View>
            <Corners color={info.color} />
          </>
        )}
        {step === 'done' && (
          <Animated.View entering={FadeIn} style={{ ...absoluteFill, backgroundColor: colors.mint + '33', alignItems: 'center', justifyContent: 'center' }}>
            <Ionicons name="checkmark-circle" size={72} color={colors.mint} />
          </Animated.View>
        )}
      </Animated.View>

      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: space(6) }}>
        <Mascot size={110} mood={mood} />
        <Animated.View key={line} entering={FadeInDown.duration(250)} exiting={FadeOut.duration(150)}>
          <Txt variant="heading" style={{ textAlign: 'center', marginTop: space(2) }}>
            {line}
          </Txt>
        </Animated.View>
        {error && (
          <Animated.View entering={FadeInDown} style={{ width: '100%', gap: space(3), marginTop: space(2) }}>
            <Txt variant="label" style={{ textAlign: 'center', color: colors.rose }}>
              {error}
            </Txt>
            <View style={{ flexDirection: 'row', gap: space(3) }}>
              <Button title="Close" variant="ghost" onPress={() => router.back()} style={{ flex: 1 }} />
              <Button title="Try again" onPress={retry} style={{ flex: 1 }} />
            </View>
          </Animated.View>
        )}
      </View>
    </View>
  );
}

const absoluteFill = { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 } as const;

function Corners({ color }: { color: string }) {
  const s = 28;
  const w = 3;
  const base = { position: 'absolute', width: s, height: s, borderColor: color } as const;
  return (
    <>
      <View style={[base, { top: 14, left: 14, borderTopWidth: w, borderLeftWidth: w, borderTopLeftRadius: 12 }]} />
      <View style={[base, { top: 14, right: 14, borderTopWidth: w, borderRightWidth: w, borderTopRightRadius: 12 }]} />
      <View style={[base, { bottom: 14, left: 14, borderBottomWidth: w, borderLeftWidth: w, borderBottomLeftRadius: 12 }]} />
      <View style={[base, { bottom: 14, right: 14, borderBottomWidth: w, borderRightWidth: w, borderBottomRightRadius: 12 }]} />
    </>
  );
}
