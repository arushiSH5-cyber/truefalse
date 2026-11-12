"""AI-content detection: signal combination, Content Credentials (real signed files), metadata,
classifier parsing, reverse image search, the detector, and the pipeline wiring."""

import importlib.util
import io
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image, PngImagePlugin

from app.ai_detect import classifiers, credentials, metadata, reverse_image
from app.ai_detect.detector import AIDetector
from app.ai_detect.signals import W_POSSIBLY_AI, AIReport, AISignal, Strength
from app.ai_detect.text import AITextAssessment, check_text
from app.core.models import EvidenceDraft, Part, PartKind, SourceKind
from app.modality.media import temp_workdir
from app.modality.process import MediaProviders, process_submission
from tests.fakes import FakeLLM
from tests.test_multimodal import FakeFetcher, _analysis, _sub, fake_dns  # noqa: F401

S = Strength


def _sig(detector: str, finding: str = "ai_generated", strength: Strength = S.WEAK, **kw: Any):
    return AISignal(detector=detector, media="image", finding=finding, strength=strength, **kw)  # type: ignore[arg-type]


# ---- combining signals ---------------------------------------------------------------------


def test_weak_signal_alone_is_not_shown() -> None:
    report = AIReport(signals=[_sig("vision_llm")])
    assert report.overall == "no_signal" and report.user_warnings() == []
    # The same detector twice is still one opinion.
    assert AIReport(signals=[_sig("hive"), _sig("hive")]).overall == "no_signal"


def test_two_weak_detectors_or_one_moderate_mean_possibly_ai() -> None:
    assert AIReport(signals=[_sig("vision_llm"), _sig("hive")]).user_warnings() == [W_POSSIBLY_AI]
    assert AIReport(signals=[_sig("sightengine", strength=S.MODERATE)]).overall == "possibly_ai"


def test_strong_signal_uses_its_own_detail_and_recycled_is_reported() -> None:
    report = AIReport(
        signals=[
            _sig("vision_llm"),
            _sig("c2pa", strength=S.STRONG, detail="Made with AI (X), signed by Y."),
            _sig("reverse_image", "recycled", S.STRONG, detail="Online since 2015."),
        ]
    )
    assert report.overall == "likely_ai"
    assert report.user_warnings() == ["Made with AI (X), signed by Y.", "Online since 2015."]
    assert AIReport(signals=[_sig("reverse_image", "recycled", S.STRONG)]).overall == "recycled"


# ---- Content Credentials (C2PA) ------------------------------------------------------------


def test_interpret_manifest_without_the_library() -> None:
    store = {
        "active_manifest": "m1",
        "manifests": {
            "m1": {
                "claim_generator_info": [{"name": "Studio"}],
                "signature_info": {"issuer": "Example Corp"},
                "assertions": [
                    {
                        "label": "c2pa.actions.v2",
                        "data": {
                            "actions": [
                                {
                                    "action": "c2pa.created",
                                    "digitalSourceType": credentials.IPTC
                                    + "compositeWithTrainedAlgorithmicMedia",
                                }
                            ]
                        },
                    }
                ],
            }
        },
    }
    s = credentials.interpret(store, "Valid", "image")
    assert (s.finding, s.strength) == ("ai_generated", S.STRONG)
    assert "(Studio)" in s.detail and "Example Corp" in s.detail
    assert credentials.interpret(store, "Invalid", "video").finding == "edited"
    empty = {"active_manifest": "m1", "manifests": {"m1": {"assertions": []}}}
    assert credentials.interpret(empty, "Valid", "image").finding == "no_finding"


