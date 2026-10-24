"""Les doublures d'un test de cas d'usage de `documents`, réunies."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from documents.fakes import (
    NOW,
    FakeCvSource,
    FakeEngine,
    FakeTemplateJobs,
    InMemoryGeneratedCvRepository,
    InMemoryTemplateRepository,
    InMemoryUserTemplateRepository,
)
from doubles import FakeClock
from karria.modules.documents.application import CvRenderer, ExportCv, PreviewTemplate
from karria.shared.config import StorageSettings
from karria.shared.storage import InMemoryObjectStorage


@dataclass
class Env:
    templates: InMemoryTemplateRepository = field(default_factory=InMemoryTemplateRepository)
    owned: InMemoryUserTemplateRepository = field(default_factory=InMemoryUserTemplateRepository)
    generated: InMemoryGeneratedCvRepository = field(default_factory=InMemoryGeneratedCvRepository)
    cvs: FakeCvSource = field(default_factory=FakeCvSource)
    engine: FakeEngine = field(default_factory=FakeEngine)
    jobs: FakeTemplateJobs = field(default_factory=FakeTemplateJobs)
    storage: InMemoryObjectStorage = field(
        default_factory=lambda: InMemoryObjectStorage(StorageSettings(), "test")
    )
    clock: FakeClock = field(default_factory=lambda: FakeClock(NOW))
    user: UUID = field(default_factory=uuid4)

    def renderer(self) -> CvRenderer:
        return CvRenderer(self.templates, self.owned, self.cvs, self.engine, self.storage)

    def preview(self) -> PreviewTemplate:
        return PreviewTemplate(self.renderer(), self.engine, self.storage)

    def export(self) -> ExportCv:
        return ExportCv(
            renderer=self.renderer(),
            templates=self.templates,
            owned=self.owned,
            generated=self.generated,
            engine=self.engine,
            storage=self.storage,
            jobs=self.jobs,
            clock=self.clock,
        )


@pytest.fixture
def env() -> Env:
    return Env()
