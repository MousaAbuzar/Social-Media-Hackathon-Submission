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
    voice_id: str
    # Outputs of every stage completed so far, keyed by stage name.
    prior: dict[str, dict]


def content_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


# --- Stage: titles -----------------------------------------------------


def stage_titles(ctx: StageContext) -> StageResult:
    settings = get_settings()
    llm = get_llm()

    system = prompts.TITLES_SYSTEM.format(count=settings.title_count)
    result = llm.complete(
        system=system,
        prompt=prompts.TITLES_PROMPT.format(topic=ctx.topic),
        max_tokens=2000,
    )

    titles = [line.strip(" -•\t") for line in result.text.splitlines() if line.strip()]
    titles = [t for t in titles if len(t) > 5][: settings.title_count]
    if not titles:
        raise ValueError("Model returned no usable titles")

    return StageResult(
        output={"titles": titles, "chosen": titles[0]},
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_micros=llm.cost_micros(result.usage),
    )


def titles_hash(ctx: StageContext) -> str:
    return content_hash("titles", ctx.topic, get_llm().name)


# --- Stage: script -----------------------------------------------------


def stage_script(ctx: StageContext) -> StageResult:
    settings = get_settings()
    llm = get_llm()
    title = ctx.prior[StageName.titles.value]["chosen"]

    result = llm.complete(
        system=prompts.SCRIPT_SYSTEM.format(words=settings.target_script_words),
        prompt=prompts.SCRIPT_PROMPT.format(title=title, topic=ctx.topic),
        max_tokens=16000,
    )

    script = result.text
    if len(script.split()) < 50:
        raise ValueError("Script came back implausibly short")

    key = f"runs/{ctx.run_id}/script.txt"
    size = put_object(key, script.encode("utf-8"), "text/plain; charset=utf-8")

    return StageResult(
        output={"title": title, "word_count": len(script.split()), "script": script},
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_micros=llm.cost_micros(result.usage),
        artifacts=[
            ArtifactSpec(
                kind="script",
                s3_key=key,
                content_type="text/plain; charset=utf-8",
                size_bytes=size,
                meta={"word_count": len(script.split())},
            )
        ],
    )


def script_hash(ctx: StageContext) -> str:
    return content_hash("script", ctx.topic, ctx.prior[StageName.titles.value]["chosen"])


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
        "tts", ctx.prior[StageName.script.value]["script"], ctx.voice_id, get_tts().name
    )


# --- Stage: package ----------------------------------------------------


def stage_package(ctx: StageContext) -> StageResult:
    """Bundle the upload-ready metadata so the last step is copy-paste."""
    titles = ctx.prior[StageName.titles.value]
    script_out = ctx.prior[StageName.script.value]
    review = ctx.prior[StageName.review.value]

    metadata = {
        "title": script_out["title"],
        "alternate_titles": titles["titles"][1:],
        "topic": ctx.topic,
        "word_count": script_out["word_count"],
        "voice_id": ctx.prior[StageName.tts.value]["voice_id"],
        "review_passed": review["passed"],
        "review_findings": review["findings"],
        "disclosure": (
            "This video's narration is synthetic, generated with a text-to-speech "
            "voice from a written script."
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
