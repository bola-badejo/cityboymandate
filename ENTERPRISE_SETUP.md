# Buying X credits and turning on the full engine

## 1. What you will pay for (all prices change: confirm in each console)
| Service | What it is for | Price seen in my checks |
|---|---|---|
| X API credits | reading mentions and conversations, posting replies | post read $0.005 each; user object $0.010 each; post $0.015; a post WITH A LINK: one source says $0.20, another $0.015, so plan for $0.20 until your console shows otherwise |
| Anthropic API | natural replies and judging claims without dates/numbers | pay per use, check the console |
| Brave Search API | finding official pages live | about $5 free credit a month, card required, then about $5 per 1,000 searches |
Our replies carry a source link, so each reply may cost $0.20: a $5 daily cap is about 25 replies a day.

## 2. Buy X credits safely
1. console.x.com > Billing > Credits. Start with $10 to $20. Do not turn on auto-recharge yet.
2. Set a SPENDING LIMIT for the billing cycle in the same place. The bot has its own daily cap (`X_DAILY_BUDGET_USD`) but only X's limit is authoritative.
3. X may offer one-time promotional credits for new pay-per-use accounts (one source says $20 for saving a first eligible card). Read the console terms before relying on it.
4. Check "Usage" in the console after the first day and compare with the dashboard's "X spend today" badge (it is an estimate, set deliberately high).

## 3. Settings for the first week
```
DEMO_MODE=0   X_KEYWORD_SEARCH=0   SCAN_INTERVAL_SECONDS=900   X_DAILY_BUDGET_USD=5   X_THREAD_MAX=8
```
Mentions only, every 15 minutes. Each mention costs a few reads (the post, its author, a few conversation posts), roughly $0.05 to $0.15.

## 4. Real data
- Library: documents you load (docs/EVIDENCE_LIBRARY.md). Most reliable.
- Live official sites: `WEB_EVIDENCE=1`, `BRAVE_API_KEY`, and `EVIDENCE_DOMAINS` such as
  `inecnigeria.org:official_record,nass.gov.ng:legislation,cbn.gov.ng:official_record,nigerianstat.gov.ng:dataset,statehouse.gov.ng:official_record,africacheck.org:fact_checker,dubawa.org:fact_checker,factcheckhub.com:fact_checker`
  That list is from memory: open every address in your browser and confirm it is the genuine site before using it. Only these sites are ever fetched,
  only HTML or text pages (PDFs are not read), and pages are judged like library documents. Many government sites are slow or down: expect gaps (answer: "could not verify").

## 5. Natural replies (RESPONDER=llm)
A model writes one or two plain sentences from the verdict and the source excerpt. The code adds the link and the "automated reply" label. A reply is thrown
away, and the plain template used, if it invents a number or date, says the opposite of the verdict, contains a link, @mention, hashtag, emoji or quotation,
accuses anyone of lying, is too short or does not fit. Before relying on it: run 50 real mentions, read every draft, and note how often it falls back.
The voice is set with `VOICE_STYLE`.

## 6. Reading the thread
The bot reads up to `X_THREAD_MAX` other posts in the conversation, and holds for a person anything that looks like a joke, a hypothetical, a rumour being
repeated, or a fragment of an earlier post. The dashboard's "Conversation" tab shows what it read.

## 7. Not built (be honest in your presentation)
Audio and video analysis (transcription, deepfake evidence): clips get "could not confirm". Multi-turn chat with one person. A database for free hosting.
Fluent-reviewer-approved wording in Hausa, Yoruba, Igbo. X's written approval for an AI reply bot is still required before automatic posting.
