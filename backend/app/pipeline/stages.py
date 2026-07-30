"""Stage implementations.

Each stage is a pure-ish function: it reads the run plus previously completed
stage outputs, does its work, and returns a StageResult. It never touches the
database or the Celery API, which is what makes the whole pipeline testable
without either.
"""

import hashlib
import json
from dataclasses import dataclass, field

from app.config import get_settings
from app.models import StageName
from app.pipeline import prompts
from app.providers.registry import get_llm, get_tts, resolve_voice
from app.storage import put_object


@dataclass
class ArtifactSpec:
    kind: str
    s3_key: str
    content_type: str
    size_bytes: int
    meta: dict


@dataclass
class StageResult:
    output: dict
    input_tokens: int = 0
    output_tokens: int = 0
    tts_characters: int = 0
    cost_micros: int = 0
    artifacts: list[ArtifactSpec] = field(default_factory=list)


@dataclass
class StageContext:
    run_id: str
    topic: str
    # Both are None until the user picks them; the runner gates the stages that
    # need them, so a stage body can treat its own gate field as present.
    chosen_title: str | None
    voice_id: str | None
    # Outputs of every stage completed so far, keyed by stage name.
    prior: dict[str, dict]
    # Requested narration length. Arrives with the title; None on runs created
    # before script length was selectable, which fall back to the default.
    target_minutes: int | None = None


def _clean_title(line: str) -> str:
    """Strip the list scaffolding models add even when told not to."""
    text = line.strip().lstrip("-*•").strip()
    # Leading "1." / "3)" numbering.
    if text[:1].isdigit():
        for sep in (". ", ") ", "- ", ": "):
            head, found, tail = text.partition(sep)
            if found and head.isdigit():
                text = tail
                break
    return text.strip().strip('"').strip("'").strip()


def _strip_fence(text: str) -> str:
    """Drop a ```json fence, which models add even when told not to."""
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.splitlines()
    return "\n".join(lines[1 : -1 if lines[-1].strip().startswith("```") else None])


