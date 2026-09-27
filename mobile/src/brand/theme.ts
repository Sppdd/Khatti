// Khatti brand tokens. Dark-first: ink background, violet Kha, mint dot.
export const colors = {
  ink: '#0D0B16',
  surface: '#16131F',
  surfaceHigh: '#201C2C',
  line: '#2E2940',
  text: '#F4F1FF',
  textDim: '#A39DB8',
  textMute: '#6E6886',
  violet: '#7C5CFF',
  violetDeep: '#4B2FD6',
  violetSoft: '#B7A6FF',
  mint: '#2EE6A6',
  saffron: '#FFB547',
  rose: '#FF5C7A',
  sky: '#4CC9F0',
} as const;

export const gradients = {
  brand: ['#8B6CFF', '#4B2FD6'] as const,
  mint: ['#2EE6A6', '#12B886'] as const,
  sunset: ['#FFB547', '#FF5C7A'] as const,
  night: ['#1B1630', '#0D0B16'] as const,
};

export const radius = { sm: 10, md: 16, lg: 24, xl: 32, pill: 999 } as const;
export const space = (n: number) => n * 4;

export const fonts = {
  brand: 'ReemKufi_700Bold',
  brandMedium: 'ReemKufi_500Medium',
  body: 'Cairo_400Regular',
  bodyMedium: 'Cairo_600SemiBold',
  bodyBold: 'Cairo_700Bold',
} as const;

// Accent per capture kind, used by badges and library cards.
export const kindColor: Record<string, string> = {
  receipt: colors.saffron,
  price_tag: colors.saffron,
  menu: colors.saffron,
  document: colors.sky,
  note: colors.violetSoft,
  screenshot: colors.sky,
  product: colors.mint,
  scene: colors.rose,
  other: colors.textDim,
};
