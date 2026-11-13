# 0005 Durability and exactly-once submission

**Decision.** LangGraph checkpointer (Postgres in deployment), `interrupt()` before any send/spend, and
idempotency keys derived from the thread id for every marketplace write.
**Why.** Approvals must survive restarts, and a crash between "submit" and "checkpoint written" must not
submit twice or spend Connects twice. The marketplace stores key -> response, so a replay returns the
first response.
