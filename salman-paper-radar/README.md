# Salman Lab Paper Radar

A website that collects new papers on cerebral small vessel disease, the blood–brain barrier, brain delivery (TfR shuttles), glymphatics/AQP4 and drugs in testing for Alzheimer's and other neurodegenerative diseases. It updates itself every morning and keeps a permanent archive, so any day, week or month can be looked up later.

People can make an account, choose the topics, keywords, journals and authors they care about, get a personal feed that greets them by name, and receive a morning email at 08:00 UK time with their most relevant new papers.

- **Journals:** PubMed, searched daily (this includes papers published online ahead of print). Every journal that SCImago ranks Q1 in any field (about 8,000) is flagged Q1, matched by ISSN.
- **Preprints:** bioRxiv and medRxiv, through their public API.
- **Accounts:** Supabase (free tier): sign-up, login and each person's preferences.
- **Morning email:** Resend (free tier), sent by a scheduled GitHub Action.
- **Hosting:** GitHub Pages, free. The daily jobs run on GitHub Actions, also free for this amount of use.

## 1. Put the site online (about 10 minutes)

1. **Create a GitHub account** at github.com if you don't have one.
2. **Create a new repository**, e.g. `paper-radar`. Choose *Public* (free GitHub Pages needs a public repo, or a paid plan for private).
3. **Upload the files:** on the new repo page choose *uploading an existing file* and drag in everything from this folder, **including the `.github` folder** (on a Mac press `Cmd+Shift+.` in Finder to show hidden folders). Commit to the `main` branch.
   - If the `.github` folder won't drag in: in the repo click *Add file → Create new file*, type the name `.github/workflows/update.yml`, and paste in the contents of that file. Do the same for `.github/workflows/digest.yml`.
4. **Turn on Pages:** *Settings → Pages → Build and deployment → Source:* choose **GitHub Actions**.
5. **Let the job save data:** *Settings → Actions → General → Workflow permissions:* choose **Read and write permissions** and save.
6. **Put your email in `config.yaml`** (`ncbi_email`). NCBI asks for a contact address.
7. **First run with a backfill:** *Actions → Update papers → Run workflow*, set days to `30`, and run it. When it finishes (a few minutes), the site address appears on the run page and under *Settings → Pages*. It looks like `https://<your-username>.github.io/paper-radar/`.

From then on it collects papers by itself at 05:00 UTC every day.

## 2. Turn on accounts (about 10 minutes)

1. Create a free project at **supabase.com** (any name; pick the London region).
2. In the project open **SQL Editor → New query**, paste in all of `supabase/schema.sql`, and press **Run**. This creates the preferences table and the rules that let each person see and edit only their own.
3. **Authentication → URL Configuration:** set *Site URL* to your site address (e.g. `https://<your-username>.github.io/paper-radar/`) and add the same address under *Redirect URLs*. Confirmation and password-reset links then bring people back to the site.
4. **Project Settings → API Keys:** copy the **Project URL** and the **publishable** key (or the legacy `anon` key). Put them in `site/config.js`. Both are safe to publish.
5. From the same page copy the **secret** key (or legacy `service_role` key). This one is private: never put it in a file. In GitHub go to *Settings → Secrets and variables → Actions* and add:
   - Secret `SUPABASE_SERVICE_KEY` = the secret key
   - Variable (the *Variables* tab) `SUPABASE_URL` = the Project URL

Commit `site/config.js`; the site redeploys by itself and shows **Sign in / Create account** under the title.