@pytest.fixture(scope="session")
def c2pa_signer(tmp_path_factory) -> Path:
    """A throwaway CA + signing certificate (C2PA requires a CA-issued end-entity cert)."""
    if not importlib.util.find_spec("c2pa") or not shutil.which("openssl"):
        pytest.skip("needs the ai_detect extra and openssl")
    d = tmp_path_factory.mktemp("c2pa")

    def run(*args: str) -> None:
        subprocess.run(["openssl", *args], cwd=d, check=True, capture_output=True)

    (d / "ca.cnf").write_text(
        "[req]\ndistinguished_name=dn\n[dn]\n[v3]\nbasicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n"
    )
    (d / "leaf.ext").write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
        "extendedKeyUsage=emailProtection\nsubjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid\n"
    )
    run("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key")
    run(
        "req",
        "-new",
        "-x509",
        "-key",
        "ca.key",
        "-subj",
        "/CN=Test CA/O=TrueFalse Tests",
        "-days",
        "30",
        "-out",
        "ca.pem",
        "-extensions",
        "v3",
        "-config",
        "ca.cnf",
    )
    run("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "leaf.key")
    run("pkcs8", "-topk8", "-nocrypt", "-in", "leaf.key", "-out", "leaf_pk8.key")
    run(
        "req",
        "-new",
        "-key",
        "leaf.key",
        "-subj",
        "/CN=Test Signer/O=TrueFalse Tests",
        "-out",
        "leaf.csr",
    )
    run(
        "x509",
        "-req",
        "-in",
        "leaf.csr",
        "-CA",
        "ca.pem",
        "-CAkey",
        "ca.key",
        "-CAcreateserial",
        "-days",
        "30",
        "-extfile",
        "leaf.ext",
        "-out",
        "leaf.pem",
    )
    (d / "chain.pem").write_bytes((d / "leaf.pem").read_bytes() + (d / "ca.pem").read_bytes())
    return d


def _signed(signer_dir: Path, out: Path, source_type: str, tool: str = "Some Model") -> Path:
    import c2pa

    src = out.with_suffix(".src.jpg")
    Image.effect_noise((128, 128), 60).convert("RGB").save(src, quality=90)
    manifest = {
        "claim_generator_info": [{"name": "Test App"}],
        "title": out.name,
        "format": "image/jpeg",
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": credentials.IPTC + source_type,
                            "softwareAgent": {"name": tool},
                        }
                    ]
                },
            }
        ],
    }
    info = c2pa.C2paSignerInfo(
        b"es256",
        (signer_dir / "chain.pem").read_bytes(),
        (signer_dir / "leaf_pk8.key").read_bytes(),
        None,
    )
    signer = c2pa.Signer.from_info(info)
    with c2pa.Builder(json.dumps(manifest)) as b, src.open("rb") as i, out.open("wb") as o:
        b.sign(signer, "image/jpeg", i, o)
    return out


async def test_real_content_credentials(c2pa_signer, tmp_path) -> None:
    check = credentials.ContentCredentialsCheck()
    ai = _signed(c2pa_signer, tmp_path / "ai.jpg", "trainedAlgorithmicMedia", "Imagine Model")
    [s] = await check.check(ai, "image")
    assert (s.finding, s.strength) == ("ai_generated", S.STRONG)
    assert "Imagine Model" in s.detail and "TrueFalse Tests" in s.detail

    cam = _signed(c2pa_signer, tmp_path / "cam.jpg", "digitalCapture")
    [s] = await check.check(cam, "image")
    assert s.finding == "camera_original"

    tampered = tmp_path / "tampered.jpg"
    data = bytearray(cam.read_bytes())
    data[-200] ^= 0xFF  # change the picture after signing
    tampered.write_bytes(bytes(data))
    [s] = await check.check(tampered, "image")
    assert (s.finding, s.strength) == ("edited", S.MODERATE)

    plain = tmp_path / "plain.jpg"
    Image.new("RGB", (32, 32), "white").save(plain)
    assert await check.check(plain, "image") == []
    garbage = tmp_path / "garbage.jpg"
    garbage.write_bytes(b"not an image")
    assert await check.check(garbage, "image") == []


# ---- metadata ------------------------------------------------------------------------------


async def test_metadata_markers(tmp_path) -> None:
    check = metadata.MetadataCheck()

    sd = tmp_path / "sd.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a lemon curing cancer, Steps: 30, Sampler: Euler a, Seed: 1")
    Image.new("RGB", (32, 32)).save(sd, pnginfo=info)
    [s] = await check.check(sd, "image")
    assert s.strength is S.STRONG

    firefly = tmp_path / "ff.jpg"
    exif = Image.Exif()
    exif[0x0131] = "Adobe Firefly Image 3"  # Software
    Image.new("RGB", (32, 32)).save(firefly, exif=exif)
    [s] = await check.check(firefly, "image")
    assert s.strength is S.MODERATE and "Firefly" in s.detail

    xmp = tmp_path / "xmp.jpg"
    buf = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buf, format="JPEG")
    packet = (
        b"<x:xmpmeta><Iptc4xmpExt:DigitalSourceType>http://cv.iptc.org/newscodes/"
        b"digitalsourcetype/trainedAlgorithmicMedia</Iptc4xmpExt:DigitalSourceType></x:xmpmeta>"
    )
    xmp.write_bytes(buf.getvalue()[:2] + packet + buf.getvalue()[2:])
    [s] = await check.check(xmp, "image")
    assert s.strength is S.STRONG

    camera = tmp_path / "cam.jpg"
    exif = Image.Exif()
    exif[0x0131] = "Snapseed 2.0"
    Image.new("RGB", (32, 32)).save(camera, exif=exif)
    assert await check.check(camera, "image") == []


