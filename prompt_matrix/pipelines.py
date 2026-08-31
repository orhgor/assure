"""Multi-model workflows: ensemble, create + red-hat, anti-hallucination."""

from __future__ import annotations

import sys

from pydantic import BaseModel, Field

try:
    from .engine import (
        DirectCallError,
        MatrixError,
        copy_to_clipboard,
        load_matrix,
        render_prompt_detailed,
        send_to_llm,
        _resolve_model,
    )
    from .history import history_enabled, record_run, run_hash, store_full_run
    from .keys import key_present, load_keys
    from .linter import lint_prompt
    from .models import RenderedPrompt
    from .personas import get_persona
    from .route import ensure_ollama, live_targets, pick_pair
    from .agents.critique import enforce as enforce_citations
    from .agents.final import shape_final_reply
    from .agents.rule_critic import critique_prompt, rule_critic_requested
    from .workflow_cap import WorkflowTimeout, workflow_deadline
except ImportError:
    from engine import (
        DirectCallError,
        MatrixError,
        copy_to_clipboard,
        load_matrix,
        render_prompt_detailed,
        send_to_llm,
        _resolve_model,
    )
    from history import history_enabled, record_run, run_hash, store_full_run
    from keys import key_present, load_keys
    from linter import lint_prompt
    from models import RenderedPrompt
    from personas import get_persona
    from route import ensure_ollama, live_targets, pick_pair
    from agents.critique import enforce as enforce_citations
    from agents.final import shape_final_reply
    from agents.rule_critic import critique_prompt, rule_critic_requested
    from workflow_cap import WorkflowTimeout, workflow_deadline

WORKFLOWS = ("single", "ensemble", "redhat")

COMBINE_PROMPT = """Merge the model replies into one answer for this task:

{task}

Replies:
{replies}

Rules:
- State consensus once.
- Show disagreements and which side is better supported.
- Drop claims that appear in only one reply with no support.
- Unverified items must be: Data not available in this context.
- If the task is research, split FINAL into [Verified from Context] and [Logical Inference].
- Otherwise keep the dialect output format for that intent.
- Do not invent dates, percentages, or publication names.
- Keep the requested output shape if one was specified.
- Domain and case come only from the task and uploaded files.
"""

try:
    from .config.system_prompt import PEM_BASE_INSTRUCTION, STANDALONE_MARKER
except ImportError:
    from config.system_prompt import PEM_BASE_INSTRUCTION, STANDALONE_MARKER

GROUNDING_BLOCK = PEM_BASE_INSTRUCTION

GROUND_PROMPT = """Rewrite the draft so it cannot hallucinate.

Original task:
{task}

Draft:
{draft}

Rules:
- Keep only claims supported by the task or the supplied context.
- Replace unsupported claims with: Data not available in this context.
- If the task is research, split FINAL into [Verified from Context] and [Logical Inference].
- Otherwise keep the dialect output format for that intent.
- If the task asked for recent Google results or current market data, the rewrite must say: Live search unavailable in standalone PEM.
- Keep the same useful structure. Do not add new facts.
- Domain and case come only from the task and uploaded files.
- Do not add a confidence line unless the original task asked for one.
"""


class PipelineStep(BaseModel):
    name: str
    target_ai: str
    prompt: str
    reply: str | None = None
    error: str | None = None


class PipelineResult(BaseModel):
    workflow: str
    prompt: str
    reply: str | None = None
    note: str | None = None
    steps: list[PipelineStep] = Field(default_factory=list)
    target_ai: str
    intent: str
    wrapper: str = "plain"
    files_read: list[str] = Field(default_factory=list)
    class_id: str | None = None
    copied: bool = False
    persona: str | None = None
    local: bool = False
    direct: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tokens: dict[str, int] = Field(default_factory=dict)
    estimated_cost: float | None = None
    routed_model: str | None = None
    variation_id: int | None = None
    quality: dict | None = None
    run_hash: str | None = None