Supabase sends the sign-up confirmation and password-reset emails itself, but its built-in sender only manages a couple of emails an hour. Once you have Resend set up (step 3), connect it as the sender under *Authentication → Emails → SMTP Settings* (Resend's docs list the values) so several people can sign up on the same day.

A free Supabase project pauses after a week without use. The daily jobs read from it every morning, so it stays awake.

## 3. Turn on the morning email (about 10 minutes)

1. Create a free account at **resend.com** and make an **API key** (sending access is enough).
2. **Verify a sending domain** in Resend (*Domains → Add domain*, then add the DNS records it shows). Until you do, Resend only delivers to your own Resend login address, so this step is needed before lab members get emails. A departmental subdomain or any domain you own works; ask your department's IT team if you'd like an `ox.ac.uk` address.
3. In GitHub, *Settings → Secrets and variables → Actions*:
   - Secret `RESEND_API_KEY` = the API key
   - Variable `DIGEST_FROM` = the sender, e.g. `Salman Lab Paper Radar <radar@yourdomain.org>`
   - Variable `SITE_URL` = your site address (used for the "Open your feed" and unsubscribe links)
4. Test it: *Actions → Morning email → Run workflow*, type your own email address in the box, and run.

Every day the email goes out at about **08:00 UK time**, all year: the job is scheduled for both 06:58 and 07:58 UTC to cover GMT and BST, sends from 07:55 London time, and never mails anyone twice in a day. GitHub sometimes starts scheduled jobs a few minutes late. Each email holds up to the number of papers the person chose (5–15), ranked for them. On a day with nothing new for someone, they get no email. People turn the email off in *Preferences*.

Resend's free plan allows 100 emails a day and 3,000 a month, which covers a lab of up to about 100 people.

## Tracked authors and lab topics

- **Tracked authors:** under *Tracked authors* in the sidebar, *Find an author* searches OpenAlex (an open index of researchers and papers built from PubMed, Crossref, ORCID and others). Pick the right person by institution and topics and click *Track*. A red dot next to a name means they have a newer paper than the last time you opened them; clicking the name shows their latest papers and clears the dot. Tracked authors also count towards your feed, the daily search and the morning email.
- **Lab topics:** *+ Add a topic* under *Topics* lets anyone signed in add a topic with 1–12 keywords. Topics are shared: everyone sees them in the sidebar and can tick them in *Preferences*. Only the person who added a topic can remove it.
- **How a new topic reaches the feed:** the *Update papers* job also runs every 30 minutes. It does nothing unless a new topic has appeared; then it searches PubMed, bioRxiv and medRxiv for that topic's keywords over the last 30 days, tags matching papers already in the archive, and republishes the site. From then on the topic is part of the daily search.
- These need the latest `supabase/schema.sql`. If you set up Supabase before these features existed, run the whole file again in the SQL Editor (it is safe to re-run).

## How the personal feed works

- **My feed** shows papers that match any of the person's topics, keywords, journals or authors, ranked by relevance plus matches: keyword +4 each (up to 3), topic +3, journal +4, author +6 each (up to 2). With no preferences yet, it shows the whole feed. **All papers** is the shared lab feed.
- The greeting follows the viewer's clock: *Good morning* (05:00–12:00), *Good afternoon* (12:00–18:00), *Good evening* otherwise.
- Keywords and authors that anyone follows are added to the daily PubMed search. Papers they bring in that fall outside the lab topics appear only in the feeds of the people who follow them. Author matches go by surname and first initial ("Salman M" matches "Salman MM" and "Mootaz Salman").
- The site and the email use the same ranking (`scripts/personal.py` and its copy near the top of the script in `site/index.html`).

## Preview on your computer

Double-click `site/index.html`. It reads the archive from `data/papers.js`, so it works straight from the folder without a web server. The live site reads `data/papers.json`; the daily job keeps both files in sync. Accounts need `site/config.js` filled in.

## Optional extras

Add these under *Settings → Secrets and variables → Actions → New repository secret*.

| Secret | What it does |
|---|---|
| `NCBI_API_KEY` | Free key from your NCBI account settings. Makes PubMed requests more reliable. |
| `ANTHROPIC_API_KEY` | Claude writes a short "why it matters for the lab" note and relevance score for up to 40 new papers a day, instead of using the start of the abstract. Uses the small Haiku model; this costs a small amount per run on your Anthropic API account. |

## Tuning it

Everything is in `config.yaml`:

- **`pubmed_query`:** what PubMed is asked for.
- **`topics`:** keywords and weights that decide the score and topic tags. Raise a weight to push a theme up.
- **`min_score` / `min_score_non_q1`:** how strict the filter is. Raise them if too many papers come in, lower them if too few.
- **`q1_journals`:** extra journals to count as Q1 on top of the SCImago list, matched by name.

The SCImago Q1 list (`data/q1_journals.json`) refreshes itself once a month. If SCImago can't be reached, the previous list keeps working. To refresh it now, run `python scripts/update_q1.py --force`.

If you change the topic ids, also change the `TOPICS` list near the top of the script in `site/index.html`.

**Adding things by hand:** trial readouts, company news or anything the feeds miss go in `data/manual.json`, using the same fields as the existing entries. Hand entries always win over automatic ones with the same title.

## Files

- `site/index.html` is the website; `site/config.js` holds the Supabase address and public key.
- `data/papers.json` is the archive, and `data/papers.js` is the same data for opening the page from disk. The daily job rewrites both; don't edit them by hand.
- `data/q1_journals.json` is the SCImago Q1 list (made by the daily job).
- `data/manual.json` holds hand-curated entries.
- `scripts/fetch_papers.py` is the collector; `scripts/update_q1.py` builds the Q1 list; `scripts/send_digest.py` sends the morning emails; `scripts/personal.py` is the shared ranking.
- `supabase/schema.sql` sets up the accounts database.
- `.github/workflows/update.yml` collects papers daily; `.github/workflows/digest.yml` sends the morning emails.

Read/saved marks are stored in each person's browser, so each lab member keeps their own.
