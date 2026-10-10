"""Images and screenshots via Claude vision: OCR, description, and manipulation cues."""

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from app.core import prompts
from app.core.llm import ImageInput, LLMClient
from app.modality.base import OCRResult
from app.modality.media import compress_image


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


async def analyze_images(llm: LLMClient, images: Sequence[ImageInput]) -> ImageAnalysis:
    """One vision call for one image, or for several frames of the same video."""
    return await llm.structured(
        stage="image",
        system=prompts.render("image_read.v1"),
        user=(
            "Analyze this image that a user forwarded to be fact-checked."
            if len(images) == 1
            else f"These are {len(images)} frames from one video a user forwarded. Analyze them "
            "together as one item."
        ),
        output=ImageAnalysis,
        effort="low",
        images=list(images),
    )


class ClaudeImageReader:
    """ImageReader implementation. Images are downscaled/recompressed before upload."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def read(self, image_path: str) -> OCRResult:
        a = await analyze_images(self._llm, [compress_image(Path(image_path))])
        return OCRResult(text=a.text_in_image, description=a.description, language=a.language)
