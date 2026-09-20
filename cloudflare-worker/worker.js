/**
 * Telegram webhook for the SG Deals Bot.
 *
 * Deploy this as a Cloudflare Worker (free tier). Telegram pushes every
 * message sent to your bot here instantly, so commands like /check and
 * /latest get an immediate reply - unlike the daily GitHub Actions run,
 * which only checks feeds once a day.
 *
 * Required Worker environment variables (set in the Cloudflare dashboard
 * under Settings -> Variables, encrypting the secret-like ones):
 *   TELEGRAM_BOT_TOKEN  - same token used by check_deals.py
 *   ALLOWED_CHAT_ID     - your Telegram chat ID; messages from any other
 *                         chat are silently ignored
 *   WEBHOOK_SECRET      - a random string you invent; must match the
 *                         secret_token used when registering the webhook
 *   GITHUB_TOKEN        - a fine-grained GitHub PAT scoped to this repo
 *                         only, with "Contents: read" and "Actions: write"
 *   GITHUB_OWNER        - the repo owner, e.g. "jovitaanderson"
 *   GITHUB_REPO         - the repo name, e.g. "sg-deals-tele-bot"
 *
 * See README.md for the full step-by-step setup.
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK");
    }

    // Reject anything that isn't genuinely from Telegram.
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (secret !== env.WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch (err) {
      return new Response("OK");
    }

    const message = update.message;
    if (!message || !message.text) {
      return new Response("OK");
    }

    const chatId = String(message.chat.id);
    if (chatId !== env.ALLOWED_CHAT_ID) {
      // Not the bot owner - ignore silently rather than leaking anything.
      return new Response("OK");
    }

    const text = message.text.trim();
    const command = text.split(/\s+/)[0].split("@")[0].toLowerCase();

    if (command === "/start" || command === "/help") {
      await sendMessage(
        env,
        chatId,
        "🇸🇬 <b>SG Deals Bot</b>\n\n" +
          "/check — run a deal check right now\n" +
          "/latest — show the most recent report\n" +
          "/help — show this message"
      );
    } else if (command === "/check") {
      await sendMessage(env, chatId, "🔍 Checking for new deals now, give me a minute…");
      const ok = await triggerWorkflow(env);
      if (!ok) {
        await sendMessage(env, chatId, "⚠️ Couldn't trigger the check - please try again shortly.");
      }
    } else if (command === "/latest") {
      const report = await fetchLatestReport(env);
      await sendMessage(env, chatId, report);
    } else {
      await sendMessage(env, chatId, "Unknown command. Try /help.");
    }

    return new Response("OK");
  },
};

async function sendMessage(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
}

async function triggerWorkflow(env) {
  const resp = await fetch(
    `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/check-deals.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "sg-deals-tele-bot-worker",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
  return resp.ok;
}

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function fetchLatestReport(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/report.md?ref=main`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github.raw+json",
      "User-Agent": "sg-deals-tele-bot-worker",
    },
  });
  if (!resp.ok) {
    return "Couldn't fetch the latest report right now.";
  }
  let text = await resp.text();
  text = escapeHtml(text);
  if (text.length > 3500) {
    text = text.slice(0, 3500) + "\n…(truncated, see report.md in the repo)";
  }
  return `<pre>${text}</pre>`;
}