def run_workflow(
    target_ai: str,
    intent: str,
    task: str,
    context: str = "",
    *,
    workflow: str = "single",
    extra_targets: list[str] | None = None,
    critic: str | None = None,
    persona: str | None = None,
    ground: bool = False,
    direct: bool = False,
    copy: bool = True,
    class_id: str | None = None,
    local: bool = False,
    lint: bool = False,
    history: bool = False,
    cheap: bool = False,
) -> PipelineResult:
    load_keys()
    workflow = (workflow or "single").strip().lower()
    if workflow not in WORKFLOWS:
        raise MatrixError(f"Unknown workflow '{workflow}'. Use single, ensemble, or redhat.")

    try:
        from .editions import (
            clamp_ensemble_extras,
            clamp_persona,
            current_edition,
            guard_send,
            note_for_clamped_persona,
        )
    except ImportError:
        from editions import (
            clamp_ensemble_extras,
            clamp_persona,
            current_edition,
            guard_send,
            note_for_clamped_persona,
        )
    edition_id = load_matrix().runtime.edition
    plan = current_edition(edition_id)
    # Copy mode (direct=False) skips the daily Send quota. Send is gated here.
    guard_send(direct=direct, config_edition=edition_id)
    requested_persona = persona
    persona = clamp_persona(persona, edition_id)
    persona_note = note_for_clamped_persona(requested_persona, persona)
    extra_targets = clamp_ensemble_extras(target_ai, extra_targets, edition_id)

    format_override = None
    variation_id = None
    domain = "general"
    try:
        from .template_library import improve_enabled, pick_variation
        from .variation_generator import infer_domain
    except ImportError:
        from template_library import improve_enabled, pick_variation
        from variation_generator import infer_domain
    try:
        domain = infer_domain(task, context)
        if improve_enabled() and not class_id:
            chosen = pick_variation(intent, domain, target_ai)
            if chosen:
                format_override = chosen.get("variation_text")
                variation_id = chosen.get("id")
    except Exception:
        format_override = None
        variation_id = None

    route_note: str | None = None
    cost_model: str | None = None
    cost_target: str | None = None
    rule_mode = rule_critic_requested(critic)
    if rule_mode:
        critic = "rule"
    if direct:
        ensure_ollama()
        preferred = "ollama" if local else target_ai
        live = live_targets()
        if not live:
            raise MatrixError(
                "Nothing is reachable. Paste a Gemini, DeepSeek, Claude, or Kimi key, "
                "or install Ollama (`brew install ollama`) so PEM can start it."
            )
        try:
            from .cost_router import should_auto_route, suggest_live_target
        except ImportError:
            from cost_router import should_auto_route, suggest_live_target
        if should_auto_route(local=local, cheap=cheap):
            preferred, cost_model, cheap_note = suggest_live_target(
                f"{task}\n{context}", intent, live
            )
            cost_target = preferred
            target_ai = preferred
            route_note = _join_notes(route_note, cheap_note)
        if workflow == "redhat" and rule_mode:
            if preferred not in live:
                route_note = f"{preferred} was down, used {live[0]}."
                target_ai = live[0]
                local = False
            else:
                target_ai = preferred
                local = target_ai == "ollama"
            critic = "rule"
            route_note = _join_notes(route_note, "Critic is the rule-based checker (no API call).")
        elif workflow == "redhat":
            target_ai, critic, route_note = pick_pair(preferred, "ollama" if local else critic)
            local = target_ai == critic == "ollama"
        else:
            if preferred not in live:
                route_note = f"{preferred} was down, used {live[0]}."
                target_ai = live[0]
                local = False
            else:
                target_ai = preferred
                local = target_ai == "ollama"
            if workflow == "ensemble":
                extra_targets = [name for name in (extra_targets or []) if name in live and name != target_ai]
                if not extra_targets:
                    rest = [name for name in live if name != target_ai]
                    extra_targets = rest[:1]
                    if extra_targets:
                        route_note = _join_notes(route_note, f"Added {extra_targets[0]} so Combine has two live models.")
                    else:
                        workflow = "single"
                        route_note = _join_notes(
                            route_note,
                            f"Only {target_ai} is live, so Combine became a single draft.",
                        )

    elif local:
        target_ai = "ollama"
        if workflow == "redhat" and not rule_mode:
            critic = "ollama"

    try:
        from .cost_router import model_override
    except ImportError:
        from cost_router import model_override
    with model_override(cost_target, cost_model):
        if workflow == "ensemble":
            result = _ensemble(
                target_ai, intent, task, context, extra_targets, class_id, direct, ground, lint,
                format_override=format_override,
            )
        elif workflow == "redhat":
            result = _redhat(
                target_ai, critic, intent, task, context, class_id, direct, ground, persona, lint,
                format_override=format_override,
            )
        else:
            result = _single(
                target_ai, intent, task, context, class_id, direct, ground, lint,
                format_override=format_override,
            )
        result.variation_id = variation_id

        if route_note:
            result.note = _join_notes(route_note, result.note)
        if persona_note:
            result.note = _join_notes(persona_note, result.note)

        if copy:
            if result.reply:
                copy_to_clipboard(result.reply)
                result.copied = True
            elif not direct:
                if result.workflow == "single":
                    copy_to_clipboard(result.prompt)
                elif result.steps:
                    copy_to_clipboard(result.steps[0].prompt)
                result.copied = True
        result.local = local
        result.direct = direct
        if workflow == "redhat":
            result.persona = get_persona(persona)["id"]
        try:
            from .token_counter import count_tokens
        except ImportError:
            from token_counter import count_tokens
        compiled_prompt = result.prompt or ""
        final_text = result.reply or ""
        target_model = _resolve_model(result.target_ai, load_matrix()) or result.target_ai
        preferred = result.routed_model or str(target_model)
        input_tokens = count_tokens(compiled_prompt, model=preferred)
        output_tokens = count_tokens(final_text, model=preferred)
        total_tokens = input_tokens + output_tokens
        result.input_tokens = input_tokens
        result.output_tokens = output_tokens
        result.total_tokens = total_tokens
        result.tokens = {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }
        try:
            from .cost_router import estimate_cost
        except ImportError:
            from cost_router import estimate_cost
        est_cost = estimate_cost(preferred, input_tokens, output_tokens)
        result.estimated_cost = est_cost
        if direct:
            print(f"[Cost Router] Estimated cost: ${est_cost:.6f} for {preferred}", file=sys.stderr)
        if history_enabled(history) or (direct and result.reply):
            record_run(
                target_ai=result.target_ai,
                intent=result.intent,
                workflow=result.workflow,
                task=task,
                prompt=result.prompt,
                reply=result.reply,
                note=result.note,
                persona=result.persona,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                extra_targets=extra_targets,
                ground=ground,
                cheap=cheap,
                local=local,
                estimated_cost=est_cost,
                class_id=class_id,
                critic=critic,
                edition=plan.id,
                context=context if plan.full_text_history else "",
            )
        if direct and result.reply:
            try:
                from .history import record_send, prune_old_executions
            except ImportError:
                from history import record_send, prune_old_executions
            record_send()
            digest = run_hash(compiled_prompt)
            result.run_hash = digest
            if plan.history_days:
                prune_old_executions(plan.history_days)
            if plan.full_text_history:
                store_full_run(
                    run_hash=run_hash(compiled_prompt),
                    compiled_prompt=compiled_prompt,
                    final_response=final_text,
                    model=str(preferred),
                    intent=result.intent,
                    workflow=result.workflow,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    force=True,
                )
            try:
                from .quality import draft_replies, score_run
                from .template_library import improve_enabled, record_outcome, record_performance
            except ImportError:
                from quality import draft_replies, score_run
                from template_library import improve_enabled, record_outcome, record_performance
            metrics = None
            try:
                metrics = score_run(
                    reply=final_text,
                    context=context,
                    drafts=draft_replies(result.steps),
                    total_tokens=total_tokens,
                )
                result.quality = metrics.as_dict()
            except Exception as exc:
                print(f"[Improve] score_run skipped: {exc}", file=sys.stderr)
            if improve_enabled() and metrics:
                record_outcome(variation_id, metrics.overall_score)
                print(
                    f"[Improve] overall={metrics.overall_score} "
                    f"consensus={metrics.consensus_score} "
                    f"coherence={metrics.coherence_score} "
                    f"hall={metrics.hallucination_rate} "
                    f"eff={metrics.token_efficiency} "
                    f"variation={variation_id}",
                    file=sys.stderr,
                )
            record_performance(
                run_hash=digest,
                variation_id=variation_id,
                intent=result.intent,
                domain=domain,
                model=result.target_ai,
                consensus_score=metrics.consensus_score if metrics else None,
                coherence_score=metrics.coherence_score if metrics else None,
                hallucination_rate=metrics.hallucination_rate if metrics else None,
                token_efficiency=metrics.token_efficiency if metrics else None,
                overall_score=metrics.overall_score if metrics else None,
                total_tokens=total_tokens,
            )
        return result


