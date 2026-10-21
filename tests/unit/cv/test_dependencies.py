"""Le câblage par défaut : chaque port reçoit l'adaptateur réellement déployé."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from karria.modules.cv.api.dependencies import (
    get_audio_transcriber,
    get_chapter_interpreter,
    get_clock,
    get_cv_extractor,
    get_cv_repository,
    get_draft_repository,
    get_image_transport,
    get_photo_repository,
    get_portrait_enhancer,
    get_text_transport,
    get_upload_inspector,
)
from karria.modules.cv.infrastructure import (
    DisabledTransport,
    FileInspector,
    GeminiChapterInterpreter,
    GeminiClient,
    GeminiCvExtractor,
    GeminiPortraitEnhancer,
    GeminiTranscriber,
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
)
from karria.shared.clock import SystemClock
from karria.shared.config import GeminiSettings, Settings

pytestmark = pytest.mark.unit


def test_les_ports_de_persistance_et_dinspection_ont_leur_adaptateur() -> None:
    assert isinstance(get_clock(), SystemClock)
    assert isinstance(get_draft_repository(), TortoiseDraftRepository)
    assert isinstance(get_cv_repository(), TortoiseCvRepository)
    assert isinstance(get_photo_repository(), TortoisePhotoRepository)
    assert isinstance(get_upload_inspector(), FileInspector)


def test_sans_cle_les_transports_refusent_a_lappel() -> None:
    settings = Settings()

    assert isinstance(get_text_transport(settings), DisabledTransport)
    assert isinstance(get_image_transport(settings), DisabledTransport)


def test_avec_une_cle_les_adaptateurs_gemini_sont_assembles() -> None:
    settings = Settings(gemini=GeminiSettings(api_key=SecretStr("k")))
    text = get_text_transport(settings)
    image = get_image_transport(settings)

    assert isinstance(text, GeminiClient)
    assert isinstance(image, GeminiClient)
    assert isinstance(get_chapter_interpreter(text, settings), GeminiChapterInterpreter)
    assert isinstance(get_audio_transcriber(text, settings), GeminiTranscriber)
    assert isinstance(get_portrait_enhancer(image, settings), GeminiPortraitEnhancer)
    assert isinstance(get_cv_extractor(text, settings), GeminiCvExtractor)
