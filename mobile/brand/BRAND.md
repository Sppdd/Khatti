# Khatti brand

**Name:** Khatti — خطّي, "my handwriting / my line". The app reads the lines of the
real world (receipts, labels, notes, scenes) and writes them down as data.

**Tagline:** Every photo, turned into data. · كل صورة… بيانات

## Mark

The letter **خ (Kha)** drawn as one continuous stroke. Its dot is the mint "spark",
and two eyes sit in the bowl of the letter, so the logo doubles as the character.

- `mark.svg`: rounded app mark (logo)
- `icon.svg`, `adaptive-*.svg`, `splash.svg`: sources for `../assets/*.png`
- In the app, `src/components/Mascot.tsx` draws the same path, animated.

## Character

Khatti the خ has four moods, all driven by Reanimated:

| Mood | Where | Motion |
|---|---|---|
| idle | Capture, empty states | floats, dot bobs, blinks, glances around |
| thinking | while the API works | tilts, the dot orbits the head, eyes look up, glow pulses |
| happy | capture saved | hops three times, `^ ^` eyes, blush |
| sad | errors, no results | droops, the dot falls, worried brows |

The stroke writes itself in on first appearance.

## Colour

| Token | Hex | Use |
|---|---|---|
| Ink | `#0D0B16` | background |
| Surface | `#16131F` | cards |
| Kha violet | `#7C5CFF` → `#4B2FD6` | brand gradient, primary actions |
| Spark mint | `#2EE6A6` | the dot, success |
| Saffron | `#FFB547` | prices |
| Rose | `#FF5C7A` | prompts, errors |
| Sky | `#4CC9F0` | documents, text |

## Type

- **Reem Kufi**: wordmark, display numbers, Arabic mode labels
- **Cairo**: all UI text (Arabic and Latin in one family)
