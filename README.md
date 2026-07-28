# ai-news-digest

Two automated email digests a day, ranked by an LLM, near-zero cost.

| Email | Time (ET) | Covers |
|-------|-----------|--------|
| Morning (flagship) | 9:00 AM | 5:00 PM prev day → 9:00 AM |
| Afternoon | 5:00 PM | 9:00 AM → 5:00 PM |

Sources: 11 free RSS feeds **plus all of Hacker News** in the window (via the Algolia HN API, ranked by points, not just the front page). One `gpt-4o-mini` call ranks + writes a 2-3 sentence self-contained summary per story (no links, everything in the email body). Resend sends the HTML email. DST handled automatically.

## Setup

1. **Create the repo** and push these files.
2. **Resend**: sign up, verify a domain (or use `onboarding@resend.dev` for the `from` while testing), grab an API key.
3. **Add repo secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |--------|-------|
   | `OPENAI_API_KEY` | your OpenAI key |
   | `RESEND_API_KEY` | your Resend key |
   | `MAIL_FROM` | e.g. `News <digest@yourdomain.com>` or `onboarding@resend.dev` |
   | `MAIL_TO` | your inbox |

4. **Test now**: Actions tab → `news-digest` → Run workflow → pick `morning`.

## Tune

- Edit `feeds.txt` to add/remove sources (one RSS/Atom URL per line).
- `MAX_ITEMS_TO_MODEL` / `DESC_CHARS` in `digest.py` control token spend.
- Times: edit the 4 cron lines in `.github/workflows/digest.yml`.

## Local test

```bash
DIGEST_SLOT=morning OPENAI_API_KEY=... RESEND_API_KEY=... \
  MAIL_FROM="onboarding@resend.dev" MAIL_TO="you@example.com" python digest.py
```
