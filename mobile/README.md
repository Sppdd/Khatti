# Khatti mobile (خطّي)

Expo app for iOS and Android: **every photo, turned into data.** Photograph a
receipt, a shelf tag, a note or a street scene. The Khatti API turns it into one
structured JSON record that you can search later, compare by price, or paste
into an AI tool.

<img src="brand/logo.png" width="96" alt="Khatti logo: the letter خ with eyes" />

| Tab | What it does |
|---|---|
| **Capture** | Pick a mode, then shoot or pick a photo. The animated خ reads it and files it. |
| **Library** | A mymind-style masonry library of everything captured. Search it in Arabic or English, filter by kind, mark favourites. |
| **Prices** | Every price from receipts and tags, grouped by product: latest, low/high, change, cheapest store. |
| **API Lab** | Set the API URL and key, check `/health`, run a KYC case against `/v1/kyc/cases`, and see the request log. |
| **Model bridge** (from API Lab) | Browse every model on Nebius Token Factory, Hugging Face and your own servers; pick the one that reads your photos; try chat and image models in a playground; host any Hugging Face model on a dedicated endpoint and pause, resume or delete it. |

Modes (`auto`, `prices`, `mind`, `prompt`, `text`) steer what the model focuses on.
Every capture has **Copy for AI** (compact JSON context). Prompt captures open a
**Prompt studio** with Universal, Midjourney, Stable Diffusion and Video variants,
plus the scene's colour palette.

## Run it

```bash
cd mobile
npm install
cp .env.example .env.local   # optional: API + Supabase, see below
npx expo start               # scan the QR code with Expo Go on iOS or Android
```

With no configuration the app starts in **demo mode**: sample API answers, and
data saved on the device. Everything else works, so you can try the whole app first.

Checks: `npm run typecheck` and `npm run lint`.

## Connect the Khatti API

The app calls `POST /v1/extract` (multipart: `file`, `mode`) on the Python API in
the repo root. Start the API with a vision model configured:

```bash
export KHATTI_TOKEN_FACTORY_KEY=...        # or KHATTI_EXTRACTOR='{"base_url":...,"model":...,"api_key":...}'
export KHATTI_READERS='[{"name":"vlm","base_url":"https://api.studio.nebius.com/v1","model":"Qwen/Qwen2.5-VL-72B-Instruct"}]'
export KHATTI_API_KEY=choose-a-secret      # optional; the app sends it as a Bearer token
uvicorn khatti.api:app --host 0.0.0.0 --port 8000
```

Then set `EXPO_PUBLIC_KHATTI_API_URL` (use your computer's LAN IP, for example
`http://192.168.1.20:8000`, not `localhost`), or type it in **API Lab** and turn
demo mode off. Every call is timed and written to the request log.

## Connect Supabase (login and saved data)

1. Create a Supabase project and run `../supabase/migrations/20260927000000_khatti_app.sql`
   in the SQL editor (or `supabase db push`). It creates `captures`, `price_items`,
   `api_logs`, row-level security, and a private `captures` storage bucket.
2. In **Authentication → Sign In / Providers**, enable **Anonymous sign-ins** (and Email for codes).
   For 6-digit codes, put `{{ .Token }}` in the Magic Link and Change Email templates.
3. Set `EXPO_PUBLIC_SUPABASE_URL` and `EXPO_PUBLIC_SUPABASE_ANON_KEY` and restart Expo.

Each person starts with an anonymous account, so there is no sign-up wall. In
**API Lab** they can attach an email to keep their data across devices. Photos go to
`captures/<user id>/<capture id>.jpg`; rows are visible only to their owner.

## Layout

```
src/app/               routes (Expo Router)
  (tabs)/              Capture, Library, Prices, API Lab + custom animated tab bar
  scan.tsx             processing screen: scan-line, thinking mascot, save
  capture/[id].tsx     result: prices, prompt studio, text, fields, tags, raw JSON
src/components/        Mascot (animated خ), TabBar, UI kit (squishy buttons, cards, chips)
src/lib/               api client, Supabase/device store, settings, image prep, formatting
brand/                 logo sources and brand notes (BRAND.md)
```

The response schema lives in `src/lib/types.ts` and mirrors `khatti/extract.py`.

## Ship it

Build with EAS: `npx eas-cli@latest build -p ios` / `-p android`. The bundle id and
package are `app.khatti.mobile`, set in `app.json`.
