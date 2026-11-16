"""Pure async Python Research Controller: orchestrates the WhatsApp Deep Research pipeline.

Replaces LangGraph with clean, native, structured async orchestration:
Router -> Planner -> Parallel Researchers (asyncio.gather) -> Critic -> Verifier -> Writer.
"""
import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.database import mongo
from app.errors import AppError, EmbeddingError, FetchError, JobTimeoutError, LLMError, WhatsAppError, classify
from app.logging_config import job_ctx, mask
from app.models.research import (
    Action,
    CritiqueOut,
    ExtractOut,
    PlanOut,
    RouterOut,
    VerifyOut,
)
from app.services import llm, whatsapp
from app.services.budget import drop_budget, get_budget
from app.services.prompt import render
from app.services.report import markdown_to_pdf_bytes
from app.services.text import (
    clean_whatsapp_text,
    credibility,
    domain_of,
    extract_title,
    finalize_citations,
    norm,
    quote_in_text,
    references_md,
    wrap_untrusted,
)
from app.services.tools.academic import academic_search
from app.services.tools.fetch_page import fetch
from app.services.tools.retrieve import index_page, retrieve
from app.services.tools.web_search import web_search

log = logging.getLogger(__name__)

PENDING_TTL_MIN = 30
NO_REPORT = (
    "💡 I don't have a recent research report in this conversation to refer to. "
    "Send me any research question first, and I'll create a cited report for you!"
)
NO_EVIDENCE = (
    "🤔 I searched across multiple web and academic sources, but couldn't find solid, "
    "verifiable evidence to answer that specific question. "
    "Could you try rephrasing it or adding a bit more detail?"
)
HELP = (
    "👋 *Hi there! I'm your AI Deep Research Assistant.*\n\n"
    "Send me any topic or question, and I will:\n"
    "• 🔍 Research across current web articles and academic papers\n"
    "• 🛡️ Verify claims against verbatim source quotes\n"
    "• 📊 Send you an executive summary and a cited PDF report!\n\n"
    "💡 *Try asking:*\n"
    '• "Compare RAG vs Fine-tuning for medical domain LLMs"\n'
    '• "What is the state of solid-state batteries in 2026?"\n\n'
    "💬 _After you get a report, feel free to ask follow-up questions about it!_\n\n"
    "⚙️ *Commands:*\n"
    "• /help - Show this message\n"
    "• /reset - Forget the last report and start fresh"
)


