"""Images and screenshots via Claude vision: OCR, description, and manipulation cues."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from app.core import prompts
from app.core.llm import ImageInput, LLMClient


class ImageAnalysis(BaseModel):
    text_in_image: str = Field(description="All readable text, verbatim, in its original language")
    description: str = Field(description="1-3 neutral sentences in English on what the image shows")
    language: str | None = Field(description="BCP-47 code of the text in the image, or null")
    is_screenshot_of_post: bool
    possible_editing_signs: list[str] = Field(
        description="Concrete visual cues of editing or manipulation; empty if none"
    )
    possibly_ai_generated: bool = Field(
        description="True only with concrete visual cues of AI generation"
    )


ImageKind = Literal["image", "slices", "video"]


def _instruction(kind: ImageKind, n: int) -> str:
    if kind == "video":
        return (
            f"These are {n} frames from one video a user forwarded. Analyze them together as one "
            "item."
        )
    if kind == "slices":
        return (
            f"These are {n} overlapping slices of one tall screenshot a user forwarded, from top "
            "to bottom. Analyze them as one image: transcribe its text once, in reading order, "
            "without repeating the lines where slices overlap."
        )
    return "Analyze this image that a user forwarded to be fact-checked."


async def analyze_images(
    llm: LLMClient, images: Sequence[ImageInput], kind: ImageKind | None = None
) -> ImageAnalysis:
    """One vision call: one image, the slices of one tall screenshot, or frames of one video."""
    kind = kind or ("image" if len(images) == 1 else "video")
    return await llm.structured(
        stage="image",
        system=prompts.render("image_read.v1"),
        user=_instruction(kind, len(images)),
        output=ImageAnalysis,
        effort="low",
        images=list(images),
    )