def _single(target, intent, task, context, class_id, direct, ground, lint=False, format_override=None) -> PipelineResult:
    rendered = render_prompt_detailed(
        target, intent, task, context, class_id=class_id, format_override=format_override
    )
    prompt = _maybe_ground_prompt(rendered.prompt, ground)
    _guard_dialect(target, prompt, strict=direct or lint)
    step = PipelineStep(name="draft", target_ai=target, prompt=prompt)
    reply = None
    note = None
    routed_model = None
    if direct:
        try:
            from .cost_router import cap_output_tokens, send_model_id, suggest_optimal_model
        except ImportError:
            from cost_router import cap_output_tokens, send_model_id, suggest_optimal_model
        try:
            from .litellm_runner import completion_limits
        except ImportError:
            from litellm_runner import completion_limits
        compiled_prompt = prompt
        target_model = _resolve_model(target, load_matrix()) or target
        preferred = suggest_optimal_model(compiled_prompt, intent, user_preference=target_model)
        send_id = send_model_id(preferred, str(target_model))
        max_out = cap_output_tokens(intent, send_id)
        _tok, timeout = completion_limits(max_tokens=max_out, intent=intent, model=send_id)
        reply, err, note, used = _ask(
            target,
            prompt,
            intent=intent,
            model=send_id,
            max_tokens=max_out,
            timeout=timeout,
        )
        routed_model = preferred
        step.target_ai = used
        step.reply, step.error = reply, err
        if ground and reply and not err:
            gprompt = GROUND_PROMPT.format(task=task, draft=reply)
            grounded, gerr, gnote, gtarget = _ask(
                used,
                gprompt,
                intent=intent,
                model=send_id,
                max_tokens=max_out,
                timeout=timeout,
            )
            step_g = PipelineStep(name="grounding", target_ai=gtarget, prompt=gprompt, reply=grounded, error=gerr)
            if grounded:
                reply = grounded
            note = _join_notes(note, gnote)
            steps = [step, step_g]
        else:
            steps = [step]
        if reply:
            reply, note = _enforce_citations(reply, context, steps, note, intent=intent)
    else:
        steps = [step]
    return PipelineResult(
        workflow="single",
        prompt=prompt,
        reply=reply,
        note=note,
        steps=steps,
        target_ai=rendered.target_ai,
        intent=rendered.intent,
        wrapper=rendered.wrapper,
        files_read=rendered.files_read,
        class_id=rendered.class_id,
        routed_model=routed_model,
    )


