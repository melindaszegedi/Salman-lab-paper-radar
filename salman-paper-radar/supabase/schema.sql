-- Salman Lab Paper Radar: accounts and personal preferences.
-- Paste this whole file into Supabase: SQL Editor -> New query -> Run.
-- Safe to run again: it only creates what is missing.

create table if not exists public.profiles (
  id              uuid primary key references auth.users (id) on delete cascade,
  email           text,
  first_name      text not null default '',
  keywords        text[] not null default '{}',   -- free-text terms, e.g. 'TEER', 'claudin-5'
  topics          text[] not null default '{}',   -- topic ids from config.yaml (csvd, bbb, delivery, adtx, glymph)
  journals        text[] not null default '{}',   -- journal names to boost
  authors         text[] not null default '{}',   -- e.g. 'Salman M', 'Koenderink GH'
  digest          boolean not null default true,  -- morning email on/off
  digest_size     int not null default 8 check (digest_size between 3 and 25),
  last_digest_on  date,                            -- written by the daily email job only
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Each signed-in person can read and change only their own row.
drop policy if exists "own profile: read"   on public.profiles;
drop policy if exists "own profile: insert" on public.profiles;
drop policy if exists "own profile: update" on public.profiles;
create policy "own profile: read"   on public.profiles for select using (auth.uid() = id);
create policy "own profile: insert" on public.profiles for insert with check (auth.uid() = id);
create policy "own profile: update" on public.profiles for update using (auth.uid() = id) with check (auth.uid() = id);

-- From the browser people may only change their preferences, never the email copy or the
-- job's bookkeeping column (column grants, because a table-wide grant would override them).
revoke all on public.profiles from anon;
revoke insert, update on public.profiles from authenticated;
grant select on public.profiles to authenticated;
grant insert (id, first_name, keywords, topics, journals, authors, digest, digest_size) on public.profiles to authenticated;
grant update (first_name, keywords, topics, journals, authors, digest, digest_size) on public.profiles to authenticated;

-- Keep lists a sensible size so one account cannot blow up the daily PubMed search.
create or replace function public.profiles_guard() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  new.first_name := left(trim(new.first_name), 60);
  if coalesce(array_length(new.keywords, 1), 0) > 40 then raise exception 'At most 40 keywords'; end if;
  if coalesce(array_length(new.journals, 1), 0) > 60 then raise exception 'At most 60 journals'; end if;
  if coalesce(array_length(new.authors,  1), 0) > 40 then raise exception 'At most 40 authors'; end if;
  return new;
end $$;
drop trigger if exists profiles_guard on public.profiles;
create trigger profiles_guard before insert or update on public.profiles
  for each row execute function public.profiles_guard();

-- Create the profile row automatically when someone signs up.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, first_name)
  values (new.id, new.email, coalesce(new.raw_user_meta_data ->> 'first_name', ''))
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Keep the email copy in step if someone changes their login email.
create or replace function public.handle_user_email() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  update public.profiles set email = new.email where id = new.id;
  return new;
end $$;
drop trigger if exists on_auth_user_email on auth.users;
create trigger on_auth_user_email after update of email on auth.users
  for each row execute function public.handle_user_email();
