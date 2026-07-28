# ai-news-digest

Two automated email digests a day, ranked by an LLM, near-zero cost.

| Email | Time (ET) | Covers |
|-------|-----------|--------|
| Morning (flagship) | 9:00 AM | 5:00 PM prev day → 9:00 AM |
| Afternoon | 5:00 PM | 9:00 AM → 5:00 PM |

Sources: 11 free RSS feeds **plus all of Hacker News** in the window (via the Algolia HN API, ranked by points, not just the front page). One `gemini-2.0-flash` call (Google's free tier) ranks + writes a 2-3 sentence self-contained summary per story (no links, everything in the email body). Sends through your own Gmail (SMTP), so it arrives from you. DST handled automatically.

## Setup

1. **Create the repo** and push these files.
2. **Gmail App Password** (sends from your own Gmail, arrives from you):
   - Turn on 2-Step Verification: <https://myaccount.google.com/security>
   - Create an App Password: <https://myaccount.google.com/apppasswords> (name it "news-digest"). Google gives you a 16-char code.
3. **Add repo secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |--------|-------|
   | `GEMINI_API_KEY` | free key from <https://aistudio.google.com/apikey> (no card needed) |
   | `GMAIL_USER` | your full Gmail address (this is also the From address) |
   | `GMAIL_APP_PASSWORD` | the 16-char App Password from step 2 |
   | `MAIL_TO` | where to deliver, usually the same Gmail address |

4. **Test now**: Actions tab → `news-digest` → Run workflow → pick `morning`.

## Tune

- Edit `feeds.txt` to add/remove sources (one RSS/Atom URL per line).
- `MAX_ITEMS_TO_MODEL` / `DESC_CHARS` in `digest.py` control token spend.
- Times: edit the 4 cron lines in `.github/workflows/digest.yml`.

## Local test

```bash
DIGEST_SLOT=morning GEMINI_API_KEY=... \
  GMAIL_USER="you@gmail.com" GMAIL_APP_PASSWORD="abcd efgh ijkl mnop" \
  MAIL_TO="you@gmail.com" python digest.py
```
