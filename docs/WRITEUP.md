# Four of the six "advanced RAG" tricks I tried made my retrieval worse

*Published with the GigBid repository (~1,500 words). Every number comes from the project's results files.*

I spent a weekend building GigBid, a multi-agent copilot for freelancers: a team of LangGraph agents
that reads job posts, flags scams, decides whether a job is worth bidding on, and drafts a proposal in
which every claim links to something real in your portfolio. A human approves everything before it
leaves the system.

The heart of that product is retrieval. If the Writer agent is shown the wrong past project, it either
writes a generic proposal or, worse, claims experience you do not have. So I did what the current
playbook says: I built a baseline, then implemented the techniques everyone recommends, and measured
each one on 544 jobs with known relevant evidence.

The result surprised me enough that it is the only thing I want to write about.

## The setup

The portfolio corpus was deliberately messy, because real ones are. Each synthetic freelancer has 15-20
projects, and every project appears three ways: a case study that names the tools, a repo whose README
says nothing useful ("Small internal tool. See main.py.") while the code reveals the stack, and a client
review that never mentions a technology at all. Skills are often implied rather than named: "schedule
nightly data loads with retries" means Airflow. Around 70% of the job posts are real Upwork posts from a
public Kaggle dataset, which are long, noisy and full of exact tool names.

I built one composable pipeline and made every technique a switch, so each variant differed from the
baseline in exactly one way:

* **V0**: fixed-size chunks, embed the whole job post, cosine top-k (BGE-small).
* **V1 decomposition**: one retrieval per extracted requirement, fused with reciprocal rank fusion.
* **V2 hybrid**: BM25 plus dense, fused with RRF.
* **V3 reranking**: a cross-encoder (MiniLM, MS MARCO) over the top 40.
* **V4 outcome-aware**: boost evidence from past proposals that won or got replies.
* **V5 multi-vector**: summary, README and code indexed separately, returned as one project.
* **V6 contextual retrieval**: a context line ("case study for project X, industry Y, skills Z") prepended
  to every chunk before embedding.

The metric was evidence Recall@5: of the portfolio items that actually prove the job's requirements, how
many appear in the top five.

## What happened

| Variant | Recall@5 |
|---|---|
| V0 baseline | 0.267 |
| V1 requirement decomposition | 0.225 |
| V2 hybrid BM25 + dense | 0.358 |
| V3 cross-encoder reranking | 0.263 |
| V4 outcome-aware boosting | 0.142 |
| V5 multi-vector | 0.233 |
| V6 contextual chunks | 0.391 |
| **Contextual + hybrid (shipped)** | **0.502** |

Four of the six "advanced" techniques scored *below* the naive baseline. Two helped, and together they
nearly doubled recall.

## Decomposition: the hypothesis I wrote down, and lost

Before running anything I wrote down a hypothesis: requirement decomposition plus hybrid retrieval would
raise Recall@5 by 15 points on jobs with four or more requirements. It sounds obviously right. A job that
asks for five things should get five targeted searches, not one blurry one.

On exactly those jobs, decomposition *halved* recall: 0.163 against the baseline's 0.339.

My best explanation (I have not isolated it with a separate experiment yet) is that the whole job post
is a better query than its parts. A post that says "FastAPI backend, Redis queue, RAG over our PDFs"
describes one project; the portfolio item that matches is the one project that used all three. Splitting
the post into "experience with FastAPI", "experience with Redis" and "experience with RAG" throws away that
co-occurrence and retrieves the three most FastAPI-ish, Redis-ish and RAG-ish items separately, which are
often three different, less relevant projects.

## Reranking: slower and worse

The cross-encoder is the technique I was most sure about. It scored 0.263, slightly below the baseline,
and its p95 latency was 3.4 seconds, against well under a millisecond for vector search.

During development I saw a clue: the reranker scored a case study that clearly used Python at about -8 for
the query "experience with Python". MS MARCO cross-encoders are trained on web search questions and
passages, and "experience with X" against a project write-up is not that distribution. A reranker trained
or tuned on your own pairs might behave very differently; an off-the-shelf one is not automatically an
upgrade.

## Outcome-aware boosting: the worst of all

Boosting evidence from proposals that won sounds like learning from what works. It cut recall almost in
half (0.142). Multiplying the scores of past winning proposals promotes them for *every* job, whether or not
they are about the same thing. The outcome signal is real, but it belongs in how a proposal is written
(which GigBid uses separately, through an "outcome memory" with explicit promotion rules), not in what
counts as evidence.

## What actually worked, and why I think it did

**Contextual chunks** were the best single change (0.391). The terse README and the tool-free client review
become findable once each chunk carries a line saying which project and skills it belongs to. This is the
cheapest technique on the list: the context line can be generated once at indexing time.

**Hybrid retrieval** was second (0.358). Freelance work is full of exact names (Celery, dbt, Pinecone,
Next.js) that embeddings blur together and BM25 matches exactly.

They are complementary: one fixes what is in the chunk, the other fixes how the query matches it. The
ablation backs this up: removing contextual chunks from the shipped pipeline costs 14.2 points, removing
hybrid search costs 11.1.

## Better retrieval is not the end of the story

Retrieval is measured against gold evidence. The product is measured by whether clients reply. So I ran
the whole agent team end to end on the same nine jobs twice, once with the naive retriever and once with
the shipped one, and scored each letter with an LLM judge and with the simulator's model of a client.

The naive retriever won. Judge 3.75 against 2.67, expected reply rate 0.27 against 0.21. Nine jobs is far
too few to call it (on the six jobs where both produced a judged letter, naive won four, tied one and lost
one), but the direction was a surprise, and the rows suggest why. Better retrieval hands the Writer more
evidence, the Writer tries to say more with it, and more of what it says is not quite supported.

The other half of grounding is verification. After the Writer drafts, every sentence that claims
experience is checked against the evidence: numbers and tool names must appear in it (checked lexically,
because language models happily accept "40%" against "38%"), and a cross-encoder must agree the evidence is
about the same thing. Unsupported sentences are removed before a critic ever sees the draft.

It works, and it is blunt. Before verification, 62% of the experience claims in the shipped pipeline's
letters were unsupported; after it, none were. But a letter with half its sentences cut is short and
choppy: the average fell to 72 words, the critic rejected most of them, and two dropped below the policy's
60-word minimum and were held back for the human instead of being sent. One sentence that survived was
still wrong: "loaded in under 54 seconds on 3G" contains a number from the evidence and a meaning that
isn't in it. Verification made the letters true. It did not make them good. The fix I would make next
is to hold the Writer to the supported claims before it drafts, not to cut after.

## What I would tell someone building this

1. **Make every technique a switch, and measure each against the baseline on your own data.** The
   playbook is a list of hypotheses, not a list of improvements.
2. **Write the hypothesis down before you run it.** Mine was wrong, and the way it was wrong told me more
   about my data than a confirmed one would have.
3. **Check the slices, not just the average.** Decomposition looked only mildly worse overall; on the jobs
   it was designed for, it was disastrous.
4. **Latency is part of the result.** A 3-second reranker that does not help is a regression twice over.
5. **Keep learning signals where they belong.** Outcomes should shape how you write, not what you count
   as proof.

The limits are real: the gold labels are generated by rules that reward skill overlap, the personas are
synthetic, and the simulated clients are a model of clients, not clients. But the method is the point:
GigBid ships contextual chunks plus hybrid search because the measurements chose it, not because a blog
post did.

*Code, results files and the full report: https://github.com/waqasmalik0007/gigbid*
