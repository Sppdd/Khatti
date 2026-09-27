import * as Haptics from 'expo-haptics';
import { LinearGradient } from 'expo-linear-gradient';
import type { ReactNode } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  Text,
  View,
  type PressableProps,
  type StyleProp,
  type TextProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, fonts, gradients, kindColor, radius, space } from '../brand/theme';
import { kindLabel } from '../lib/format';

export const tap = (style: Haptics.ImpactFeedbackStyle = Haptics.ImpactFeedbackStyle.Light) => {
  if (Platform.OS !== 'web') Haptics.impactAsync(style).catch(() => {});
};

type Variant = 'display' | 'title' | 'heading' | 'body' | 'label' | 'caption' | 'mono';

const variantStyle: Record<Variant, TextStyle> = {
  display: { fontFamily: fonts.brand, fontSize: 34, lineHeight: 44, color: colors.text },
  title: { fontFamily: fonts.bodyBold, fontSize: 24, lineHeight: 34, color: colors.text },
  heading: { fontFamily: fonts.bodyBold, fontSize: 17, lineHeight: 26, color: colors.text },
  body: { fontFamily: fonts.body, fontSize: 15, lineHeight: 24, color: colors.text },
  label: { fontFamily: fonts.bodyMedium, fontSize: 13, lineHeight: 20, color: colors.textDim },
  caption: { fontFamily: fonts.body, fontSize: 12, lineHeight: 18, color: colors.textMute },
  mono: { fontFamily: Platform.select({ ios: 'Menlo', default: 'monospace' }), fontSize: 12, lineHeight: 18, color: colors.textDim },
};

export function Txt({ variant = 'body', style, ...rest }: TextProps & { variant?: Variant }) {
  return <Text {...rest} style={[variantStyle[variant], style]} />;
}

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

// Every tappable thing in Khatti gives a little squish and a haptic tick.
export function PressableScale({
  children,
  style,
  onPress,
  haptic = true,
  scaleTo = 0.96,
  ...rest
}: PressableProps & { style?: StyleProp<ViewStyle>; haptic?: boolean; scaleTo?: number; children?: ReactNode }) {
  const scale = useSharedValue(1);
  const anim = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }));
  return (
    <AnimatedPressable
      {...rest}
      onPressIn={() => scale.set(withSpring(scaleTo, { damping: 15, stiffness: 400 }))}
      onPressOut={() => scale.set(withSpring(1, { damping: 12, stiffness: 300 }))}
      onPress={(e) => {
        if (haptic) tap();
        onPress?.(e);
      }}
      style={[style, anim]}
    >
      {children}
    </AnimatedPressable>
  );
}

export function Button({
  title,
  onPress,
  icon,
  variant = 'primary',
  loading,
  disabled,
  style,
}: {
  title: string;
  onPress?: () => void;
  icon?: ReactNode;
  variant?: 'primary' | 'mint' | 'ghost' | 'danger';
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const inner = (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space(2) }}>
      {loading ? <ActivityIndicator color={variant === 'mint' ? colors.ink : colors.text} /> : icon}
      <Txt
        variant="heading"
        numberOfLines={1}
        style={{ fontSize: 15, color: variant === 'mint' ? colors.ink : variant === 'danger' ? colors.rose : colors.text }}
      >
        {title}
      </Txt>
    </View>
  );
  const base: ViewStyle = { borderRadius: radius.pill, paddingVertical: 14, paddingHorizontal: space(5), overflow: 'hidden' };
  return (
    <PressableScale
      onPress={onPress}
      disabled={disabled || loading}
      style={[{ opacity: disabled ? 0.5 : 1, borderRadius: radius.pill }, style]}
    >
      {variant === 'primary' || variant === 'mint' ? (
        <LinearGradient
          colors={variant === 'primary' ? gradients.brand : gradients.mint}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={base}
        >
          {inner}
        </LinearGradient>
      ) : (
        <View
          style={[
            base,
            { borderWidth: 1, borderColor: variant === 'danger' ? colors.rose + '66' : colors.line, backgroundColor: colors.surface },
          ]}
        >
          {inner}
        </View>
      )}
    </PressableScale>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <View
      style={[
        {
          backgroundColor: colors.surface,
          borderRadius: radius.lg,
          borderWidth: 1,
          borderColor: colors.line,
          padding: space(4),
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

export function Chip({
  label,
  active,
  onPress,
  color = colors.violet,
  icon,
}: {
  label: string;
  active?: boolean;
  onPress?: () => void;
  color?: string;
  icon?: ReactNode;
}) {
  return (
    <PressableScale
      onPress={onPress}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
        paddingHorizontal: 14,
        paddingVertical: 8,
        borderRadius: radius.pill,
        borderWidth: 1,
        borderColor: active ? color : colors.line,
        backgroundColor: active ? color + '26' : colors.surface,
      }}
    >
      {icon}
      <Txt variant="label" style={{ color: active ? colors.text : colors.textDim }}>
        {label}
      </Txt>
    </PressableScale>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  const c = kindColor[kind] ?? colors.textDim;
  return (
    <View
      style={{
        alignSelf: 'flex-start',
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
        paddingHorizontal: 10,
        paddingVertical: 3,
        borderRadius: radius.pill,
        backgroundColor: c + '22',
      }}
    >
      <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: c }} />
      <Txt variant="caption" style={{ color: c, fontFamily: fonts.bodyMedium }}>
        {kindLabel[kind] ?? kind}
      </Txt>
    </View>
  );
}

export function Screen({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <SafeAreaView edges={['top']} style={[{ flex: 1, backgroundColor: colors.ink }, style]}>
      {children}
    </SafeAreaView>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: space(2) }}>
      <Txt variant="label" style={{ textTransform: 'uppercase', letterSpacing: 1.2, fontSize: 11 }}>
        {children}
      </Txt>
      {right}
    </View>
  );
}
