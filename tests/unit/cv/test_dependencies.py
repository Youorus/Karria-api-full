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
    get_photo_repository,
    get_portrait_enhancer,
    get_upload_inspector,
)
from karria.modules.cv.infrastructure import (
    FileInspector,
    LlmChapterInterpreter,
    LlmCvExtractor,
    LlmPortraitEnhancer,
    LlmTranscriber,
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
)
from karria.shared.clock import SystemClock
from karria.shared.config import GeminiSettings, Settings
from karria.shared.llm import DisabledLanguageModel, build_language_model

pytestmark = pytest.mark.unit


def test_les_ports_de_persistance_et_dinspection_ont_leur_adaptateur() -> None:
    assert isinstance(get_clock(), SystemClock)
    assert isinstance(get_draft_repository(), TortoiseDraftRepository)
    assert isinstance(get_cv_repository(), TortoiseCvRepository)
    assert isinstance(get_photo_repository(), TortoisePhotoRepository)
    assert isinstance(get_upload_inspector(), FileInspector)


def test_les_adaptateurs_recoivent_le_port_llm_route() -> None:
    llm = build_language_model(Settings(gemini=GeminiSettings(api_key=SecretStr("k"))))

    assert isinstance(get_chapter_interpreter(llm), LlmChapterInterpreter)
    assert isinstance(get_audio_transcriber(llm), LlmTranscriber)
    assert isinstance(get_portrait_enhancer(llm), LlmPortraitEnhancer)
    assert isinstance(get_cv_extractor(llm), LlmCvExtractor)


def test_sans_aucune_cle_le_port_refuse_a_l_appel() -> None:
    assert isinstance(build_language_model(Settings()), DisabledLanguageModel)
