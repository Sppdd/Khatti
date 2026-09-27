// "Khatti", the living خ. The letter's body is the character, its dot is its spark,
// and the eyes sit in the bowl of the letter.
import { useEffect, useId } from 'react';
import { View, type ViewStyle } from 'react-native';
import Animated, {
  Easing,
  cancelAnimation,
  useAnimatedProps,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import Svg, { Circle, Defs, Ellipse, G, LinearGradient, Path, RadialGradient, Stop } from 'react-native-svg';

import { colors } from '../brand/theme';

export type MascotMood = 'idle' | 'thinking' | 'happy' | 'sad';

const AnimatedPath = Animated.createAnimatedComponent(Path);
const AnimatedCircle = Animated.createAnimatedComponent(Circle);
const AnimatedEllipse = Animated.createAnimatedComponent(Ellipse);

export const KHA_PATH = 'M27 37 Q28 31 33 33 L70 33 L44 52 Q27 62 32 76 Q38 88 58 85 Q70 83 76 76';
const PATH_LENGTH = 190;
const EYES = [46, 58];
const EYE_Y = 67;

// Gradient ids must be unique per instance: on web every <svg> shares one document.
const useSvgId = (prefix: string) => prefix + useId().replace(/[^a-zA-Z0-9]/g, '');

export function Mascot({ size = 160, mood = 'idle', style }: { size?: number; mood?: MascotMood; style?: ViewStyle }) {
  const khaId = useSvgId('kha');
  const glowId = useSvgId('glow');
  const draw = useSharedValue(PATH_LENGTH);
  const float = useSharedValue(0);
  const tilt = useSharedValue(0);
  const squash = useSharedValue(1);
  const blink = useSharedValue(1);
  const orbit = useSharedValue(0); // 0..1 around the head while thinking
  const hop = useSharedValue(0); // dot height offset
  const lookX = useSharedValue(0);
  const lookY = useSharedValue(0);
  const glow = useSharedValue(0);

  // Write itself on first appearance, then blink forever.
  useEffect(() => {
    draw.value = withTiming(0, { duration: 1100, easing: Easing.out(Easing.cubic) });
    blink.value = withRepeat(
      withSequence(
        withDelay(2600, withTiming(0.1, { duration: 70 })),
        withTiming(1, { duration: 90 }),
        withDelay(180, withTiming(0.1, { duration: 70 })),
        withTiming(1, { duration: 90 }),
      ),
      -1,
    );
  }, [blink, draw]);

  useEffect(() => {
    [float, tilt, squash, orbit, hop, lookX, lookY, glow].forEach((v) => cancelAnimation(v));
    const loop = (from: number, to: number, duration: number) =>
      withRepeat(withSequence(withTiming(to, { duration, easing: Easing.inOut(Easing.sin) }), withTiming(from, { duration, easing: Easing.inOut(Easing.sin) })), -1);

    orbit.value = 0;
    if (mood === 'idle') {
      float.value = loop(0, -6, 1600);
      tilt.value = withSpring(0);
      squash.value = withSpring(1);
      hop.value = loop(0, -4, 900);
      lookX.value = withRepeat(
        withSequence(withDelay(1800, withTiming(1.2, { duration: 300 })), withDelay(1400, withTiming(-1, { duration: 300 })), withDelay(1200, withTiming(0, { duration: 300 }))),
        -1,
      );
      lookY.value = withTiming(0);
      glow.value = withTiming(0.25, { duration: 600 });
    } else if (mood === 'thinking') {
      float.value = loop(0, -3, 700);
      tilt.value = loop(-5, 5, 900);
      squash.value = withSpring(1);
      orbit.value = withRepeat(withTiming(1, { duration: 1100, easing: Easing.linear }), -1);
      hop.value = withTiming(0);
      lookX.value = loop(-1.3, 1.3, 550);
      lookY.value = withTiming(-1.2);
      glow.value = loop(0.2, 0.7, 700);
    } else if (mood === 'happy') {
      tilt.value = withSpring(0);
      float.value = withRepeat(withSequence(withTiming(-14, { duration: 220, easing: Easing.out(Easing.quad) }), withTiming(0, { duration: 260, easing: Easing.in(Easing.quad) })), 3);
      squash.value = withRepeat(withSequence(withTiming(1.06, { duration: 220 }), withTiming(0.9, { duration: 130 }), withSpring(1)), 3);
      hop.value = withRepeat(withSequence(withTiming(-12, { duration: 240 }), withSpring(0)), 3);
      lookX.value = withTiming(0);
      lookY.value = withTiming(0);
      glow.value = withSequence(withTiming(0.9, { duration: 250 }), withTiming(0.3, { duration: 900 }));
    } else {
      float.value = withTiming(4, { duration: 500 });
      tilt.value = withSpring(-8);
      squash.value = withTiming(0.96);
      hop.value = withSpring(7, { damping: 6 });
      lookX.value = withTiming(-0.6);
      lookY.value = withTiming(1.4);
      glow.value = withTiming(0.1);
    }
  }, [mood, float, tilt, squash, orbit, hop, lookX, lookY, glow]);

  const bodyStyle = useAnimatedStyle(() => ({
    transform: [
      { translateY: float.value },
      { rotate: `${tilt.value}deg` },
      { scaleX: 2 - squash.value },
      { scaleY: squash.value },
    ],
  }));

  const glowStyle = useAnimatedStyle(() => ({
    opacity: glow.value,
    transform: [{ scale: 0.85 + glow.value * 0.3 }],
  }));

  const strokeProps = useAnimatedProps(() => ({ strokeDashoffset: draw.value }));

  const dotProps = useAnimatedProps(() => {
    const a = orbit.value * Math.PI * 2;
    const r = orbit.value === 0 ? 0 : 1;
    return {
      cx: 50 + Math.cos(a) * 10 * r,
      cy: 19 + hop.value + Math.sin(a) * 4 * r,
    };
  });

  const eyeProps = useAnimatedProps(() => ({ ry: 4.6 * blink.value }));
  const pupilLeft = useAnimatedProps(() => ({ cx: EYES[0] + lookX.value, cy: EYE_Y + 0.6 + lookY.value, r: 2.3 * Math.min(1, blink.value * 1.6) }));
  const pupilRight = useAnimatedProps(() => ({ cx: EYES[1] + lookX.value, cy: EYE_Y + 0.6 + lookY.value, r: 2.3 * Math.min(1, blink.value * 1.6) }));

  return (
    <View style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}>
      <Animated.View style={[{ position: 'absolute', width: size * 1.3, height: size * 1.3 }, glowStyle]}>
        <Svg width="100%" height="100%" viewBox="0 0 100 100">
          <Defs>
            <RadialGradient id={glowId} cx="50" cy="50" r="50" gradientUnits="userSpaceOnUse">
              <Stop offset="0" stopColor={colors.violet} stopOpacity={0.9} />
              <Stop offset="0.6" stopColor={colors.violet} stopOpacity={0.25} />
              <Stop offset="1" stopColor={colors.violet} stopOpacity={0} />
            </RadialGradient>
          </Defs>
          <Circle cx={50} cy={50} r={50} fill={`url(#${glowId})`} />
        </Svg>
      </Animated.View>
      <Animated.View style={[{ width: size, height: size }, bodyStyle]}>
        <Svg width={size} height={size} viewBox="0 0 100 100">
          <Defs>
            <LinearGradient id={khaId} x1="0" y1="0" x2="1" y2="1">
              <Stop offset="0" stopColor="#B7A6FF" />
              <Stop offset="0.55" stopColor={colors.violet} />
              <Stop offset="1" stopColor="#5B3BEA" />
            </LinearGradient>
          </Defs>
          <AnimatedPath
            d={KHA_PATH}
            fill="none"
            stroke={`url(#${khaId})`}
            strokeWidth={9}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={PATH_LENGTH}
            animatedProps={strokeProps}
          />
          <AnimatedCircle r={6} fill={colors.mint} animatedProps={dotProps} />
          {mood === 'happy' ? (
            <G stroke={colors.text} strokeWidth={2.4} strokeLinecap="round" fill="none">
              <Path d={`M${EYES[0] - 3.5} ${EYE_Y + 1.5} Q${EYES[0]} ${EYE_Y - 3.5} ${EYES[0] + 3.5} ${EYE_Y + 1.5}`} />
              <Path d={`M${EYES[1] - 3.5} ${EYE_Y + 1.5} Q${EYES[1]} ${EYE_Y - 3.5} ${EYES[1] + 3.5} ${EYE_Y + 1.5}`} />
              <Circle cx={EYES[0] - 4} cy={EYE_Y + 6} r={2.2} fill={colors.rose} opacity={0.45} stroke="none" />
              <Circle cx={EYES[1] + 4} cy={EYE_Y + 6} r={2.2} fill={colors.rose} opacity={0.45} stroke="none" />
            </G>
          ) : (
            <G>
              {EYES.map((x) => (
                <AnimatedEllipse key={x} cx={x} cy={EYE_Y} rx={4.2} fill={colors.text} animatedProps={eyeProps} />
              ))}
              <AnimatedCircle fill={colors.ink} animatedProps={pupilLeft} />
              <AnimatedCircle fill={colors.ink} animatedProps={pupilRight} />
              {mood === 'sad' && (
                <G stroke={colors.textDim} strokeWidth={1.6} strokeLinecap="round">
                  <Path d={`M${EYES[0] - 4} ${EYE_Y - 7} L${EYES[0] + 2} ${EYE_Y - 5}`} />
                  <Path d={`M${EYES[1] + 4} ${EYE_Y - 7} L${EYES[1] - 2} ${EYE_Y - 5}`} />
                </G>
              )}
            </G>
          )}
        </Svg>
      </Animated.View>
    </View>
  );
}

// Static brand mark (app icon look) for headers and splash-like moments.
export function KhaMark({ size = 36 }: { size?: number }) {
  const bgId = useSvgId('markbg');
  return (
    <Svg width={size} height={size} viewBox="0 0 100 100">
      <Defs>
        <LinearGradient id={bgId} x1="0" y1="0" x2="1" y2="1">
          <Stop offset="0" stopColor="#8B6CFF" />
          <Stop offset="1" stopColor="#4B2FD6" />
        </LinearGradient>
      </Defs>
      <Path d="M24 0 H76 A24 24 0 0 1 100 24 V76 A24 24 0 0 1 76 100 H24 A24 24 0 0 1 0 76 V24 A24 24 0 0 1 24 0 Z" fill={`url(#${bgId})`} />
      <Path d={KHA_PATH} fill="none" stroke="#fff" strokeWidth={8.5} strokeLinecap="round" strokeLinejoin="round" />
      <Circle cx={50} cy={19} r={6} fill={colors.mint} />
      <Circle cx={46} cy={68} r={3.4} fill="#fff" />
      <Circle cx={58} cy={68} r={3.4} fill="#fff" />
    </Svg>
  );
}
