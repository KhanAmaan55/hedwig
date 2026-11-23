"""Composition root.

The only module that knows both ports and concrete implementations (docs/03 §6).
Everything else receives what it needs through its constructor.

A plain function, not a DI framework: it reads top to bottom, it is where startup ordering
lives, and swapping an implementation is a one-line edit in a file whose entire purpose is
to be edited. Tests call `build_container(clock=FakeClock(), ...)` and get a fully wired
system with no patching.

**Construction and lifecycle are separate concerns.** This function builds the graph and
nothing else — no I/O beyond opening the database, no tasks started. The `ServiceRegistry`
then starts things in dependency order and stops them in reverse (`container.start()`).
Keeping the two apart is what makes it possible to build a container in a test, inspect it,
and never start it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from hedwig import __version__
from hedwig.brain import Brain, BusAnnouncer, Collaborators, RulePlanner
from hedwig.brain.guard import RuleGuard
from hedwig.brain.stubs import DefaultMindReader
from hedwig.core.bus import InProcessBus, platform_catalogue
from hedwig.core.clock import SystemClock
from hedwig.core.config import Config, load_config
from hedwig.core.config_manager import LayeredConfigManager
from hedwig.core.logging import fields, get_logger
from hedwig.core.plugins import AllowlistPluginLoader
from hedwig.core.ports import (
    Clock,
    ConfigManager,
    Conversation,
    ConversationHistory,
    EmotionReader,
    EventBus,
    FileStorage,
    LLMProvider,
    PluginLoader,
    ServiceRegistry,
    StateManager,
    TaskScheduler,
)
from hedwig.core.ports.brain import MindReader
from hedwig.core.ports.personality import PersonaControl, PersonaReader, RelationshipReader, Trait
from hedwig.core.ports.scheduler import Priority, TaskSpec
from hedwig.core.ports.speech import Listener, Speaker
from hedwig.core.registry import InMemoryServiceRegistry
from hedwig.core.scheduler import AsyncTaskScheduler, DailyTrigger, IntervalTrigger
from hedwig.core.state import SqliteStateManager
from hedwig.core.storage import ContentAddressedStorage
from hedwig.core.store import Database, MigrationRunner
from hedwig.core.store.backups import Backups
from hedwig.curiosity import CuriosityStore, Explorer, FindingsMindReader, GapDetector
from hedwig.emotion import EmotionEngine, EmotionMindReader, EmotionStore
from hedwig.goals import GoalsMindReader, GoalStore
from hedwig.knowledge.documents import DocumentLibrary
from hedwig.knowledge.web import WebClient
from hedwig.llm import LanguageModelGateway, OllamaProvider, RecordedProvider
from hedwig.llm.responder import GatewayResponder
from hedwig.memory import (
    SUBSCRIPTION,
    CaptureService,
    EmbeddingIndex,
    HybridRetrieval,
    MemoryMaintenance,
    MemoryRecaller,
    RelationshipMemory,
    SqliteMemoryStore,
    TurnCaptureSubscriber,
)
from hedwig.personality import DriftLimits, PersonalityEngine, PersonalityStore, PersonaMindReader
from hedwig.privacy import Exporter, Purger
from hedwig.reflection import NightlyReflection, SessionReflector
from hedwig.sessions import SqliteSessionStore
from hedwig.speech import PiperSpeaker, WhisperListener
from hedwig.tools import (
    ToolRegistry,
    calculate_tool,
    datetime_tool,
    research_memory,
    research_tool,
    search_memory_tool,
)

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True, slots=True)
class Container:
    """Everything the application is built from.

    Frozen, and typed against ports rather than implementations. If a field's type is a
    concrete class, that is a bug: it means something cannot be replaced.
    """

    config: Config
    clock: Clock
    version: str
    started_at: datetime
    _started_monotonic: float

    # Milestone 2 — core infrastructure.
    database: Database
    registry: ServiceRegistry
    config_manager: ConfigManager
    bus: EventBus
    state: StateManager
    storage: FileStorage
    scheduler: TaskScheduler
    llm: LanguageModelGateway
    sessions: Conversation
    history: ConversationHistory
    """The same store as `sessions`, typed by the other role it plays: reading the log back
    for clients (docs/16 §12.3). The brain sees only `sessions`."""
    emotion: EmotionReader | None
    personality: PersonaReader | None
    relationship: RelationshipReader
    memory: SqliteMemoryStore
    retrieval: HybridRetrieval
    capture: CaptureService
    maintenance: MemoryMaintenance
    brain: Brain
    plugins: PluginLoader
    goals: GoalStore
    curiosity: CuriosityStore
    explorer: Explorer
    speaker: Speaker
    listener: Listener
    nightly: NightlyReflection
    exporter: Exporter
    purger: Purger
    library: DocumentLibrary
    embeddings: EmbeddingIndex
    """Typed so a document ingest can embed its passages at once rather than in ten minutes."""
    """The user's documents (KNW-01). Retrieval reads its tables; only it writes them."""
    """Export and erase (SEC-02, ADR-0024): the one place that reads and deletes every table."""
    persona_control: PersonaControl | None
    """The same engine as `personality`, typed by the role the inspector needs (PER-01)."""

    def uptime_seconds(self) -> float:
        """Elapsed time since startup, measured monotonically.

        Monotonic on purpose: a clock adjustment or a laptop waking from sleep must not
        make uptime jump or go negative.
        """
        return max(0.0, self.clock.monotonic() - self._started_monotonic)

    async def start(self) -> None:
        """Start every service in dependency order, then load plugins.

        Plugins come last because they register services of their own and may depend on any
        of the platform being available.
        """
        await self.registry.start_all()
        await self.plugins.load_all()
        await self.bus.emit(
            "system.startup.completed",
            {"version": self.version},
            source="wiring",
        )

    async def stop(self) -> None:
        """Reverse of `start`. Never raises: shutdown has to finish."""
        try:
            await self.bus.emit("system.shutdown.started", {}, source="wiring")
        except Exception:  # the bus may already be stopping
            logger.debug("could not publish shutdown event")
        await self.plugins.unload_all()
        await self.registry.stop_all()
        self.database.close()


