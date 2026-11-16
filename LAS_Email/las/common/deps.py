"""Dependency container: every external system this service touches.

This service only reads the mailbox and extracts (handoff-only, 2026-11-16): the mailbox, the
object store, the AV scanner, the document parser and the LLM. Core LAS is reached over HTTP by
``las/core_handoff.py``; nothing here sends mail or calls a partner.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from las.common.db import SessionFactory, get_session_factory
from las.common.interfaces import AVScanner, DocumentParser, LLMClient, ObjectStore
from las.common.llm import MeteredLLM
from las.common.settings import Settings, get_settings


@dataclass
class Deps:
    settings: Settings
    db: SessionFactory
    store: ObjectStore
    av: AVScanner
    parser: DocumentParser
    llm_client: LLMClient

    @property
    def llm(self) -> MeteredLLM:
        return MeteredLLM(self.llm_client, self.db, self.settings)


def assert_no_fakes_in_prod(deps: Deps) -> None:
    """Defence in depth: raise if any external-system field is a ``las.common.fakes`` test double
    while running in a real environment (a fake AV scanner would pass every file as clean)."""
    if deps.settings.env not in ("staging", "prod"):
        return
    fakeish = [
        f.name for f in fields(deps) if type(getattr(deps, f.name)).__module__ == "las.common.fakes"
    ]
    if fakeish:
        raise RuntimeError(f"fake adapters wired in {deps.settings.env}: {', '.join(fakeish)}")


def build_fake_deps(settings: Settings, db: SessionFactory) -> Deps:
    from las.common import fakes

    return Deps(
        settings=settings,
        db=db,
        store=fakes.FakeObjectStore(),
        av=fakes.FakeAVScanner(),
        parser=fakes.FakeDocumentParser(),
        llm_client=fakes.FakeLLM(),
    )


def build_deps(settings: Settings | None = None, db: SessionFactory | None = None) -> Deps:
    """Production wiring from settings. Fakes are only allowed outside staging/prod."""
    from las.attachments.av import ClamdScanner
    from las.attachments.parser import LiteParseParser
    from las.common import fakes
    from las.common.gemini import GeminiClient
    from las.common.storage import LocalEncryptedStore

    s = settings or get_settings()
    prod = s.env in ("staging", "prod")

    def require(cond: bool, what: str) -> None:
        if prod and not cond:
            raise RuntimeError(f"{what} is not configured")

    require(bool(s.core_intake_url), "CORE_INTAKE_URL")
    require(bool(s.core_intake_api_key.get_secret_value()), "CORE_INTAKE_API_KEY")

    store = LocalEncryptedStore.from_settings(
        s.local_store_path,
        s.local_store_key_b64.get_secret_value(),
        public_base_url=s.public_base_url,
        signing_key=s.url_signing_key.get_secret_value(),
        allow_dev_key=s.env in ("dev", "test"),
    )

    av: AVScanner = (
        ClamdScanner(s.clamd_host, s.clamd_port)
        if s.av_scanner == "clamd"
        else fakes.FakeAVScanner()
    )
    if s.av_scanner != "clamd":
        require(False, "ClamAV")

    parser: DocumentParser = (
        LiteParseParser(
            ocr_language=s.ocr_language,
            max_pages=s.parser_max_pages,
            tessdata_path=s.tessdata_path or None,
        )
        if s.document_parser == "liteparse"
        else fakes.FakeDocumentParser()
    )

    if not s.gemini_use_vertex:
        require(bool(s.gemini_api_key.get_secret_value()), "GEMINI_API_KEY")
    llm_client: LLMClient = GeminiClient(
        api_key=s.gemini_api_key.get_secret_value(),
        vertex=s.gemini_use_vertex,
        project=s.gemini_vertex_project,
        location=s.gemini_vertex_location,
        provider_name=s.llm_provider_name,
    )

    deps = Deps(
        settings=s,
        db=db or get_session_factory(),
        store=store,
        av=av,
        parser=parser,
        llm_client=llm_client,
    )
    assert_no_fakes_in_prod(deps)
    return deps