# ---- classifiers ---------------------------------------------------------------------------


def test_classifier_parsers_and_thresholds() -> None:
    se = {"status": "success", "type": {"ai_generated": 0.93, "deepfake": 0.1}}
    assert classifiers.parse_sightengine(se) == 0.93
    assert classifiers.parse_sightengine({"status": "failure"}) is None
    hive = {
        "status": [
            {
                "response": {
                    "output": [
                        {
                            "classes": [
                                {"class": "ai_generated", "score": 0.2},
                                {"class": "not_ai_generated", "score": 0.8},
                                {"class": "deepfake", "score": 0.6},
                            ]
                        }
                    ]
                }
            }
        ]
    }
    assert classifiers.parse_hive(hive) == 0.6
    labels = classifiers.ai_score_from_labels
    assert labels([{"label": "human", "score": 0.7}, {"label": "artificial", "score": 0.3}]) == 0.3
    assert labels([{"label": "Real", "score": 0.8}]) == pytest.approx(0.2)
    assert labels([{"label": "LABEL_0", "score": 0.9}]) is None
    assert classifiers.to_signal("hive", "image", 0.4) == []
    assert classifiers.to_signal("hive", "image", 0.7)[0].strength is S.WEAK
    assert classifiers.to_signal("hive", "image", 0.95)[0].strength is S.MODERATE


async def test_sightengine_request_shape(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "type": {"ai_generated": 0.8}})

    img = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(img)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        score = await classifiers.SightengineClassifier(http, "u", "s").score([img])
    assert score == 0.8
    body = seen[0].content
    assert b'name="models"' in body and b"genai,deepfake" in body and b'name="media"' in body


# ---- reverse image search ------------------------------------------------------------------

VISION = {
    "responses": [
        {
            "webDetection": {
                "pagesWithMatchingImages": [
                    {"url": "https://blog.example.org/x", "pageTitle": "Blog"},
                    {
                        "url": "https://news.example.com/flood-2015",
                        "pageTitle": "<b>Floods</b> hit the city",
                        "fullMatchingImages": [{"url": "https://news.example.com/a.jpg"}],
                    },
                    {"url": "ftp://bad"},
                ],
                "bestGuessLabels": [{"label": "flood"}],
            }
        }
    ]
}
OLD_ARTICLE = b"""<html><head><meta property="og:title" content="Floods hit the city">
<meta property="article:published_time" content="2015-08-02T10:00:00Z"></head>
<body><p>Heavy rain flooded the old town on Sunday, officials said today.</p></body></html>"""


def test_parse_web_detection() -> None:
    pages, labels = reverse_image.parse_web_detection(VISION)
    assert [p.url for p in pages] == [
        "https://news.example.com/flood-2015",
        "https://blog.example.org/x",
    ]
    assert pages[0].title == "Floods hit the city" and labels == ["flood"]


async def test_recycled_photo_found_and_pages_become_evidence(fake_dns, tmp_path) -> None:  # noqa: F811
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "vision.googleapis.com":
            assert request.url.params["key"] == "k"
            return httpx.Response(200, json=VISION)
        if request.url.host == "news.example.com":
            return httpx.Response(200, content=OLD_ARTICLE)
        return httpx.Response(404)

    img = tmp_path / "flood.jpg"
    Image.new("RGB", (64, 64), "blue").save(img)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        search = reverse_image.ReverseImageSearch(http, "k")
        signals, evidence = await search.check(img, "image", today=date(2026, 10, 22))
    [s] = signals
    assert (s.finding, s.strength) == ("recycled", S.STRONG)
    assert "2015-08-02" in s.detail and "news.example.com" in s.detail
    assert evidence[0].published == "2015-08-02" and "The same picture" in evidence[0].quote
    assert evidence[1].quote.startswith("A similar picture")