def build_container(
    config: Config | None = None,
    *,
    clock: Clock | None = None,
    database: Database | None = None,
    migrate: bool = True,
    llm_provider: LLMProvider | None = None,
    web: WebClient | None = None,
) -> Container:
    """Construct the application graph.

    The order below is the order things must be built in; keep it that way. Each service
    receives its dependencies explicitly, so the graph is readable as a list rather than
    discovered by tracing imports.
    """
    config = config or load_config()
    clock = clock or SystemClock()

    # 1. Storage substrate. Everything durable depends on it, so it is first and it is the
    #    only thing here that touches the disk during construction.
    database = database or Database(config.database_path)
    if migrate:
        MigrationRunner(
            database, MIGRATIONS_DIR, clock=clock, backup_dir=config.runtime.backups_dir
        ).run()

    # 2. Configuration manager — provenance and hot reload over the frozen Config.
    config_manager = LayeredConfigManager(config)

    # 3. Event bus over the outbox. Nothing subscribes yet; subscriptions are registered by
    #    the modules that own them, which do not exist until later milestones.
    bus = InProcessBus(
        database,
        clock=clock,
        catalogue=platform_catalogue(),
        queue_size=config.bus.queue_size,
        handler_timeout=config.bus.handler_timeout_seconds,
        replay_window=timedelta(hours=config.bus.replay_window_hours),
        strict_types=config.bus.strict_types,
    )

    # 4. Services that write through the bus.
    state = SqliteStateManager(database, clock=clock, bus=bus)
    storage = ContentAddressedStorage(
        config.runtime.blobs_dir,
        database,
        clock=clock,
        bus=bus,
        quota_bytes=config.storage.quota_bytes,
        max_blob_bytes=config.storage.max_blob_bytes,
    )
    scheduler = AsyncTaskScheduler(
        database,
        clock=clock,
        bus=bus,
        tick_seconds=config.scheduler.tick_seconds,
        max_concurrent=config.scheduler.max_concurrent,
    )

    # 5. The language faculty. Constructed with the provider its configuration names, so a
    #    test gets a scripted provider and production gets Ollama without a branch anywhere
    #    else (docs/14 §4).
    provider: LLMProvider = llm_provider or (
        RecordedProvider()
        if config.llm.provider == "recorded"
        else OllamaProvider(
            config.llm.endpoint,
            request_timeout=config.llm.request_timeout_seconds,
            connect_timeout=config.llm.connect_timeout_seconds,
            stall_timeout=config.llm.stall_timeout_seconds,
        )
    )
    llm = LanguageModelGateway(provider, config=config.llm, clock=clock, database=database, bus=bus)

    # 6. The conversation log: sessions, messages, turns, and the recent-turn window that
    #    short-term memory is a query over (docs/05 §5.1, docs/06 §5.1).
    sessions = SqliteSessionStore(
        database,
        clock=clock,
        bus=bus,
        window_size=config.memory.recent_turn_window,
        idle_for=timedelta(minutes=config.sessions.idle_timeout_minutes),
    )
    # Sessions end after a spell of inactivity, and the relationship update runs then
    # (docs/27 §5.1). Until this existed, sessions never ended.
    scheduler.register(
        TaskSpec(
            name="sessions.end_idle",
            handler=sessions.end_idle_task,
            trigger=IntervalTrigger(seconds=config.sessions.reap_interval_seconds),
            priority=Priority.MAINTENANCE,
            description="End sessions idle past the timeout.",
            max_runtime_seconds=60.0,
        )
    )

    # 7. Memory. Episodic and semantic stores, retrieval, capture and the decay pass
    #    (docs/06).
    memory = SqliteMemoryStore(database, clock=clock, bus=bus)
    # Recall by meaning (docs/06 §17.2): written by events, repaired by a task, and off — with
    # memory's health saying why — when sqlite-vec or the embedding model is missing.
    embeddings = EmbeddingIndex(
        database,
        llm,
        clock=clock,
        model=config.llm.embedding,
        query_prefix=config.llm.embedding_query_prefix,
        document_prefix=config.llm.embedding_document_prefix,
        enabled=config.memory.semantic_recall,
    )
    for kind in ("memory.episode.stored", "memory.belief.formed"):
        bus.subscribe(kind, embeddings.handle, name=f"memory-embeddings-{kind.split('.')[1]}")
    scheduler.register(
        TaskSpec(
            name="memory.embed",
            handler=embeddings.task,
            trigger=IntervalTrigger(seconds=600),
            priority=Priority.MAINTENANCE,
            description="Embed memories that have no vector, a stale one, or another model's.",
            max_runtime_seconds=300.0,
        )
    )
    retrieval = HybridRetrieval(database, clock=clock, semantic=embeddings)
    capture = CaptureService(memory, clock=clock, max_per_turn=config.memory.max_captures_per_turn)
    maintenance = MemoryMaintenance(
        database,
        memory,
        clock=clock,
        bus=bus,
        half_life_days=config.memory.decay_half_life_days,
        forget_threshold=config.memory.forget_threshold,
    )
    # Social memory (docs/06 §3.3). In `memory` because docs/05 puts `relation` there;
    # personality reads it through `RelationshipReader` and never writes it.
    relationship = RelationshipMemory(database, clock=clock, bus=bus)

    # 8. Emotion. Deterministic, rule-driven, no model anywhere in it (docs/09 §4.2).
    #    Optional: a user who wants a flat companion is a legitimate configuration, and
    #    `DefaultMindReader` is exactly what a flat companion is.
    emotion: EmotionEngine | None = None
    if config.emotion.enabled:
        emotion = EmotionEngine(
            EmotionStore(database, clock=clock),
            clock=clock,
            bus=bus,
            timezone=config.runtime.timezone,
            min_publish_delta=config.emotion.min_publish_delta,
        )
        # The 30-second coalescing tick (docs/09 §5.2). It is a scheduled task rather than
        # a loop of its own so it inherits catch-up, health and cancellation for free.
        scheduler.register(
            TaskSpec(
                name="emotion.tick",
                handler=emotion.tick_task,
                trigger=IntervalTrigger(seconds=config.emotion.tick_seconds),
                priority=Priority.MAINTENANCE,
                description="Integrate appraisals and decay toward baseline.",
                max_runtime_seconds=30.0,
            )
        )

    # 9. Personality (docs/10, docs/27). Bootstraps its anchors and identity core at
    #    construction — idempotent, like a migration, and never resetting a drifted trait.
    personality: PersonalityEngine | None = None
    if config.personality.enabled:
        personality = PersonalityEngine(
            PersonalityStore(database),
            clock=clock,
            bus=bus,
            limits=DriftLimits(
                per_proposal_cap=config.personality.per_proposal_cap,
                weekly_budget=config.personality.weekly_budget,
                lifetime_cap=config.personality.lifetime_cap,
                instruction_step=config.personality.instruction_step,
            ),
            anchors={Trait(name): value for name, value in config.personality.anchors.items()},
        )

    # 9b. Goals (docs/08 §6.1). What HEDWIG is helping with over time, swept daily.
    goals = GoalStore(database, clock=clock, bus=bus)
    scheduler.register(
        TaskSpec(
            name="goals.lifecycle",
            handler=goals.sweep_task,
            trigger=DailyTrigger(hour=config.memory.maintenance_hour),
            priority=Priority.MAINTENANCE,
            description="Expire stale proposals, block stalled goals, cap the active set.",
            max_runtime_seconds=60.0,
        )
    )

    # One reading of cognition per turn. With personality, it composes personality, the
    # relationship and emotion through their ports — importing none of them (ADR-0008).
    # Goals wrap whichever reader that is, by composition (docs/08 §6.1).
    base_mind: MindReader = (
        PersonaMindReader(personality, emotion=emotion, relationship=relationship)
        if personality
        else EmotionMindReader(emotion)
        if emotion
        else DefaultMindReader()
    )
    # Curiosity (docs/11 §13): the register always, exploring only when opted in twice.
    curiosity = CuriosityStore(database, clock=clock, bus=bus)
    mind = GoalsMindReader(FindingsMindReader(base_mind, curiosity, clock), goals)

    # 10. The turn graph, now a closed loop: it observes into the conversation log, recalls
    #    from real memory, and announces what happened so memory can learn from it
    #    (docs/26 §3). Capture is a *subscriber*, not a node, so it can take its time.
    # 10a. Tools (docs/13 §12). Read-only today; every call audited in `tool_call`.
    tools = ToolRegistry(database=database, clock=clock)
    tools.register(datetime_tool(clock, timezone=config.runtime.timezone))
    tools.register(calculate_tool())
    tools.register(search_memory_tool(retrieval))
    # Web research, opt-in (docs/13 §13). Absent, the planner says so instead of guessing.
    if config.knowledge.web_enabled:
        web = web or WebClient(
            database,
            clock,
            search_endpoint=config.knowledge.search_endpoint,
            timeout=config.knowledge.timeout_seconds,
        )
        tools.register(research_tool(web, remember=research_memory(capture)))

    brain = Brain(
        Collaborators(
            guard=RuleGuard(),
            mind=mind,
            recaller=MemoryRecaller(retrieval, store=memory),
            planner=RulePlanner(available_tools=frozenset(tools.names())),
            # The language model, at last (docs/27 §6). Every reply is generated from a
            # prompt that carries the identity core and the rendered directives.
            responder=GatewayResponder(llm, context_window=config.llm.context_window),
            tools=tools,
            conversation=sessions,
            announcer=BusAnnouncer(bus),
        )
    )
    bus.subscribe(
        "conversation.turn.completed",
        TurnCaptureSubscriber(capture, memory).handle,
        name=SUBSCRIPTION,
    )

    gap_detector = GapDetector(curiosity, bus=bus)
    explorer = Explorer(
        store=curiosity,
        tools=tools,
        gateway=llm,
        history=sessions,
        clock=clock,
        emotion=emotion,
        bus=bus,
        enabled=config.curiosity.enabled and config.knowledge.web_enabled,
        idle_minutes=config.curiosity.idle_minutes,
        max_daily=config.curiosity.max_explorations_daily,
        quiet_start=config.curiosity.quiet_start_hour,
        quiet_end=config.curiosity.quiet_end_hour,
        timezone=config.runtime.timezone,
    )
    scheduler.register(
        TaskSpec(
            name="curiosity.explore",
            handler=explorer.task,
            trigger=IntervalTrigger(seconds=config.curiosity.interval_minutes * 60),
            priority=Priority.BACKGROUND,
            description="Research one open gap, when idle and opted in (docs/11 §13).",
            max_runtime_seconds=120.0,
        )
    )

    # 10c. Voice (docs/15 §13): Piper as a separate process. Absent, the app stays text-only.
    speaker: Speaker = PiperSpeaker(
        command=config.speech.command,
        voice=config.speech.voice,
        voices_dir=config.speech.voices_dir or config.runtime.data_dir / "voices",
        timeout_seconds=config.speech.timeout_seconds,
    )
    listener: Listener = WhisperListener(
        model=config.speech.listen_model,
        models_dir=(config.speech.voices_dir or config.runtime.data_dir / "voices") / "whisper",
        max_seconds=config.speech.listen_max_seconds,
    )

    # 10b. Reflection, T1 (docs/12 §14): when a conversation ends, keep a summary and the
    #      facts the user stated, as tentative beliefs. A subscriber, like capture.
    reflector: SessionReflector | None = None
    if config.reflection.enabled:
        reflector = SessionReflector(
            history=sessions,
            gateway=llm,
            capture=capture,
            store=memory,
            bus=bus,
            goals=goals,
            min_user_messages=config.reflection.min_user_messages,
            max_messages=config.reflection.max_transcript_messages,
            max_facts=config.reflection.max_facts,
        )

    # 10d. Reflection, T2 (docs/12 §15): the nightly run. Decay and forgetting are its last
    #      stage now (they were the standalone `memory.maintenance` task, MEM-01).
    nightly = NightlyReflection(
        database=database,
        store=memory,
        capture=capture,
        maintenance=maintenance,
        history=sessions,
        gateway=llm,
        clock=clock,
        timezone=config.runtime.timezone,
        auto_apply=config.reflection.auto_apply_judgements,
        drift=personality,  # evidence in; the guardrails decide (docs/10 §13)
    )
    scheduler.register(
        TaskSpec(
            name="reflection.nightly",
            handler=nightly.task,
            trigger=DailyTrigger(hour=config.memory.maintenance_hour),
            priority=Priority.MAINTENANCE,
            description="Summarise days, merge and judge beliefs, promote, decay and forget.",
            max_runtime_seconds=900.0,
        )
    )

    # 11. Lifecycle. The registry starts these in dependency order and stops them in reverse.
    registry = InMemoryServiceRegistry()
    registry.register("config", config_manager)
    registry.register("bus", bus, depends_on=("config",))
    registry.register("state", state, depends_on=("bus",))
    registry.register("storage", storage, depends_on=("bus",))
    if config.scheduler.enabled:
        registry.register("scheduler", scheduler, depends_on=("bus",), critical=False)
    # Not critical: a missing model is a fixable situation, and HEDWIG is more useful
    # running and saying so than refusing to boot (docs/17 §3).
    registry.register("llm", llm, depends_on=("bus",), critical=False)
    registry.register("sessions", sessions, depends_on=("bus",))
    registry.register("memory", memory, depends_on=("bus",))
    registry.register("maintenance", maintenance, depends_on=("memory",), critical=False)
    registry.register("embeddings", embeddings, depends_on=("memory", "llm"), critical=False)
    registry.register("relationship", relationship, depends_on=("memory",), critical=False)
    if emotion is not None:
        registry.register("emotion", emotion, depends_on=("bus",), critical=False)
    if personality is not None:
        # After emotion, so emotion is already listening when personality announces the
        # profile at startup — otherwise baselines would stay neutral until the first drift.
        registry.register(
            "personality",
            personality,
            depends_on=("bus", "emotion") if emotion is not None else ("bus",),
            critical=False,
        )
    registry.register("goals", goals, depends_on=("bus",), critical=False)
    registry.register("curiosity", gap_detector, depends_on=("bus",), critical=False)
    registry.register("brain", brain, depends_on=("bus", "memory", "sessions"), critical=False)
    if reflector is not None:
        registry.register(
            "reflection",
            reflector,
            depends_on=("bus", "memory", "sessions", "llm", "goals"),
            critical=False,
        )

    # Nightly backups, checked before they are kept (SEC-03, docs/05 §7).
    backups = Backups(database, config.runtime.backups_dir, clock=clock)
    scheduler.register(
        TaskSpec(
            name="storage.backup",
            handler=backups.task,
            trigger=DailyTrigger(hour=config.memory.maintenance_hour),
            priority=Priority.MAINTENANCE,
            description="Back up the database, check the copy, keep 7 daily + 4 weekly + 12 monthly.",
            max_runtime_seconds=600.0,
        )
    )

    # Local documents (KNW-01, docs/13 §14): read only under folders the user adds.
    library = DocumentLibrary(
        database,
        clock=clock,
        data_dir=config.runtime.data_dir,
        max_bytes=config.knowledge.document_max_bytes,
        max_files=config.knowledge.document_max_files,
    )
    if config.knowledge.documents_enabled:
        scheduler.register(
            TaskSpec(
                name="knowledge.reindex",
                handler=library.task,
                trigger=IntervalTrigger(seconds=3600),
                priority=Priority.MAINTENANCE,
                description="Re-read the user's document folders: new, changed and deleted files.",
                max_runtime_seconds=600.0,
            )
        )

    # The user's control over their data (SEC-02). Purge waits for the bus to drain and refuses
    # while a turn runs, so nothing in flight can re-create what it erases (ADR-0024).
    exporter = Exporter(database=database, config=config, clock=clock, version=__version__)
    purger = Purger(
        database=database,
        clock=clock,
        backups_dir=config.runtime.backups_dir,
        is_busy=lambda: brain.busy,
        drain=bus.drain,
    )

    # 12. Plugins last: they register services of their own against the registry above.
    plugins = AllowlistPluginLoader(
        config.plugins.enabled,
        config=config,
        registry=registry,
        settings=config.plugins.settings,
    )

    container = Container(
        config=config,
        clock=clock,
        version=__version__,
        started_at=clock.now(),
        _started_monotonic=clock.monotonic(),
        database=database,
        registry=registry,
        config_manager=config_manager,
        bus=bus,
        state=state,
        storage=storage,
        scheduler=scheduler,
        llm=llm,
        sessions=sessions,
        history=sessions,
        emotion=emotion,
        personality=personality,
        relationship=relationship,
        memory=memory,
        retrieval=retrieval,
        capture=capture,
        maintenance=maintenance,
        brain=brain,
        plugins=plugins,
        goals=goals,
        curiosity=curiosity,
        explorer=explorer,
        speaker=speaker,
        listener=listener,
        nightly=nightly,
        exporter=exporter,
        purger=purger,
        library=library,
        embeddings=embeddings,
        persona_control=personality,
    )

    logger.debug(
        "container built",
        extra=fields(
            environment=config.runtime.environment,
            database=str(database.path),
            services=list(registry.names()),
            plugins=list(config.plugins.enabled),
            clock=type(clock).__name__,
        ),
    )
    return container