def _ensemble(primary, intent, task, context, extra, class_id, direct, ground, lint=False, format_override=None) -> PipelineResult:
    try:
        from .cost_router import (
            TARGET_FOR,
            cap_output_tokens,
            filter_ensemble_extras,
            send_model_id,
            suggest_optimal_model,
        )
    except ImportError:
        from cost_router import (
            TARGET_FOR,
            cap_output_tokens,
            filter_ensemble_extras,
            send_model_id,
            suggest_optimal_model,
        )
    try:
        from .litellm_runner import completion_limits
    except ImportError:
        from litellm_runner import completion_limits

    orig_extras = list(extra or [])
    sample = render_prompt_detailed(
        primary, intent, task, context, class_id=class_id, format_override=format_override
    )
    sample_prompt = _maybe_ground_prompt(sample.prompt, ground)
    target_model = _resolve_model(primary, load_matrix()) or primary
    preferred = suggest_optimal_model(sample_prompt, intent, user_preference=target_model)
    suggested = TARGET_FOR.get(preferred, primary)
    members = _unique([primary] + orig_extras)
    if suggested in members and suggested != primary:
        orig_extras = [primary] + [name for name in orig_extras if name != suggested]
        primary = suggested
    extras, skipped = orig_extras, []
    if direct:
        extras, skipped = filter_ensemble_extras(primary, orig_extras, sample_prompt)
        if len(_unique([primary] + extras)) < 2:
            extras, skipped = orig_extras, []
    skip_note = None
    if skipped:
        skip_note = f"Cost router skipped {', '.join(skipped)} on a short prompt."

    targets = _unique([primary] + list(extras))
    targets = [name for name in targets if name != "cursor"]
    if len(targets) < 2:
        raise MatrixError("Combine needs at least two models. Pick extra targets besides Cursor.")

    steps: list[PipelineStep] = []
    packets: list[str] = []
    files: list[str] = []
    replies: list[tuple[str, str]] = []
    wrapper = "plain"
    used_class = class_id

    for name in targets:
        rendered = render_prompt_detailed(
            name, intent, task, context, class_id=class_id, format_override=format_override
        )
        prompt = _maybe_ground_prompt(rendered.prompt, ground)
        _guard_dialect(name, prompt, strict=direct or lint)
        files = rendered.files_read or files
        used_class = rendered.class_id
        wrapper = rendered.wrapper
        packets.append(f"===== {name} =====\n{prompt}".strip())
        step = PipelineStep(name=f"draft:{name}", target_ai=name, prompt=prompt)
        if direct:
            model_id = _resolve_model(name, load_matrix()) or name
            if name == primary:
                send_id = send_model_id(preferred, str(model_id))
                max_out = cap_output_tokens(intent, send_id)
            else:
                send_id = str(model_id)
                max_out = cap_output_tokens(intent, str(model_id))
            _tok, timeout = completion_limits(max_tokens=max_out, intent=intent, model=send_id)
            reply, err, _, used = _ask(
                name,
                prompt,
                failover=False,
                intent=intent,
                model=send_id,
                max_tokens=max_out,
                timeout=timeout,
            )
            step.target_ai = used
            step.reply, step.error = reply, err
            if reply:
                replies.append((used, reply))
        steps.append(step)

    packet = "\n\n".join(packets) + "\n"
    final = None
    note = skip_note
    if direct and len(replies) >= 2:
        blob = "\n\n".join(f"### {name}\n{text}" for name, text in replies)
        combiner = COMBINE_PROMPT.format(task=task, replies=blob)
        combiner_target = primary if key_present(primary) else replies[0][0]
        combined, err, combine_note, combiner_target = _ask(combiner_target, combiner, intent=intent)
        note = _join_notes(skip_note, combine_note)
        steps.append(
            PipelineStep(name="combine", target_ai=combiner_target, prompt=combiner, reply=combined, error=err)
        )
        final = combined
        packet = packet + "\n===== combine =====\n" + combiner
    elif direct and len(replies) == 1:
        final = replies[0][1]
        note = _join_notes(skip_note, "Only one model replied, so there was nothing to merge.")
    elif not direct:
        order = ", then ".join(targets)
        note = _join_notes(skip_note, f"Copied prompts in this order: {order}. Connect APIs and check Send to merge replies.")

    if final:
        final, note = _enforce_citations(final, context, steps, note, intent=intent)

    return PipelineResult(
        workflow="ensemble",
        prompt=packet,
        reply=final,
        note=note,
        steps=steps,
        target_ai=primary,
        intent=intent,
        wrapper=wrapper,
        files_read=files,
        class_id=used_class,
        routed_model=preferred,
    )


