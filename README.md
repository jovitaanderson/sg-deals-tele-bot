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
3. Click the **Run workflow** button (top right), then **Run workflow**
   again to confirm.
4. Wait about a minute, then click into the run to see its logs. If it
   found anything new, you should get a Telegram message within a minute
   or two of the run finishing.
5. Check the `report.md` file in this repository — it's updated after
   every run and lists everything the bot found (even on days it doesn't
   message you, e.g. because nothing was new).

After this first test, the bot runs automatically every day at 00:00 UTC
(8:00am Singapore time) with no further action needed from you.

## Running it locally (optional, for developers)

```bash
export TELEGRAM_BOT_TOKEN=your-token   # optional; omit to skip Telegram
export TELEGRAM_CHAT_ID=your-chat-id   # optional; omit to skip Telegram
python3 check_deals.py
```

This requires only the Python 3 standard library — no `pip install`
needed. It creates/updates `seen.sqlite3` (dedup store) and `report.md`
(a markdown summary of everything found in that run).
