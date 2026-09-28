import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export interface Settings {
  apiUrl: string;
  apiKey: string;
  // Demo mode answers with sample results instead of calling the API, for trying the UI offline.
  demoMode: boolean;
  // Bridged vision model for extraction, e.g. "nebius/Qwen/Qwen2.5-VL-72B-Instruct". Empty = server default.
  extractModel: string;
}

const KEY = 'khatti.settings.v1';

const defaults: Settings = {
  apiUrl: process.env.EXPO_PUBLIC_KHATTI_API_URL ?? '',
  apiKey: process.env.EXPO_PUBLIC_KHATTI_API_KEY ?? '',
  demoMode: !process.env.EXPO_PUBLIC_KHATTI_API_URL,
  extractModel: '',
};

// Read by non-React code (api.ts). Kept in sync by the provider.
let current: Settings = defaults;
export const getSettings = () => current;

interface Ctx {
  settings: Settings;
  ready: boolean;
  update: (patch: Partial<Settings>) => void;
}

const SettingsContext = createContext<Ctx>({ settings: defaults, ready: false, update: () => {} });

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(defaults);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((raw) => {
        if (raw) {
          current = { ...defaults, ...JSON.parse(raw) };
          setSettings(current);
        }
      })
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      settings,
      ready,
      update: (patch) => {
        current = { ...current, ...patch };
        setSettings(current);
        AsyncStorage.setItem(KEY, JSON.stringify(current)).catch(() => {});
      },
    }),
    [settings, ready],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export const useSettings = () => useContext(SettingsContext);