def _redhat(creator, critic, intent, task, context, class_id, direct, ground, persona_id=None, lint=False, format_override=None) -> PipelineResult:
    rule_mode = rule_critic_requested(critic)
    if rule_mode:
        critic = "rule"
    else:
        critic = (critic or "").strip() or _default_critic(creator)
        if not critic:
            critic = creator
        if not critic:
            raise MatrixError("Red-hat needs a model that can answer.")

    persona = get_persona(persona_id)
    created = render_prompt_detailed(
        creator, intent, task, context, class_id=class_id, format_override=format_override
    )
    create_prompt = _maybe_ground_prompt(created.prompt, ground)
    _guard_dialect(creator, create_prompt, strict=direct or lint)
    draft, create_err, note = (None, None, None)
    critique, critique_err, cnote = (None, None, None)
    revised, revise_err, rnote = (None, None, None)
    timed_out = False
    if direct:
        try:
            with workflow_deadline() as cap:
                draft, create_err, note, used = _ask(creator, create_prompt, failover=False, intent=intent)
                cap.check()
                if draft:
                    creator = used
                elif not draft:
                    for alt in live_targets(wake_ollama=False):
                        cap.check()
                        if alt == creator:
                            continue
                        created = render_prompt_detailed(
                            alt, intent, task, context, class_id=class_id, format_override=format_override
                        )
                        create_prompt = _maybe_ground_prompt(created.prompt, ground)
                        _guard_dialect(alt, create_prompt, strict=direct or lint)
                        draft, create_err, note, used = _ask(alt, create_prompt, failover=False, intent=intent)
                        if draft:
                            creator = used
                            note = _join_notes(f"Attempt moved to {used}.", note)
                            break
                critic_prompt = persona["critique"].format(
                    task=task,
                    creator=creator,
                    draft=draft or "[PASTE ATTEMPT HERE]",
                    context=context or "(none)",
                )
                if draft:
                    _, code_verdict = enforce_citations(draft, context)
                    if code_verdict.get("status") == "REJECTED":
                        critic_prompt = (
                            critic_prompt.rstrip()
                            + "\n\nDeterministic citation critique (code, not a model):\n"
                            + str(code_verdict.get("reason") or "")
                            + "\n"
                            + str(code_verdict.get("action") or "")
                            + "\n"
                        )
                if draft:
                    cap.check()
                    if rule_mode:
                        critique = critique_prompt(create_prompt, draft)
                        critique_err, cnote = None, "Rule critic ran locally. No API call."
                    else:
                        critique, critique_err, cnote, critic = _ask(critic, critic_prompt, failover=True, intent=intent)
                revise_prompt = persona["revise"].format(
                    task=task,
                    draft=draft or "[PASTE ATTEMPT HERE]",
                    critique=critique or "[PASTE CRITIQUE HERE]",
                    context=context or "(none)",
                )
                if draft and critique:
                    cap.check()
                    revised, revise_err, rnote, creator = _ask(creator, revise_prompt, failover=True, intent=intent)
        except WorkflowTimeout:
            timed_out = True
            abort = "ERROR: Full workflow timeout. Increase PEM_WORKFLOW_TIMEOUT."
            create_err = create_err or abort
            if draft and not critique:
                critique_err = critique_err or abort
            elif draft and critique and not revised:
                revise_err = revise_err or abort
    if not direct:
        critic_prompt = persona["critique"].format(
            task=task,
            creator=creator,
            draft=draft or "[PASTE ATTEMPT HERE]",
            context=context or "(none)",
        )
        revise_prompt = persona["revise"].format(
            task=task,
            draft=draft or "[PASTE ATTEMPT HERE]",
            critique=critique or "[PASTE CRITIQUE HERE]",
            context=context or "(none)",
        )
    else:
        critic_prompt = persona["critique"].format(
            task=task,
            creator=creator,
            draft=draft or "[PASTE ATTEMPT HERE]",
            context=context or "(none)",
        )
        if draft:
            _, code_verdict = enforce_citations(draft, context)
            if code_verdict.get("status") == "REJECTED" and "Deterministic citation critique" not in critic_prompt:
                critic_prompt = (
                    critic_prompt.rstrip()
                    + "\n\nDeterministic citation critique (code, not a model):\n"
                    + str(code_verdict.get("reason") or "")
                    + "\n"
                    + str(code_verdict.get("action") or "")
                    + "\n"
                )
        revise_prompt = persona["revise"].format(
            task=task,
            draft=draft or "[PASTE ATTEMPT HERE]",
            critique=critique or "[PASTE CRITIQUE HERE]",
            context=context or "(none)",
        )

    if rule_mode:
        if not critique:
            critique = critique_prompt(create_prompt, draft)
            cnote = _join_notes(cnote, "Rule critic ran locally. No API call.")
        critic_prompt = critique
        revise_prompt = persona["revise"].format(
            task=task,
            draft=draft or "[PASTE ATTEMPT HERE]",
            critique=critique,
            context=context or "(none)",
        )

    packet = (
        f"===== 1. ATTEMPT ({creator}) =====\n"
        "Copy this first. Paste it into that model. Wait for the reply.\n\n"
        f"{create_prompt}\n\n"
        f"===== 2. CRITIQUE ({critic} · {persona['label']}) =====\n"
        "Copy this second. Replace [PASTE ATTEMPT HERE] with the attempt reply if it is still a placeholder.\n\n"
        f"{critic_prompt}\n\n"
        f"===== 3. FINAL ({creator}) =====\n"
        "Copy this third. Replace the placeholders with the attempt and the critique.\n\n"
        f"{revise_prompt}\n"
    )
    abort_msg = "ERROR: Full workflow timeout. Increase PEM_WORKFLOW_TIMEOUT."
    if timed_out:
        final = abort_msg
        run_note = abort_msg
    elif revised:
        final = revised
        run_note = (
            f"Ran attempt, then {persona['label']} critique, then a final rewrite. "
            "Read Final. You do not paste anything back."
        )
    elif draft and critique:
        final = f"## Attempt ({creator})\n{draft}\n\n## Critique ({critic})\n{critique}\n"
        run_note = "Attempt and critique finished, but the final rewrite failed. Read the working notes."
    elif draft:
        final = draft
        run_note = "Only the attempt came back. The critic did not run."
    else:
        final = None
        if direct:
            run_note = create_err or "The attempt model did not reply. Nothing was copied. Fix the connection and click again."
        else:
            run_note = (
                "Copied step 1 (Attempt). After that reply, copy step 2, then step 3. "
                "Or check Send so this page runs all three calls."
            )

    steps = [
        PipelineStep(name="create", target_ai=creator, prompt=create_prompt, reply=draft, error=create_err),
        PipelineStep(name="red-hat", target_ai=critic, prompt=critic_prompt, reply=critique, error=critique_err),
        PipelineStep(name="final", target_ai=creator, prompt=revise_prompt, reply=revised, error=revise_err),
    ]
    if final and not timed_out:
        final, run_note = _enforce_citations(final, context, steps, run_note, intent=intent)

    return PipelineResult(
        workflow="redhat",
        prompt=packet,
        reply=final,
        note=_join_notes(note, cnote, rnote, run_note),
        steps=steps,
        target_ai=creator,
        intent=intent,
        wrapper=created.wrapper,
        files_read=created.files_read,
        class_id=created.class_id,
        persona=persona["id"],
    )


