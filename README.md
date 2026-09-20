# sg-deals-tele-bot

A small, free, self-running bot that checks for Singapore deals, vouchers,
freebies and "earn a voucher" style promos every day, and messages you on
Telegram when it finds something new. It costs nothing to run:

- **GitHub Actions** (free tier) runs the check once a day on a schedule.
- **Telegram's Bot API** (free) delivers the message to you.
- No servers, no paid APIs, no sign-ups beyond a Telegram account.

It looks at Google News search results, a few Reddit communities, and two
Singapore deals/finance blogs, scores each article with keyword heuristics,
and only messages you about things that look like real deals (it tries to
filter out scam/news stories, stock price news, and government policy
articles that just happen to mention "voucher").

It remembers what it has already sent you (in a small `seen.sqlite3` file
committed back to this repo), so you won't get the same deal twice. On a
day with nothing new, it stays completely quiet — no message at all.

## How it decides what's a "deal"

- `check_deals.py` pulls RSS/Atom feeds from Google News, Reddit, and two
  blogs (Milelion, Mothership).
- Each article is scored with regex keyword rules:
  - Big bonus for "earn a voucher / complete a challenge / claim / redeem /
    sign-up reward / cashback" style language.
  - Smaller bonus for generic deal words (voucher, promo code, 1-for-1,
    % off, sale, freebie, etc).
  - Penalty for scam/news-y language (scam, phishing, arrested, fined,
    stock price, inflation, budget/parliament/policy news, etc) so real
    news stories don't get mixed in with actual deals.
  - Tagged into a category: Tech, F&B, Skincare, or Lifestyle.
- Only articles published in roughly the last 2 days are considered.
- Anything that scores above the threshold, and hasn't been sent before,
  goes into `report.md` and a Telegram message, with "earn a voucher /
  freebies" items listed first, then Tech / F&B / Skincare / Lifestyle.

You can tune the behaviour with optional environment variables (set as
repository variables/secrets, or edit the workflow file):

| Variable          | Default | Meaning                                   |
|-------------------|---------|--------------------------------------------|
| `LOOKBACK_DAYS`   | `2`     | How many days back to consider articles    |
| `SCORE_THRESHOLD` | `4`     | Minimum score before something is reported |

## Setup (step-by-step, no coding needed)

### 1. Create a Telegram bot

1. Open Telegram and search for the user **@BotFather** (it has a blue
   checkmark).
2. Start a chat with it and send the message `/newbot`.
3. Follow the prompts: give your bot a display name, then a username that
   ends in `bot` (e.g. `sgdeals_yourname_bot`).
4. BotFather will reply with a message containing a long token that looks
   like `123456789:AAExampleTokenTextGoesHere`. **Save this** — it's your
   `TELEGRAM_BOT_TOKEN`. Keep it secret; anyone with it can send messages
   as your bot.

### 2. Start a chat with your bot and get your Chat ID

1. In Telegram, search for the username you just gave your bot and open a
   chat with it.
2. Send it any message, e.g. `hello`. (Bots can't message you first — you
   have to message them once.)
3. In your browser, go to this URL, replacing `<TOKEN>` with the token
   from step 1:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. You'll see some JSON text. Look for a section like:
   ```
   "chat":{"id":123456789,"first_name":"Your Name", ... }
   ```
   The number next to `"id"` (e.g. `123456789`) is your
   `TELEGRAM_CHAT_ID`. If the page looks empty (`"result":[]`), make sure
   you sent the bot a message first, then reload the URL.

### 3. Add the two values as GitHub Actions secrets

1. Open this repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Click **New repository secret**.
4. Add a secret named `TELEGRAM_BOT_TOKEN` with the token from step 1.
5. Click **New repository secret** again and add `TELEGRAM_CHAT_ID` with
   the number from step 2.

That's it — no other configuration is required. The daily schedule and
everything else is already set up in `.github/workflows/check-deals.yml`.

### 4. Test it

1. Go to the **Actions** tab of this repository.
2. Click on the **Check Singapore Deals** workflow in the left sidebar.
3. Click the **Run workflow** button (top right). There's a checkbox
   labelled "Send a fixed Telegram test message instead of checking
   feeds" — tick it, then click **Run workflow** to confirm. This sends
   one fixed confirmation message straight away, so you don't have to
   wait for a real deal to show up to know Telegram is wired up
   correctly.
