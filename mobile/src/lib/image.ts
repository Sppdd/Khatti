import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';

export interface PreparedImage {
  uri: string;
  base64: string;
  width: number;
  height: number;
}

const MAX_EDGE = 1600;

// Downscale and re-encode as JPEG: keeps uploads small while text stays readable.
export async function prepareImage(uri: string, width: number, height: number): Promise<PreparedImage> {
  const ctx = ImageManipulator.manipulate(uri);
  if (Math.max(width, height) > MAX_EDGE) {
    ctx.resize(width >= height ? { width: MAX_EDGE } : { height: MAX_EDGE });
  }
  const ref = await ctx.renderAsync();
  const out = await ref.saveAsync({ compress: 0.75, format: SaveFormat.JPEG, base64: true });
  return { uri: out.uri, base64: out.base64 ?? '', width: out.width, height: out.height };
}