def _parse_candidates(text: str, count: int) -> tuple[list[dict], str | None, str]:
    """Read the reply into (candidates, recommended, recommended_why).

    `recommended_why` is the comparative case for the pick — why it beats the
    other candidates, as opposed to each candidate's own standalone rationale.

    Falls back to one-title-per-line when the reply is not valid JSON. A model
    that ignores the format shouldn't cost the user a failed run — they just
    lose the rationales, which are advisory.
    """
    candidates: list[dict] = []
    recommended: str | None = None
    recommended_why = ""

    try:
        payload = json.loads(_strip_fence(text))
    except (json.JSONDecodeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        for entry in payload.get("candidates") or []:
            if not isinstance(entry, dict):
                continue
            title = _clean_title(str(entry.get("title", "")))
            if len(title) > 5:
                candidates.append({"title": title, "why": str(entry.get("why", "")).strip()})
        raw = payload.get("recommended")
        if isinstance(raw, str):
            recommended = _clean_title(raw)
        raw_why = payload.get("recommended_why")
        if isinstance(raw_why, str):
            recommended_why = raw_why.strip()

    if not candidates:
        candidates = [
            {"title": t, "why": ""}
            for t in (_clean_title(line) for line in text.splitlines() if line.strip())
            if len(t) > 5
        ]

    candidates = candidates[:count]
    titles = [c["title"] for c in candidates]
    # A recommendation that doesn't name one of the surviving candidates is
    # worse than none — the UI would badge nothing and the user would wonder.
    if recommended not in titles:
        recommended = titles[0] if titles else None
        # The comparison argued for a title that is no longer on the list, so
        # it now describes nothing. Drop it rather than mislead the hover.
        recommended_why = ""
    return candidates, recommended, recommended_why


def content_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


# --- Stage: titles -----------------------------------------------------


def stage_titles(ctx: StageContext) -> StageResult:
    settings = get_settings()
    llm = get_llm(settings.titles_model)

    system = prompts.TITLES_SYSTEM.format(count=settings.title_count)
    result = llm.complete(
        system=system,
        prompt=prompts.TITLES_PROMPT.format(topic=ctx.topic),
        max_tokens=2000,
    )

    candidates, recommended, recommended_why = _parse_candidates(
        result.text, settings.title_count
    )
    if not candidates:
        raise ValueError("Model returned no usable titles")

    # Deliberately no "chosen" key: picking is the user's job, and the run
    # parks at the gate until they do it. `titles` stays a flat list of strings
    # so the package stage and older runs keep reading the same shape.
    return StageResult(
        output={
            "titles": [c["title"] for c in candidates],
            "candidates": candidates,
            "recommended": recommended,
            "recommended_why": recommended_why,
        },
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_micros=llm.cost_micros(result.usage),
    )


def titles_hash(ctx: StageContext) -> str:
    # The model is part of the input: switching to a stronger one should
    # produce fresh titles rather than replaying the cached weaker set.
    settings = get_settings()
    return content_hash(
        "titles", ctx.topic, get_llm(settings.titles_model).name, settings.titles_model
    )


# --- Stage: script -----------------------------------------------------


def stage_script(ctx: StageContext) -> StageResult:
    settings = get_settings()
    llm = get_llm()
    title = ctx.chosen_title
    if not title:
        raise ValueError("Script stage reached without a chosen title")

    minutes = ctx.target_minutes or settings.default_script_minutes
    target_words = minutes * settings.words_per_minute
    # ~1.4 tokens per English word. The multiplier covers three things at once:
    # overshoot, the closing sentence, and — the big one — reasoning tokens,
    # which count against this same ceiling. A truncated script is a paid call
    # thrown away, so the headroom is cheap insurance.
    max_tokens = min(128_000, max(4_000, int(target_words * 4)))

    result = llm.complete(
        system=prompts.SCRIPT_SYSTEM.format(
            words=target_words, minutes=minutes, wpm=settings.words_per_minute
        ),
        prompt=prompts.SCRIPT_PROMPT.format(title=title, topic=ctx.topic),
        max_tokens=max_tokens,
    )

    script = result.text
    word_count = len(script.split())
    if word_count < 50:
        raise ValueError("Script came back implausibly short")

    key = f"runs/{ctx.run_id}/script.txt"
    size = put_object(key, script.encode("utf-8"), "text/plain; charset=utf-8")

    return StageResult(
        output={
            "title": title,
            "word_count": word_count,
            "target_minutes": minutes,
            "target_words": target_words,
            # What the script will actually take to read at the configured
            # pace, so the UI can show the gap against what was asked for.
            "estimated_minutes": round(word_count / settings.words_per_minute, 1),
            "script": script,
        },
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_micros=llm.cost_micros(result.usage),
        artifacts=[
            ArtifactSpec(
                kind="script",
                s3_key=key,
                content_type="text/plain; charset=utf-8",
                size_bytes=size,
                meta={"word_count": word_count, "target_minutes": minutes},
            )
        ],
    )


def script_hash(ctx: StageContext) -> str:
    # Length is part of the input: asking for 25 minutes after 5 must not
    # reuse the 5-minute script from the cache.
    return content_hash(
        "script", ctx.topic, ctx.chosen_title or "", str(ctx.target_minutes or "")
    )


# --- Stage: review -----------------------------------------------------


def stage_review(ctx: StageContext) -> StageResult:
    """Cheap automated QA gate before we pay for synthesis.

    Advisory by design: it records findings and lets the run continue. Making
    it blocking would trade a small quality win for a large "stuck run" rate.
    """
    llm = get_llm()
    script = ctx.prior[StageName.script.value]["script"]

    result = llm.complete(
        system=prompts.REVIEW_SYSTEM,
        prompt=prompts.REVIEW_PROMPT.format(script=script),
        max_tokens=2000,
    )

    verdict = result.text.strip()
    passed = verdict.upper().startswith("OK")

    return StageResult(
        output={"passed": passed, "findings": "" if passed else verdict},
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_micros=llm.cost_micros(result.usage),
    )


def review_hash(ctx: StageContext) -> str:
    return content_hash("review", ctx.prior[StageName.script.value]["script"])


# --- Stage: tts --------------------------------------------------------


def stage_tts(ctx: StageContext) -> StageResult:
    tts = get_tts()
    if not ctx.voice_id:
        raise ValueError("TTS stage reached without a chosen voice")
    voice = resolve_voice(ctx.voice_id)
    script = ctx.prior[StageName.script.value]["script"]

    result = tts.synthesize(text=script, voice=voice)

    extension = "wav" if "wav" in result.content_type else "mp3"
    key = f"runs/{ctx.run_id}/audio.{extension}"
    size = put_object(key, result.audio, result.content_type)

    return StageResult(
        output={
            "voice_id": voice.id,
            "provider": tts.name,
            "characters": result.characters,
            "content_type": result.content_type,
        },
        tts_characters=result.characters,
        cost_micros=tts.cost_micros(result.characters),
        artifacts=[
            ArtifactSpec(
                kind="audio",
                s3_key=key,
                content_type=result.content_type,
                size_bytes=size,
                meta={"voice_id": voice.id, "provider": tts.name},
            )
        ],
    )


def tts_hash(ctx: StageContext) -> str:
    return content_hash(
        "tts", ctx.prior[StageName.script.value]["script"], ctx.voice_id or "", get_tts().name
    )


# --- Stage: package ----------------------------------------------------


def stage_package(ctx: StageContext) -> StageResult:
    """Bundle the upload-ready metadata so the last step is copy-paste."""
    titles = ctx.prior[StageName.titles.value]
    script_out = ctx.prior[StageName.script.value]
    review = ctx.prior[StageName.review.value]

    chosen = script_out["title"]
    metadata = {
        "title": chosen,
        "alternate_titles": [t for t in titles["titles"] if t != chosen],
        "topic": ctx.topic,
        "word_count": script_out["word_count"],
        "estimated_minutes": script_out.get("estimated_minutes"),
        "voice_id": ctx.prior[StageName.tts.value]["voice_id"],
        "review_passed": review["passed"],
        "review_findings": review["findings"],
        "disclosure": (
            "This video's narration is synthetic, generated with a text-to-speech "
            "voice from a written script. It is not the voice of, written by, or "
            "affiliated with any real person."
        ),
    }

    key = f"runs/{ctx.run_id}/metadata.json"
    body = json.dumps(metadata, indent=2).encode("utf-8")
    size = put_object(key, body, "application/json")

    return StageResult(
        output=metadata,
        artifacts=[
            ArtifactSpec(
                kind="metadata",
                s3_key=key,
                content_type="application/json",
                size_bytes=size,
                meta={},
            )
        ],
    )


def package_hash(ctx: StageContext) -> str:
    return content_hash("package", ctx.run_id, ctx.prior[StageName.script.value]["script"])


STAGE_IMPLS = {
    StageName.titles: (stage_titles, titles_hash),
    StageName.script: (stage_script, script_hash),
    StageName.review: (stage_review, review_hash),
    StageName.tts: (stage_tts, tts_hash),
    StageName.package: (stage_package, package_hash),
}