async def test_recent_copies_are_not_recycled(fake_dns, tmp_path) -> None:  # noqa: F811
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "vision.googleapis.com":
            return httpx.Response(200, json=VISION)
        return httpx.Response(200, content=OLD_ARTICLE)

    img = tmp_path / "x.jpg"
    Image.new("RGB", (8, 8)).save(img)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        signals, evidence = await reverse_image.ReverseImageSearch(http, "k").check(
            img, "image", today=date(2015, 8, 10)
        )
    assert signals == [] and evidence  # pages still help the judges


# ---- detector + pipeline -------------------------------------------------------------------


class _Fixed:
    def __init__(self, name: str, score: float | None = None, error: bool = False) -> None:
        self.name, self._score, self._error = name, score, error
        self.calls: list[list[Path]] = []

    async def score(self, paths):  # type: ignore[no-untyped-def]
        self.calls.append(list(paths))
        if self._error:
            raise RuntimeError("down")
        return self._score


async def test_detector_survives_failures_and_samples_video_frames(tmp_path) -> None:
    img = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(img)
    ok, broken = _Fixed("hive", 0.95), _Fixed("sightengine", error=True)
    detector = AIDetector(frame_classifiers=[ok, broken])
    signals, evidence = await detector.check_image(img)
    assert [s.detector for s in signals] == ["hive"] and evidence == []

    frames = [img] * 8
    await detector.check_video(img, frames)
    assert len(ok.calls[-1]) == 4  # frames sampled for paid classifiers


async def test_ai_signals_flow_to_report_warnings_and_evidence(
    tmp_path, spanish_health_extraction
) -> None:
    from app.core.models import ComposedReply
    from app.pipeline import Deps, run_check
    from tests.fakes import FakeSource

    class FakeReverse:
        async def check(self, path, media):  # type: ignore[no-untyped-def]
            draft = EvidenceDraft(
                source=SourceKind.WEB,
                tier=2,
                title="Old flood photo",
                url="https://news.example.com/flood-2015",
                quote="The same picture appears on this page, published 2015-08-02",
            )
            return [_sig("reverse_image", "recycled", S.STRONG, detail="Online since 2015.")], [
                draft
            ]

    llm = FakeLLM().queue("image", _analysis(possibly_ai_generated=True))
    llm.queue("extract", spanish_health_extraction)
    llm.queue(
        "compose",
        ComposedReply(
            verdict_label="x",
            explanation="x",
            confidence_label="x",
            warnings=[],
            safety_note=None,
            sources_heading="x",
        ),
    )
    img = tmp_path / "flood.png"
    Image.new("RGB", (16, 16)).save(img)
    media = MediaProviders(
        llm=llm,
        fetcher=FakeFetcher({"f": img}),
        ai=AIDetector(frame_classifiers=[_Fixed("hive", 0.7)], reverse_search=FakeReverse()),
    )
    report = await run_check(
        _sub(Part(kind=PartKind.IMAGE, media_ref="f")),
        Deps(llm, [FakeSource(SourceKind.WEB)], media=media),
    )
    assert report.ai_report.overall == "possibly_ai"  # vision_llm + hive, both weak
    assert W_POSSIBLY_AI in report.warnings and "Online since 2015." in report.warnings
    assert any(e.url == "https://news.example.com/flood-2015" for e in report.evidence)


async def test_ai_text_signal_is_weak_and_thresholded() -> None:
    long_text = "Certainly! Here is the important news you asked for. " * 6
    llm = FakeLLM().queue("ai_text", AITextAssessment(likelihood=0.9, cues=["chatbot opener"]))
    [s] = await check_text(llm, long_text)
    assert s.strength is S.WEAK and s.media == "text"
    llm = FakeLLM().queue("ai_text", AITextAssessment(likelihood=0.6, cues=[]))
    assert await check_text(llm, long_text) == []
    assert await check_text(FakeLLM(), "short") == []  # too short: no call at all
    assert await check_text(FakeLLM(), long_text) == []  # LLM failure: no signal


async def test_media_without_detector_still_works(tmp_path) -> None:
    llm = FakeLLM().queue("image", _analysis())
    img = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(img)
    with temp_workdir() as wd:
        out = await process_submission(
            _sub(Part(kind=PartKind.IMAGE, media_ref="f")),
            MediaProviders(llm=llm, fetcher=FakeFetcher({"f": img})),
            wd,
        )
    assert out.ai_signals == [] and out.evidence_seed == []