4. Wait about 15–30 seconds, then check Telegram for the message.
5. Once that works, you can also try a normal run (leave the checkbox
   unticked) to see real deal-checking in action. If it found anything
   new, you'll get a Telegram message within a minute or two; if not, it
   stays quiet.
6. Check the `report.md` file in this repository — it's updated after
   every normal run and lists everything the bot found (even on days it
   doesn't message you, e.g. because nothing was new).

After this first test, the bot runs automatically every day at 00:00 UTC
(8:00am Singapore time) with no further action needed from you.

## Optional: interactive commands (/check, /latest)

By default the bot only *sends* messages - it can't react when you type
something back, because GitHub Actions only wakes it up once a day (or
when you manually run it). To make it respond instantly to commands like
`/check` (run a deal check right now) and `/latest` (resend the last
report), you need something listening for your messages around the
clock. A free [Cloudflare Worker](https://workers.cloudflare.com/) does
this at no cost. This part is optional - skip it if you're happy with
just the daily message.

### A. Create a GitHub token for the worker to use

1. Go to `https://github.com/settings/personal-access-tokens/new`.
2. Under **Repository access**, choose **Only select repositories** and
   pick this repo.
3. Under **Permissions → Repository permissions**, set:
   - **Contents**: Read-only
   - **Actions**: Read and write
4. Generate the token and copy it somewhere safe - you'll paste it into
   Cloudflare in step B, and won't be able to see it again afterwards.

### B. Create the Cloudflare Worker

1. Sign up for a free account at `https://dash.cloudflare.com/sign-up`
   (no credit card needed for the free tier).
2. In the dashboard, go to **Workers & Pages → Create → Create Worker**.
   Give it any name (e.g. `sg-deals-bot-webhook`) and deploy the default
   "Hello World" template - you'll replace the code next.
3. Click **Edit code**, delete everything, and paste in the contents of
   this repo's `cloudflare-worker/worker.js`. Click **Deploy**.
4. Go to the Worker's **Settings → Variables and Secrets** and add these
   six variables (mark the token/secret ones as "Encrypt"):
   - `TELEGRAM_BOT_TOKEN` - the same bot token from setup step 1
   - `ALLOWED_CHAT_ID` - your chat ID from setup step 2
   - `WEBHOOK_SECRET` - any random string you make up (e.g. mash the
     keyboard for 20+ characters) - it just has to match step C below
   - `GITHUB_TOKEN` - the token you created in step A
   - `GITHUB_OWNER` - the GitHub username/org that owns this repo
   - `GITHUB_REPO` - this repository's name
5. Note the Worker's URL, shown at the top of its page - something like
   `https://sg-deals-bot-webhook.<your-subdomain>.workers.dev`.

### C. Point Telegram at the Worker

In your browser, visit this URL (replace `<TOKEN>` with your bot token,
`<WORKER_URL>` with the URL from step B.5, and `<SECRET>` with the exact
`WEBHOOK_SECRET` value you set):

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<SECRET>
```

You should see `{"ok":true,"result":true,...}`. That's it - message your
bot with `/help` on Telegram and you should get an instant reply.

### Available commands

| Command   | What it does                                              |
|-----------|-------------------------------------------------------------|
| `/help`   | Shows the list of commands                                |
| `/check`  | Triggers an on-demand deal check (same as "Run workflow")  |
| `/latest` | Sends back the contents of the most recent `report.md`    |

Only messages from your own `ALLOWED_CHAT_ID` are answered - anyone else
who finds your bot's username gets ignored.

## Running it locally (optional, for developers)

```bash
export TELEGRAM_BOT_TOKEN=your-token   # optional; omit to skip Telegram
export TELEGRAM_CHAT_ID=your-chat-id   # optional; omit to skip Telegram
python3 check_deals.py
```

This requires only the Python 3 standard library — no `pip install`
needed. It creates/updates `seen.sqlite3` (dedup store) and `report.md`
(a markdown summary of everything found in that run).
