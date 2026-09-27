import { Ionicons } from '@expo/vector-icons';
import type { BottomTabBarProps } from 'expo-router/js-tabs';
import { useEffect, useState } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, radius } from '../brand/theme';
import { PressableScale, Txt } from './ui';

const ICONS: Record<string, [keyof typeof Ionicons.glyphMap, keyof typeof Ionicons.glyphMap]> = {
  index: ['scan', 'scan-outline'],
  library: ['albums', 'albums-outline'],
  prices: ['pricetags', 'pricetags-outline'],
  lab: ['flask', 'flask-outline'],
};

// Floating pill tab bar; the highlight slides between tabs with a spring.
export function TabBar({ state, descriptors, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  const [width, setWidth] = useState(0);
  const tabWidth = width / state.routes.length;
  const x = useSharedValue(0);

  useEffect(() => {
    x.value = withSpring(state.index * tabWidth, { damping: 18, stiffness: 180 });
  }, [state.index, tabWidth, x]);

  const indicator = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));

  return (
    <View
      style={{
        position: 'absolute',
        left: 16,
        right: 16,
        bottom: Math.max(insets.bottom, 12),
        backgroundColor: colors.surfaceHigh,
        borderRadius: radius.pill,
        borderWidth: 1,
        borderColor: colors.line,
        padding: 6,
        shadowColor: '#000',
        shadowOpacity: 0.4,
        shadowRadius: 20,
        shadowOffset: { width: 0, height: 8 },
        elevation: 12,
      }}
    >
      <View style={{ flexDirection: 'row' }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
        {width > 0 && (
          <Animated.View
            style={[
              { position: 'absolute', top: 0, bottom: 0, width: tabWidth, borderRadius: radius.pill, backgroundColor: colors.violet + '33' },
              indicator,
            ]}
          />
        )}
        {state.routes.map((route, i) => {
          const focused = state.index === i;
          const { options } = descriptors[route.key];
          const [on, off] = ICONS[route.name] ?? ['ellipse', 'ellipse-outline'];
          return (
            <PressableScale
              key={route.key}
              accessibilityRole="button"
              accessibilityState={{ selected: focused }}
              onPress={() => {
                const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
                if (!focused && !event.defaultPrevented) navigation.navigate(route.name);
              }}
              style={{ flex: 1, alignItems: 'center', paddingVertical: 8, gap: 2 }}
            >
              <Ionicons name={focused ? on : off} size={21} color={focused ? colors.violetSoft : colors.textMute} />
              <Txt variant="caption" style={{ fontSize: 11, lineHeight: 14, color: focused ? colors.text : colors.textMute }}>
                {typeof options.title === 'string' ? options.title : route.name}
              </Txt>
            </PressableScale>
          );
        })}
      </View>
    </View>
  );
}
