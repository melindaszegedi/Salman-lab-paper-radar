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

-- =====================================================================================
-- Added later: tracked authors and lab topics. Running the whole file again is safe.
-- =====================================================================================

-- Tracked authors (found through the author search) and which of their papers each person has seen.
alter table public.profiles add column if not exists tracked_authors jsonb not null default '[]'::jsonb;
alter table public.profiles add column if not exists author_seen     jsonb not null default '{}'::jsonb;
grant update (tracked_authors, author_seen) on public.profiles to authenticated;

create or replace function public.profiles_guard_more() returns trigger language plpgsql as $$
begin
  if jsonb_typeof(new.tracked_authors) <> 'array' or jsonb_array_length(new.tracked_authors) > 50 then
    raise exception 'At most 50 tracked authors';
  end if;
  if pg_column_size(new.author_seen) > 20000 then raise exception 'author_seen too large'; end if;
  return new;
end $$;
drop trigger if exists profiles_guard_more on public.profiles;
create trigger profiles_guard_more before insert or update on public.profiles
  for each row execute function public.profiles_guard_more();

-- Lab topics that members add. Everyone signed in can see and pick them; the daily collector
-- searches PubMed and the preprint servers for their keywords.
create table if not exists public.topics (
  id              text primary key,                        -- short slug, e.g. 'organoids'
  name            text not null,
  keywords        text[] not null default '{}',
  created_by      uuid references auth.users (id) on delete set null default auth.uid(),
  created_by_name text not null default '',
  created_at      timestamptz not null default now()
);
alter table public.topics enable row level security;

drop policy if exists "topics: read"   on public.topics;
drop policy if exists "topics: add"    on public.topics;
drop policy if exists "topics: remove" on public.topics;
create policy "topics: read"   on public.topics for select to authenticated using (true);
create policy "topics: add"    on public.topics for insert to authenticated with check (created_by = auth.uid());
create policy "topics: remove" on public.topics for delete to authenticated using (created_by = auth.uid());

revoke all on public.topics from anon;
revoke insert, update, delete on public.topics from authenticated;
grant select on public.topics to authenticated;
grant insert (id, name, keywords, created_by_name) on public.topics to authenticated;
grant delete on public.topics to authenticated;

create or replace function public.topics_guard() returns trigger language plpgsql as $$
begin
  new.id   := left(regexp_replace(lower(new.id), '[^a-z0-9-]', '', 'g'), 40);
  new.name := left(trim(new.name), 60);
  new.created_by_name := left(trim(new.created_by_name), 60);
  new.keywords := array(select left(trim(k), 60) from unnest(new.keywords) k where length(trim(k)) >= 2);
  if length(new.id) < 2 or length(new.name) < 2 then raise exception 'Topic needs a name'; end if;
  if new.id in ('csvd', 'bbb', 'delivery', 'adtx', 'glymph') then raise exception 'That topic already exists'; end if;
  if coalesce(array_length(new.keywords, 1), 0) < 1 or array_length(new.keywords, 1) > 12 then
    raise exception 'Give a topic between 1 and 12 keywords';
  end if;
  if (select count(*) from public.topics) >= 60 then raise exception 'The lab already has 60 topics'; end if;
  return new;
end $$;
drop trigger if exists topics_guard on public.topics;
create trigger topics_guard before insert on public.topics
  for each row execute function public.topics_guard();

-- =====================================================================================
-- Added later: weekly email, journal club queue, comments and reactions. Safe to re-run.
-- =====================================================================================

-- Morning email: daily (default) or weekly on Mondays.
alter table public.profiles add column if not exists digest_weekly boolean not null default false;
grant update (digest_weekly) on public.profiles to authenticated;

-- Journal club queue: one entry per paper, shared by the whole lab.
create table if not exists public.club (
  paper_id       text primary key,
  paper          jsonb not null,                       -- title, link, journal, date (a snapshot for the list)
  note           text not null default '',
  added_by       uuid references auth.users (id) on delete set null default auth.uid(),
  added_by_name  text not null default '',
  created_at     timestamptz not null default now()
);
-- Comments on papers.
create table if not exists public.comments (
  id           bigint generated always as identity primary key,
  paper_id     text not null,
  body         text not null,
  author       uuid references auth.users (id) on delete cascade default auth.uid(),
  author_name  text not null default '',
  created_at   timestamptz not null default now()
);
create index if not exists comments_paper on public.comments (paper_id);
-- Reactions on papers (one of each kind per person per paper).
create table if not exists public.reactions (
  paper_id    text not null,
  user_id     uuid not null references auth.users (id) on delete cascade default auth.uid(),
  kind        text not null check (kind in ('like', 'must', 'idea', 'doubt')),
  created_at  timestamptz not null default now(),
  primary key (paper_id, user_id, kind)
);

alter table public.club      enable row level security;
alter table public.comments  enable row level security;
alter table public.reactions enable row level security;

drop policy if exists "club: read"   on public.club;
drop policy if exists "club: add"    on public.club;
drop policy if exists "club: remove" on public.club;
create policy "club: read"   on public.club for select to authenticated using (true);
create policy "club: add"    on public.club for insert to authenticated with check (added_by = auth.uid());
create policy "club: remove" on public.club for delete to authenticated using (added_by = auth.uid());

drop policy if exists "comments: read"   on public.comments;
drop policy if exists "comments: add"    on public.comments;
drop policy if exists "comments: remove" on public.comments;
create policy "comments: read"   on public.comments for select to authenticated using (true);
create policy "comments: add"    on public.comments for insert to authenticated with check (author = auth.uid());
create policy "comments: remove" on public.comments for delete to authenticated using (author = auth.uid());

drop policy if exists "reactions: read"   on public.reactions;
drop policy if exists "reactions: add"    on public.reactions;
drop policy if exists "reactions: remove" on public.reactions;
create policy "reactions: read"   on public.reactions for select to authenticated using (true);
create policy "reactions: add"    on public.reactions for insert to authenticated with check (user_id = auth.uid());
create policy "reactions: remove" on public.reactions for delete to authenticated using (user_id = auth.uid());

revoke all on public.club, public.comments, public.reactions from anon;
revoke insert, update, delete on public.club, public.comments, public.reactions from authenticated;
grant select, delete on public.club, public.comments, public.reactions to authenticated;
grant insert (paper_id, paper, note, added_by_name) on public.club to authenticated;
grant insert (paper_id, body, author_name)          on public.comments to authenticated;
grant insert (paper_id, kind)                       on public.reactions to authenticated;

create or replace function public.social_guard() returns trigger language plpgsql as $$
begin
  if length(new.paper_id) > 200 then raise exception 'Bad paper id'; end if;
  if tg_table_name = 'club' then
    if pg_column_size(new.paper) > 4000 then raise exception 'Paper details too long'; end if;
    new.note := left(trim(new.note), 500);
    new.added_by_name := left(trim(new.added_by_name), 60);
  elsif tg_table_name = 'comments' then
    new.body := trim(new.body);
    if length(new.body) < 1 or length(new.body) > 2000 then raise exception 'Comments are 1 to 2000 characters'; end if;
    new.author_name := left(trim(new.author_name), 60);
  end if;
  return new;
end $$;
drop trigger if exists club_guard on public.club;
create trigger club_guard before insert on public.club for each row execute function public.social_guard();
drop trigger if exists comments_guard on public.comments;
create trigger comments_guard before insert on public.comments for each row execute function public.social_guard();
