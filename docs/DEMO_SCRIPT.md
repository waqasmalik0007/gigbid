# 3-minute demo script

Record the screen (Windows: **Win + Alt + R** with Xbox Game Bar, or OBS) at 1080p with your voice.
Before recording: sign in as the reviewer, click **Find new jobs** once, and wait until the inbox shows
analysed jobs (bid / skip badges). Keep one job that is awaiting approval and one client reply ready.

| Time | Show | Say (roughly) |
|---|---|---|
| 0:00-0:15 | Inbox | "GigBid is a team of agents that finds freelance jobs, flags scams, and drafts proposals backed by my real portfolio. Nothing is sent until I approve it." |
| 0:15-0:45 | Click a **bid** job → Job detail | "Three agents analysed this in parallel: the job spec, the risk report, and this requirement-to-evidence matrix. Fit 0.8, low risk, so it recommends a bid. Pricing used real market rates and decided the Connects are worth it." |
| 0:45-1:30 | **Open proposal studio** | "Every claim is underlined. Hover one: here is the case study that proves it. The verifier removed a claim it could not prove; the critic sent the draft back once; compliance passed it. The dry-run shows exactly what will be sent and that it costs 8 Connects." Click **Approve**. "Now it is submitted, once." |
| 1:30-1:50 | Inbox → a **scam** job (red badge) → detail | "This one asked for an unpaid full trial and a WhatsApp contact. Rules flagged it as a scam before any LLM could be talked out of it, so GigBid skipped it and spent nothing." |
| 1:50-2:15 | A job with **prompt_injection** flag → risk report | "This post hides an instruction to AI assistants: put your email in the first line and promise a refund. The injected text is quoted here; it was treated as data. The proposal contains no email, and the compliance guard would have removed it anyway." |
| 2:15-2:45 | **Messages** → client reply → Approvals | "The client replied asking for a call. The liaison agent classified it as an interview request, picked free slots from my calendar and drafted a reply. I approve; it sends and puts a tentative hold in my calendar." |
| 2:45-3:00 | **Runs** → a run timeline; **Analytics** | "Every run is traced node by node with tokens and cost. All of this runs on open models on free tiers." |

Tips: zoom the browser to 110%; close other tabs; if a page shows "Marketplace unavailable", restart
GigSim first. Upload the video as **unlisted** on YouTube or Google Drive (anyone with the link) and paste the
link into the README and the Classroom submission. A 30-second GIF for the README top: record 0:15-0:45 and
convert with ezgif.com (keep it under 8 MB), save as `docs/images/demo.gif`.
