-- Khatti mobile app: captures (photo -> structured data), price observations, API logs.
-- Every row belongs to the signed-in user (anonymous sign-in works); RLS keeps rows private.

create table if not exists public.captures (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  created_at timestamptz not null default now(),
  mode text not null,                 -- auto | prices | mind | prompt | text
  kind text not null default 'other', -- receipt | price_tag | menu | document | note | ...
  title text not null default '',
  summary text not null default '',
  language text,
  text text,
  tags text[] not null default '{}',
  data jsonb not null,                -- full ExtractResult from the Khatti API
  image_path text,                    -- object path in the "captures" storage bucket
  model text,
  latency_ms integer,
  favorite boolean not null default false
);

create index if not exists captures_user_created_idx on public.captures (user_id, created_at desc);
create index if not exists captures_tags_idx on public.captures using gin (tags);

create table if not exists public.price_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  capture_id uuid not null references public.captures (id) on delete cascade,
  observed_at timestamptz not null default now(),
  name text not null,
  normalized_name text not null,      -- lowercased, diacritics/spaces folded; groups the same product
  price numeric,
  currency text,
  quantity numeric,
  unit text,
  store text
);

create index if not exists price_items_user_name_idx on public.price_items (user_id, normalized_name, observed_at desc);

create table if not exists public.api_logs (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  created_at timestamptz not null default now(),
  endpoint text not null,
  status integer,
  ok boolean not null,
  latency_ms integer,
  error text,
  meta jsonb not null default '{}'
);

create index if not exists api_logs_user_created_idx on public.api_logs (user_id, created_at desc);

alter table public.captures enable row level security;
alter table public.price_items enable row level security;
alter table public.api_logs enable row level security;

create policy "own captures" on public.captures
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own price items" on public.price_items
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own api logs" on public.api_logs
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Private bucket; objects live under "<user id>/<capture id>.jpg".
insert into storage.buckets (id, name, public)
values ('captures', 'captures', false)
on conflict (id) do nothing;

create policy "own capture images read" on storage.objects
  for select using (bucket_id = 'captures' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "own capture images write" on storage.objects
  for insert with check (bucket_id = 'captures' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "own capture images delete" on storage.objects
  for delete using (bucket_id = 'captures' and (storage.foldername(name))[1] = auth.uid()::text);
