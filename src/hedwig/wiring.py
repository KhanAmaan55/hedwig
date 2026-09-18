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
from hedwig.brain.stubs import DefaultMindReader, PermissiveGuard, StubResponder, StubToolRunner
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
    EmotionReader,
    EventBus,
    FileStorage,
    LLMProvider,
    PluginLoader,
    ServiceRegistry,
    StateManager,
    TaskScheduler,
)
from hedwig.core.ports.scheduler import Priority, TaskSpec
from hedwig.core.registry import InMemoryServiceRegistry
from hedwig.core.scheduler import AsyncTaskScheduler, IntervalTrigger
from hedwig.core.state import SqliteStateManager
from hedwig.core.storage import ContentAddressedStorage
from hedwig.core.store import Database, MigrationRunner
from hedwig.emotion import EmotionEngine, EmotionMindReader, EmotionStore
from hedwig.llm import LanguageModelGateway, OllamaProvider, RecordedProvider
from hedwig.memory import (
    SUBSCRIPTION,
    CaptureService,
    HybridRetrieval,
    MemoryMaintenance,
    MemoryRecaller,
    SqliteMemoryStore,
    TurnCaptureSubscriber,
)
from hedwig.sessions import SqliteSessionStore

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
    emotion: EmotionReader | None
    memory: SqliteMemoryStore
    retrieval: HybridRetrieval
    capture: CaptureService
    maintenance: MemoryMaintenance
    brain: Brain
    plugins: PluginLoader

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
    provider: LLMProvider = (
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
        database, clock=clock, window_size=config.memory.recent_turn_window
    )

    # 7. Memory. Episodic and semantic stores, retrieval, capture and the decay pass
    #    (docs/06).
    memory = SqliteMemoryStore(database, clock=clock, bus=bus)
    retrieval = HybridRetrieval(database, clock=clock)
    capture = CaptureService(memory, clock=clock, max_per_turn=config.memory.max_captures_per_turn)
    maintenance = MemoryMaintenance(
        database,
        memory,
        clock=clock,
        bus=bus,
        half_life_days=config.memory.decay_half_life_days,
        forget_threshold=config.memory.forget_threshold,
    )

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

    # 9. The turn graph, now a closed loop: it observes into the conversation log, recalls
    #    from real memory, and announces what happened so memory can learn from it
    #    (docs/26 §3). Capture is a *subscriber*, not a node, so it can take its time.
    brain = Brain(
        Collaborators(
            guard=PermissiveGuard(),
            mind=EmotionMindReader(emotion) if emotion else DefaultMindReader(),
            recaller=MemoryRecaller(retrieval, store=memory),
            planner=RulePlanner(),
            responder=StubResponder(),
            tools=StubToolRunner(),
            conversation=sessions,
            announcer=BusAnnouncer(bus),
        )
    )
    bus.subscribe(
        "conversation.turn.completed",
        TurnCaptureSubscriber(capture, memory).handle,
        name=SUBSCRIPTION,
    )

    # 10. Lifecycle. The registry starts these in dependency order and stops them in reverse.
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
    if emotion is not None:
        registry.register("emotion", emotion, depends_on=("bus",), critical=False)
    registry.register("brain", brain, depends_on=("bus", "memory", "sessions"), critical=False)

    # 11. Plugins last: they register services of their own against the registry above.
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
        emotion=emotion,
        memory=memory,
        retrieval=retrieval,
        capture=capture,
        maintenance=maintenance,
        brain=brain,
        plugins=plugins,
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