def _age_minutes(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60


def _fmt_results(kind: str, query: str, rows: list[dict]) -> str:
    if not rows:
        return f"{kind.upper()} '{query}': no results."
    lines = [f"{kind.upper()} '{query}' results:"]
    for r in rows[:8]:
        lines.append(f"- {r['title'][:90]} | {r['url']} | {r['snippet'][:140]}")
    return "\n".join(lines)


async def _read_many(urls: list[str], seen: dict, read_urls: set, budget, report_id: str, user_id: str) -> str:
    async def one(url: str) -> str:
        if not budget.take_fetch():
            return f"SKIPPED {url}: page-fetch budget used up"
        read_urls.add(url)
        meta = seen.get(url, {})
        try:
            page = await fetch(url)
            title, text = page["title"], page["text"]
        except FetchError as e:
            abstract = meta.get("abstract") or ""
            if len(abstract) < 200:
                return f"FAILED {url}: {e.detail}"
            log.info("Fetch failed (%s); indexing the abstract instead: %s", e.detail, url)
            title, text = meta.get("title") or url, abstract
        n = await index_page(report_id, user_id, url, title, text)
        return f"READ {url} -> '{title[:80]}' ({n} chunks indexed)"

    results = await asyncio.gather(*(one(u) for u in urls), return_exceptions=True)
    lines = []
    for url, res in zip(urls, results):
        if isinstance(res, BaseException):
            if isinstance(res, asyncio.CancelledError):
                raise res
            log.warning("Reading %s failed: %s", url, repr(res)[:200])
            if isinstance(res, (EmbeddingError, LLMError)):
                budget.record_error(res)
            lines.append(f"FAILED {url}: {type(res).__name__}")
        else:
            lines.append(res)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline Steps
# ---------------------------------------------------------------------------

async def _router_step(phone: str, query: str, job_id: str, skip_clarify: bool) -> tuple[str, str, str]:
    """Step 1: Router - determine user intent and requested depth."""
    conv = await mongo.get_conversation(phone)
    has_last = bool(conv.get("last_report_id"))
    last = (
        f'"{conv.get("last_report_title", "")}" (original request: {conv.get("last_query", "")})'
        if has_last else "none"
    )
    system = render("router", last_report=last, skip_clarify=str(skip_clarify).lower())
    try:
        out = await llm.complete_json(
            "fast", system, query, RouterOut, job_id=job_id, max_tokens=400
        )
    except LLMError as e:
        log.warning("Router failed (%s); defaulting to deep research", e.detail)
        out = RouterOut(intent="research", depth="deep")

    intent = out.intent
    if intent == "followup" and not has_last:
        intent = "research"
    if intent == "clarify" and (skip_clarify or not out.clarifying_question.strip()):
        intent = "research"

    log.info("Routed: intent=%s depth=%s", intent, out.depth)
    return intent, out.depth, out.clarifying_question.strip()


async def _chat_step(query: str, job_id: str) -> dict:
    reply = await llm.complete("fast", render("chat"), query, max_tokens=300, job_id=job_id)
    return {"reply": reply.strip() or "Hi! Send me a research question."}


async def _followup_step(phone: str, query: str, job_id: str) -> dict:
    conv = await mongo.get_conversation(phone)
    rid = conv.get("last_report_id")
    report = await mongo.get_report(rid) if rid else None
    if not report:
        return {"reply": NO_REPORT}

    chunks: list[dict] = []
    try:
        chunks = await retrieve(rid, query, 6)
    except Exception as e:  # noqa: BLE001
        log.warning("Follow-up retrieval failed (%s); answering from the report text only", str(e)[:150])

    passages = "\n\n".join(wrap_untrusted(i + 1, c.get("url", ""), c.get("text", "")) for i, c in enumerate(chunks))
    system = render("followup", query=report.get("query", ""), report=report.get("report_markdown", "")[:3500])
    user = f"{passages}\n\nQuestion: {query}" if passages else f"Question: {query}"
    answer = (await llm.complete("main", system, user, max_tokens=700, job_id=job_id)).strip()

    cited = sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", answer) if 1 <= int(n) <= len(chunks)})
    if cited:
        answer += "\n\nSources:\n" + "\n".join(
            f"[{n}] {domain_of(chunks[n - 1].get('url', ''))} - {chunks[n - 1].get('url', '')}" for n in cited
        )
    return {"reply": answer}


async def _planner_step(query: str, depth: str, job_id: str, phone: str) -> tuple[str, list[str]]:
    """Step 2: Planner - break down the question into key sub-questions."""
    system = render("planner", depth=depth, max_q=settings.max_sub_questions)
    plan = await llm.complete_json(
        "strong", system, query, PlanOut, job_id=job_id, max_tokens=600
    )
    limit = 2 if depth == "quick" else settings.max_sub_questions
    questions = list(dict.fromkeys(q.strip() for q in plan.sub_questions if q.strip()))[:limit]
    if not questions:
        questions = [query]

    log.info("Planned %d sub-questions (depth=%s)", len(questions), depth)
    if depth == "deep":
        await whatsapp.notify(phone, "🔎 On it! Researching across web articles, docs, and papers for you. This will take about a minute... ⏳")
    else:
        await whatsapp.notify(phone, "⚡ Looking that up for you right now...")

    return plan.brief, questions


async def _research_subquestion(
    phone: str, report_id: str, question: str, job_id: str
) -> list[dict]:
    """Autonomous search, read, index, and claim extraction for one sub-question."""
    budget = get_budget(job_id)
    seen: dict[str, dict] = {}
    read_urls: set[str] = set()
    history: list[str] = []

    for step in range(settings.researcher_max_steps):
        if budget.exhausted():
            log.info("Budget exhausted; stopping research for %r", question[:60])
            break
        system = render(
            "researcher_step",
            question=question,
            steps_left=settings.researcher_max_steps - step,
            history="\n".join(history[-6:]) or "(nothing yet)",
        )
        try:
            act = await llm.complete_json(
                "fast", system, "Choose the next action.", Action, job_id=job_id, max_tokens=300
            )
        except LLMError as e:
            log.warning("Action step failed for %r: %s", question[:60], e.detail)
            budget.record_error(e)
            break

        detail = act.query[:60] if act.query else ",".join(act.urls)[:100]
        log.info("Step %d for %r -> %s %s", step + 1, question[:40], act.action, detail)

        if act.action == "finish":
            break
        if act.action in ("search", "academic"):
            if not act.query.strip():
                history.append("Invalid action: empty query.")
                continue
            if not budget.take_search():
                history.append("Search budget used up.")
                break
            fn = web_search if act.action == "search" else academic_search
            try:
                rows = await fn(act.query)
            except Exception as e:  # noqa: BLE001
                log.warning("%s failed: %s", act.action, str(e)[:150])
                rows = []
            for r in rows:
                seen.setdefault(r["url"], r)
            history.append(_fmt_results(act.action, act.query, rows))
        elif act.action == "read":
            urls = [u for u in dict.fromkeys(act.urls) if u in seen and u not in read_urls][:3]
            if not urls:
                history.append("Invalid action: read only URLs shown in earlier results that are not read yet.")
                continue
            result = await _read_many(urls, seen, read_urls, budget, report_id, phone)
            history.append(result)

    # Safety net: if model never read anything, auto-read top 2 results
    if not read_urls and seen and not budget.exhausted():
        log.info("No pages read by agent; auto-reading top results for %r", question[:60])
        await _read_many(list(seen)[:2], seen, read_urls, budget, report_id, phone)

    chunks = (await retrieve(report_id, question))[:5]
    if not chunks:
        log.info("No indexed material for %r", question[:60])
        return []

    passages = "\n\n".join(
        wrap_untrusted(i + 1, c.get("url", ""), c.get("text", "")[:1000])
        for i, c in enumerate(chunks)
    )
    if len(passages) > 5000:
        passages = passages[:5000]

    try:
        out = await llm.complete_json(
            "main", render("extractor", question=question), passages, ExtractOut, job_id=job_id, max_tokens=700
        )
    except LLMError as e:
        log.warning("Extraction failed for %r: %s", question[:60], e.detail)
        budget.record_error(e)
        return []

    evidence: list[dict] = []
    dropped = 0
    seen_quotes: set[str] = set()
    for it in out.items[:8]:
        if not 1 <= it.source <= len(chunks):
            dropped += 1
            continue
        chunk = chunks[it.source - 1]
        if not quote_in_text(it.quote, chunk.get("text", "")):
            dropped += 1
            continue
        key = norm(it.quote)
        if key in seen_quotes:
            continue
        seen_quotes.add(key)
        evidence.append({
            "claim": it.claim.strip(),
            "quote": it.quote.strip(),
            "url": chunk.get("url", ""),
            "title": chunk.get("title", "") or chunk.get("url", ""),
            "sub_question": question,
            "report_id": report_id,
            "credibility": credibility(chunk.get("url", "")),
        })

    log.info("Extracted %d evidence items for %r (%d rejected)", len(evidence), question[:50], dropped)
    return evidence


async def _run_parallel_research(
    questions: list[str], phone: str, report_id: str, job_id: str
) -> list[dict]:
    """Step 3: Researchers - Fan out across sub-questions in parallel using native asyncio.gather."""
    async def safe_worker(q: str) -> list[dict]:
        try:
            return await _research_subquestion(phone, report_id, q, job_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            err = classify(e)
            log.error("Researcher worker failed for %r [%s]: %s", q[:60], err.code, err.detail)
            get_budget(job_id).record_error(e)
            return []

    results = await asyncio.gather(*(safe_worker(q) for q in questions))
    all_evidence = []
    for res in results:
        all_evidence.extend(res)
    return all_evidence


async def _critic_step(
    query: str, brief: str, sub_questions: list[str], evidence: list[dict], depth: str, job_id: str
) -> tuple[list[str], dict]:
    """Step 4: Critic - evaluate evidence sufficiency; identify gaps for a follow-up round if needed."""
    budget = get_budget(job_id)
    n_sources = len({e["url"] for e in evidence})
    log.info("Critic: %d findings from %d sources", len(evidence), n_sources)

    if not evidence or budget.exhausted() or len(evidence) >= 8:
        return [], {}

    items = sorted(evidence, key=lambda e: e.get("credibility", 0), reverse=True)[:40]
    digest = "\n".join(f"- {domain_of(e['url'])}: {e['claim'][:200]}" for e in items) or "(no evidence found)"
    system = render(
        "critic",
        query=query,
        brief=brief,
        sub_questions="\n".join(f"- {q}" for q in sub_questions),
        digest=digest,
    )
    try:
        crit = await llm.complete_json(
            "strong", system, "Review the evidence.", CritiqueOut, job_id=job_id, max_tokens=700
        )
    except LLMError as e:
        log.warning("Critic failed (%s); continuing without critique", e.detail)
        budget.record_error(e)
        return [], {}

    follow_ups = [q.strip() for q in crit.follow_up_questions if q.strip()][:2]
    can_loop = (
        depth == "deep"
        and len(evidence) < 8
        and not crit.sufficient
        and follow_ups
        and not budget.exhausted()
    )
    pending = follow_ups if can_loop else []
    if pending:
        log.info("Critic found gaps; researching %d follow-up question(s)", len(pending))
    return pending, crit.model_dump()


async def _verifier_step(evidence: list[dict], job_id: str) -> list[dict]:
    """Step 5: Verifier - verify claims against verbatim quotes."""
    budget = get_budget(job_id)
    items, seen = [], set()
    for e in evidence:
        key = (e["url"], norm(e["quote"]))
        if key not in seen:
            seen.add(key)
            items.append(e)
    if not items:
        return []

    # Prioritize top high-credibility claims
    items = sorted(items, key=lambda e: e.get("credibility", 0), reverse=True)[:18]
    system = render("verifier")
    verified: list[dict] = []
    rejected = 0
    batch_size = 12

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        verdicts: dict[int, str] = {}
        if not budget.exhausted():
            text = "\n".join(f"{i}. CLAIM: {e['claim']}\n   QUOTE: {e['quote']}" for i, e in enumerate(batch))
            try:
                out = await llm.complete_json(
                    "main", system, text, VerifyOut, job_id=job_id, max_tokens=600
                )
                verdicts = {v.id: v.verdict for v in out.verdicts}
            except LLMError as e:
                log.warning("Verifier batch failed (%s); keeping quote-checked claims unverified", e.detail)
                budget.record_error(e)
        for i, e in enumerate(batch):
            verdict = verdicts.get(i, "unchecked")
            if verdict == "no":
                rejected += 1
                continue
            verified.append({**e, "verified": verdict})

    verified.sort(key=lambda e: e.get("credibility", 0), reverse=True)
    log.info("Verifier: kept %d, rejected %d of %d", len(verified), rejected, len(items))
    try:
        await mongo.save_evidence(verified)
    except Exception:  # noqa: BLE001
        log.exception("Could not save evidence to MongoDB")
    return verified


async def _writer_step(
    query: str, brief: str, verified: list[dict], critique: dict, depth: str, job_id: str
) -> dict:
    """Step 6: Writer - produce structured report and formatted WhatsApp summary."""
    budget = get_budget(job_id)
    if not verified:
        log.warning("No verified evidence found for query; returning polite fallback response")
        return {"reply": NO_EVIDENCE, "send_pdf": False}

    verified = verified[: settings.writer_max_evidence]
    order: dict[str, int] = {}
    sources: list[dict] = []
    for e in verified:
        if e["url"] not in order:
            order[e["url"]] = len(order) + 1
            sources.append({"n": order[e["url"]], "url": e["url"], "title": (e.get("title") or domain_of(e["url"]))[:140]})

    evidence_block = "\n".join(
        f"[{order[e['url']]}] ({e.get('verified', 'unchecked')}) {e['claim']} - quote: \"{e['quote'][:220]}\""
        for e in verified
    )

    limits = []
    if budget.degraded:
        limits.append("research was cut short (" + ", ".join(budget.reasons) + ")")
    limits += [f"gap: {g}" for g in critique.get("gaps", [])[:3]]
    if any(e.get("verified") in ("partial", "unchecked") for e in verified):
        limits.append("some claims are only partly supported or could not be fully checked")
    limitations = "; ".join(limits) or "sources are web pages and papers that were not independently reviewed"
    contradictions = "; ".join(critique.get("contradictions", [])[:3]) or "none identified"

    if depth == "quick":
        structure = 'Start with "# <short title>", then a direct answer in 1-3 short paragraphs. No other sections.'
        length = "100-250 words"
    else:
        structure = (
            'Structure: "# <title>", "## Executive summary" (4-6 sentences), 2-5 thematic "##" sections, '
            '"## Contradictions and caveats" (only if any), "## Limitations".'
        )
        length = "600-1200 words"

    system = render(
        "writer",
        query=query,
        brief=brief,
        structure=structure,
        contradictions=contradictions,
        limitations=limitations,
        length=length,
        evidence=evidence_block,
    )
    report = await llm.complete("strong", system, "Write the report.", max_tokens=3000, job_id=job_id)
    report = report.strip()
    if not report:
        raise LLMError("writer returned an empty report")

    report, refs = finalize_citations(report, sources)
    report_md = f"{report}\n\n## References\n{references_md(refs)}"
    title = extract_title(report_md, query)
    log.info("Report written: %d chars, %d sources", len(report_md), len(refs))

    if depth == "quick":
        return {
            "reply": clean_whatsapp_text(report_md, max_chars=1200),
            "report": report_md,
            "sources": refs,
            "title": title,
            "send_pdf": False,
        }

    try:
        summary = await llm.complete("fast", render("summary"), report[:6000], max_tokens=800, job_id=job_id)
        summary = summary.strip()
    except LLMError as e:
        log.warning("Summary generation failed (%s); using report opening", e.detail)
        budget.record_error(e)
        summary = ""

    if not summary:
        summary = clean_whatsapp_text(report, max_chars=900) + "\n\n📄 _Full cited report attached as PDF._"
    else:
        summary = clean_whatsapp_text(summary, max_chars=1400)

    return {
        "reply": summary,
        "report": report_md,
        "sources": refs,
        "title": title,
        "send_pdf": True,
    }


# ---------------------------------------------------------------------------
# End-to-End Orchestrator
# ---------------------------------------------------------------------------

async def _run_pipeline(phone: str, text: str, mid: str) -> None:
    conv = await mongo.get_conversation(phone)
    query, skip_clarify, used_pending = text, False, False
    pending = conv.get("pending_query")
    if pending:
        if _age_minutes(conv.get("pending_at", datetime.now(timezone.utc))) <= PENDING_TTL_MIN:
            query = f"{pending}\n\nUser's clarification: {text}"
            skip_clarify, used_pending = True, True
        else:
            log.info("Ignoring stale pending question")

    report_id = uuid.uuid4().hex
    get_budget(mid)

    # 1. Router
    intent, depth, clarifying_question = await _router_step(phone, query, mid, skip_clarify)

    if intent == "chat":
        result = await _chat_step(query, mid)
        await _deliver(phone, query, report_id, result)
        return

    if intent == "clarify":
        result = {"reply": clarifying_question, "needs_clarification": True}
        await _deliver(phone, query, report_id, result)
        return

    if intent == "followup":
        result = await _followup_step(phone, query, mid)
        await _deliver(phone, query, report_id, result)
        return

    # Intent is research: check rate limits and acquire single-job lock
    if not await mongo.check_and_incr_usage(phone, settings.max_messages_per_day):
        log.warning("Daily limit reached for %s", mask(phone))
        await whatsapp.send_text(
            phone, f"Daily limit of {settings.max_messages_per_day} messages reached. Try again tomorrow."
        )
        return

    if not await mongo.try_start_job(phone, mid):
        log.info("User %s already has an active research job", mask(phone))
        await whatsapp.send_text(
            phone, "⏳ I'm currently working on your research report. Give me just a minute to finish it up for you!"
        )
        return

    try:
        # 2. Planner
        brief, sub_questions = await _planner_step(query, depth, mid, phone)

        # 3. Parallel Researchers
        evidence = await _run_parallel_research(sub_questions, phone, report_id, mid)

        # 4. Critic (optional second loop for deep research when evidence is thin)
        pending_followups, crit_dict = await _critic_step(query, brief, sub_questions, evidence, depth, mid)
        if pending_followups:
            gap_evidence = await _run_parallel_research(pending_followups, phone, report_id, mid)
            evidence.extend(gap_evidence)

        # 5. Verifier
        verified = await _verifier_step(evidence, mid)

        # 6. Writer
        result = await _writer_step(query, brief, verified, crit_dict, depth, mid)

        # 7. Deliver & Persist
        await _deliver(phone, query, report_id, result)

        if used_pending:
            await mongo.unset_conversation(phone, "pending_query", "pending_at")
    finally:
        try:
            await mongo.finish_job(phone)
        except Exception:  # noqa: BLE001
            log.exception("Could not release job lock for %s", mask(phone))


async def _deliver(phone: str, query: str, report_id: str, result: dict) -> None:
    reply = (result.get("reply") or "").strip()
    if not reply:
        raise AppError("Pipeline finished without a reply")
    await whatsapp.send_text(phone, reply)

    if result.get("needs_clarification"):
        await mongo.update_conversation(phone, pending_query=query, pending_at=datetime.now(timezone.utc))
        return

    report = result.get("report")
    if not report:
        return

    title = result.get("title") or query[:80]
    try:
        await mongo.save_report({
            "report_id": report_id,
            "phone": phone,
            "query": query,
            "title": title,
            "report_markdown": report,
            "sources": result.get("sources", []),
        })
        await mongo.update_conversation(
            phone, last_report_id=report_id, last_report_title=title, last_query=query
        )
    except Exception:  # noqa: BLE001
        log.exception("Could not save the report; follow-up questions will not work for it")

    if result.get("send_pdf"):
        try:
            pdf = await asyncio.to_thread(markdown_to_pdf_bytes, title, report)
            name = (re.sub(r"[^\w\- ]", "", title).strip()[:60] or "report") + ".pdf"
            await whatsapp.send_document(phone, pdf, name, caption=title)
        except (AppError, OSError) as e:
            log.error("PDF delivery failed: %s", getattr(e, "detail", e))
            await whatsapp.notify(phone, "I could not attach the PDF, so here is the full report:")
            await whatsapp.notify(phone, report)


async def _report_failure(phone: str, mid: str, exc: BaseException) -> None:
    err = classify(exc)
    ref = f"{err.code}-{mid[-6:]}"
    log.error("Job failed [%s] %s", ref, err.detail, exc_info=exc)
    if isinstance(err, WhatsAppError) and err.is_token_error:
        return
    try:
        await whatsapp.send_text(phone, f"Sorry: {err.user_message}\n\nReference: {ref}")
    except Exception as send_exc:  # noqa: BLE001
        log.error("Could not deliver the error message to the user: %s", str(send_exc)[:200])


async def handle_research_job(msg: dict) -> None:
    """Entry point from ARQ worker. Never raises: all errors are logged and delivered honestly."""
    phone, text, mid = msg["from"], msg["text"].strip(), msg["id"]
    token = job_ctx.set(mid[-8:])
    started = asyncio.get_running_loop().time()
    try:
        log.info("Job started for %s (%d chars)", mask(phone), len(text))
        low = text.lower()
        if low in ("/help", "help", "/start"):
            await whatsapp.send_text(phone, HELP)
            return
        if low == "/reset":
            await mongo.unset_conversation(
                phone, "pending_query", "pending_at", "last_report_id", "last_report_title", "last_query"
            )
            await whatsapp.send_text(
                phone, "✨ All clear! I've reset your session and forgotten previous reports. What would you like to explore next?"
            )
            return

        await whatsapp.mark_read(mid)
        await _run_pipeline(phone, text, mid)

        log.info(
            "Job finished in %.1fs | %s",
            asyncio.get_running_loop().time() - started,
            get_budget(mid).summary(),
        )
    except asyncio.CancelledError:
        log.warning("Job cancelled (worker shutdown or timeout)")
        raise
    except Exception as exc:  # noqa: BLE001
        await _report_failure(phone, mid, exc)
    finally:
        drop_budget(mid)
        job_ctx.reset(token)