def _enforce_citations(
    text: str,
    context: str,
    steps: list[PipelineStep],
    note: str | None,
    intent: str = "",
) -> tuple[str, str | None]:
    clean, verdict = enforce_citations(text, context)
    research = (intent or "").strip().lower() == "research"
    shaped = shape_final_reply(clean, context) if research else clean
    if verdict.get("status") != "REJECTED" and shaped.strip() == text.strip():
        return text, note
    name = "citation-critique" if verdict.get("status") == "REJECTED" else "final-format"
    prompt = str(
        verdict.get("reason")
        or "Research FINAL sections: Thesis / Verified / Inferred / Open questions."
    )
    steps.append(
        PipelineStep(
            name=name,
            target_ai="pem",
            prompt=prompt,
            reply=shaped,
        )
    )
    extra = None
    if verdict.get("status") == "REJECTED":
        extra = "Citation critique rejected hallucinated sources and replaced those lines."
    if research:
        extra = _join_notes(extra, "Research output forced into Thesis / Verified / Inferred / Open questions.")
    return shaped, _join_notes(note, extra)


def _guard_dialect(target: str, prompt: str, *, strict: bool) -> None:
    report = lint_prompt(target, prompt)
    if report.errors and strict:
        raise MatrixError(
            "ERROR: Lint failed. Fix structural issues before sending.\n"
            f"{report.as_text()}"
        )


