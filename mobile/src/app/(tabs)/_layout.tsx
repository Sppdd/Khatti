import { Tabs } from 'expo-router/js-tabs';

import { colors } from '../../brand/theme';
import { TabBar } from '../../components/TabBar';

export default function TabsLayout() {
  return (
    <Tabs
      tabBar={(props) => <TabBar {...props} />}
      screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: colors.ink } }}
    >
      <Tabs.Screen name="index" options={{ title: 'Capture' }} />
      <Tabs.Screen name="library" options={{ title: 'Library' }} />
      <Tabs.Screen name="prices" options={{ title: 'Prices' }} />
      <Tabs.Screen name="lab" options={{ title: 'API Lab' }} />
    </Tabs>
  );
}