def _ask(
    target: str,
    prompt: str,
    *,
    failover: bool = True,
    intent: str = "analysis",
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> tuple[str | None, str | None, str | None, str]:
    names = [target]
    if failover:
        names.extend(name for name in live_targets(wake_ollama=False) if name != target)
    last_err: str | None = None
    last_note: str | None = None
    for name in names:
        hop_model = model if name == target else None
        hop_max = max_tokens if name == target else None
        hop_timeout = timeout if name == target else None
        reply, err, note = _ask_one(
            name,
            prompt,
            intent=intent,
            model=hop_model,
            max_tokens=hop_max,
            timeout=hop_timeout,
        )
        if reply:
            hop = None
            if name != target:
                hop = f"{target} failed, used {name}."
                if last_err:
                    hop = f"{target} failed ({last_err}). Used {name}."
            return reply, None, _join_notes(hop, note), name
        last_err, last_note = err, note
    return None, last_err, last_note, target


def _ask_one(
    target: str,
    prompt: str,
    intent: str = "analysis",
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> tuple[str | None, str | None, str | None]:
    if target == "cursor":
        return None, "Cursor has no public chat API.", None
    if max_tokens is None:
        try:
            from .cost_router import cap_output_tokens, send_model_id, suggest_optimal_model
        except ImportError:
            from cost_router import cap_output_tokens, send_model_id, suggest_optimal_model
        target_model = _resolve_model(target, load_matrix()) or target
        preferred = suggest_optimal_model(prompt, intent, user_preference=model or target_model)
        send_id = send_model_id(preferred, str(target_model))
        max_tokens = cap_output_tokens(intent, send_id if model is None else model)
        if model is None:
            model = send_id
        if timeout is None:
            try:
                from .litellm_runner import completion_limits
            except ImportError:
                from litellm_runner import completion_limits
            _tok, timeout = completion_limits(max_tokens=max_tokens, intent=intent, model=model)
    rendered = RenderedPrompt(target_ai=target, intent=intent, wrapper="plain", prompt=prompt)
    try:
        reply, _structured = send_to_llm(
            rendered,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return reply, None, None
    except WorkflowTimeout:
        raise
    except DirectCallError as exc:
        return None, str(exc), str(exc)
    except MatrixError as exc:
        return None, str(exc), str(exc)


def _maybe_ground_prompt(prompt: str, ground: bool) -> str:
    if not ground:
        return prompt
    if STANDALONE_MARKER in prompt or "Grounding rules" in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + GROUNDING_BLOCK + "\n"


def _default_critic(creator: str) -> str | None:
    live = live_targets(wake_ollama=False)
    for name in live:
        if name != creator:
            return name
    return live[0] if live else None


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        name = (item or "").strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def _join_notes(*parts: str | None) -> str | None:
    text = " ".join(part for part in parts if part)
    return text or None
