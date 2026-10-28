from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import shutil
import threading
import time
import traceback

import numpy as np

from app.asr.factory import build_asr_engine
from app.asr.worker import ASRJob, ASRResult, ASRWorker
from app.audio.pyaudio_capture import PyAudioCapture, boost_current_audio_thread_priority
from app.audio.mic_guard import MicDuplexGuard
from app.audio.noise_reducer import AdaptiveNoiseReducer
from app.config import AppConfig
from app.speakers.diarizer import SpeakerDiarizer
from app.speakers.worker import SpeakerProbe, SpeakerTask, SpeakerWorker
from app.storage.session_store import SessionStore, TranslationEntry
from app.transcript.manager import TranscriptManager
from app.translation.factory import build_translation_engine
from app.translation.types import TranslationJob, TranslationResult
from app.translation.worker import TranslationWorker
from app.ui.meeting_window import MeetingUIBus, MeetingWindow, UIEvent
from app.vad.segmenter import StreamingVadSegmenter
from app.vad.silero_onnx import SileroOnnxVad


@dataclass(slots=True)
class _Lane:
    source: PyAudioCapture
    segmenter: StreamingVadSegmenter
    worker: ASRWorker
    backend: str
    language: str
    threads: int


class MeetingSTTPipeline:
    """Two-lane realtime STT + final-only EN/JA -> Vietnamese translation."""

    def __init__(self, config: AppConfig, *, ui_bus: MeetingUIBus | None = None, prepared_models=None) -> None:
        self.config = config
        self._prepared_models = prepared_models
        resume_dir = (config.reliability.resume_session or "").strip() or None
        self.store = SessionStore(
            config.storage.output_dir,
            postprocess=config.postprocess,
            reliability=config.reliability,
            session_dir=resume_dir,
        )
        self._timeline_offset_s = (
            self.store.timeline_end_s + max(0.0, float(config.reliability.resume_gap_s))
            if self.store.resumed and self.store.timeline_end_s > 0 else 0.0
        )
        self._utterance_offsets = {
            "mic": self.store.next_utterance_id("mic"),
            "system": self.store.next_utterance_id("system"),
        }
        self._last_health = time.monotonic()
        snapshot = self.store.session_dir / "session_config.json"
        if not snapshot.exists():
            snapshot.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")

        self.ui_bus = ui_bus if ui_bus is not None else (MeetingUIBus() if config.meeting.ui_enabled else None)
        self.transcript = TranscriptManager(
            self.store,
            show_metrics=config.asr.show_metrics,
            show_live=config.asr.show_live and not config.meeting.ui_enabled,
            live_max_chars=config.asr.live_max_chars,
            live_show_metrics=config.asr.live_show_metrics,
            meeting_mode=True,
            show_language=True,
        )
        self._session_start = 0.0
        self._stop = threading.Event()
        self._audio_mode_lock = threading.Lock()
        # Only open the physical inputs selected by the user. Mode transitions
        # reconcile capture devices outside the real-time audio loop.
        self._capture_switch_lock = threading.Lock()
        self._capture_started = False
        self._capture_error: RuntimeError | None = None
        self._active_capture_sources: set[str] = set()
        self._lane_config_lock = threading.RLock()
        # Ignore slow model builds if the user has switched language again.
        self._language_requests = {"mic": 0, "system": 0}
        self._audio_mode = self._normalize_audio_mode(config.meeting.audio_mode)
        self._pending_lane_resets: set[str] = set()
        self._last_backlog_warn = 0.0
        self.speaker_worker: SpeakerWorker | None = None
        self._last_speaker_probe = {'mic': -100.0, 'system': -100.0}
        self._speaker_generation = {'mic': 0, 'system': 0}
        self._speaker_uid = {
            'mic': self.store.next_utterance_id('mic'),
            'system': self.store.next_utterance_id('system'),
        }
        if config.speakers.enabled:
            try:
                speaker_engine = (self._prepared_models.take_speaker(True)
                                  if self._prepared_models is not None else None)
                if speaker_engine is None:
                    speaker_engine = SpeakerDiarizer(
                        config.speakers.segmentation_path,
                        config.speakers.embedding_path,
                        sample_rate=config.audio.target_sample_rate,
                        threads=config.speakers.cpu_threads,
                        min_turn_s=config.speakers.min_turn_s,
                        similarity_threshold=config.speakers.similarity_threshold,
                    )
                self.speaker_worker = SpeakerWorker(
                    speaker_engine, self._speaker_final_callback,
                    max_backlog=config.speakers.max_backlog,
                    probe_callback=(self._speaker_probe_callback if config.speakers.early_split_enabled else None),
                )
                print('[SPEAKERS] ONNX ready: REMOTE only (MIC bypassed)')
            except Exception as exc:
                print(f'[SPEAKERS] Unavailable; continuing normal ASR without diarization: {exc}')

        # Audio level UI is coalesced in MeetingUIBus, so this never floods the
        # normal STT/translation event queue.
        self._last_meter_publish = {"mic": 0.0, "system": 0.0}

        # AUTO language should feel continuous, not flip JA/EN on every short
        # utterance. Partials never change the sticky language; finals need
        # strong script evidence or repeated evidence before switching.
        self._auto_language_state = {
            "mic": {"stable": None, "candidate": None, "votes": 0},
            "system": {"stable": None, "candidate": None, "votes": 0},
        }

        mg = config.mic_guard
        self.mic_guard = MicDuplexGuard(
            sample_rate=config.audio.target_sample_rate,
            enabled=mg.enabled,
            min_dbfs=mg.min_dbfs,
            noise_margin_db=mg.noise_margin_db,
            echo_enabled=mg.echo_enabled,
            echo_correlation=mg.echo_correlation,
            echo_window_ms=mg.echo_window_ms,
            echo_max_delay_ms=mg.echo_max_delay_ms,
            echo_lag_step_ms=mg.echo_lag_step_ms,
            downsample=mg.downsample,
        )

        nr = config.noise_reduction
        self.noise_reducer = AdaptiveNoiseReducer(
            sample_rate=config.audio.target_sample_rate,
            enabled=nr.enabled,
            highpass_hz=nr.highpass_hz,
            strength=nr.strength,
            spectral_floor=nr.spectral_floor,
            learn_rate=nr.learn_rate,
            speech_protect_ratio=nr.speech_protect_ratio,
            min_learn_dbfs=nr.min_learn_dbfs,
            max_learn_dbfs=nr.max_learn_dbfs,
        )

        total = max(2, int(config.asr.cpu_threads))
        # Keep a stable per-lane budget so language switching does not require
        # rebuilding both lanes. Japanese and Vietnamese are both valid on YOU/REMOTE.
        mic_threads = max(1, total // 2)
        system_threads = max(1, total - mic_threads)
        self.lanes: dict[str, _Lane] = {}
        self._add_lane("mic", config.meeting.mic_language, config.audio.mic_device_index, mic_threads)
        self._add_lane("system", config.meeting.system_language, config.audio.system_device_index, system_threads)

        self.translation_worker: TranslationWorker | None = None
        self.translation_available = False
        self.translation_status = "disabled"
        if config.translation.enabled:
            try:
                engine = (self._prepared_models.take_translator(True)
                          if self._prepared_models is not None else None)
                if engine is None:
                    engine = build_translation_engine(config)
                self.translation_worker = TranslationWorker(
                    engine,
                    self._handle_translation,
                    self._handle_translation_error,
                )
                self.translation_available = True
                self.translation_status = f"{config.translation.backend} ready (lazy load)"
            except Exception as exc:
                # Translation is optional: STT/capture must remain usable even if the
                # model or Optimum runtime has not been prepared yet.
                self.translation_status = str(exc)
                print(f"\n[TRANSLATION WARN] {exc}")
        else:
            self.translation_status = "disabled in config"

    @staticmethod
    def _normalize_audio_mode(mode: str) -> str:
        value = str(mode or "both").strip().lower()
        aliases = {"system": "remote", "remote_only": "remote", "mic": "you", "you_only": "you"}
        value = aliases.get(value, value)
        return value if value in {"both", "remote", "you"} else "both"

    def _get_audio_mode(self) -> str:
        with self._audio_mode_lock:
            return self._audio_mode

    def _mode_allows(self, source: str, mode: str | None = None) -> bool:
        mode = self._normalize_audio_mode(mode or self._get_audio_mode())
        return mode == "both" or (mode == "remote" and source == "system") or (mode == "you" and source == "mic")

    def set_audio_mode(self, mode: str) -> None:
        mode = self._normalize_audio_mode(mode)
        with self._audio_mode_lock:
            old = self._audio_mode
            self._audio_mode = mode
            if old != mode:
                for source_name in ("mic", "system"):
                    if not self._mode_allows(source_name, mode):
                        self._pending_lane_resets.add(source_name)
                        self._speaker_generation[source_name] += 1
        if old == mode:
            return
        for source_name, lane in self.lanes.items():
            if not self._mode_allows(source_name, mode):
                lane.worker.clear_pending(source_name, include_finals=True)
                if self.translation_worker is not None:
                    self.translation_worker.clear_pending(source_name)
                if self.speaker_worker is not None:
                    self.speaker_worker.clear_pending(source_name)
        # Stop/start ports asynchronously. PortAudio readiness may take seconds;
        # it must never block the Tk/UI thread or the audio/VAD loop.
        if self._capture_started and not self._stop.is_set():
            threading.Thread(target=self._sync_capture_sources,
                             name="soundtext-capture-switch", daemon=True).start()
        if self.ui_bus is not None:
            for source in ("mic", "system"):
                if not self._mode_allows(source, mode):
                    self.ui_bus.clear_audio_level(source)
        print(f"\n[MODE] Capture: {mode.upper()}")

    def _sync_capture_sources(self) -> None:
        """Start/stop only the allowed physical capture devices, serially.

        Queues and ASR/VAD workers stay intact so switching source never
        rebuilds expensive recognition models. A second switch waits for the
        first and reconciles against the most recent requested mode.
        """
        with self._capture_switch_lock:
            if not self._capture_started or self._stop.is_set():
                return
            desired = {source for source in ("mic", "system") if self._mode_allows(source)}
            for source in tuple(self._active_capture_sources - desired):
                self.lanes[source].source.stop()
                self._active_capture_sources.discard(source)
                if self.ui_bus is not None:
                    self.ui_bus.clear_audio_level(source)
                print(f"[AUDIO] {source.upper()} capture stopped")
            for source in ("system", "mic"):
                if source in desired and source not in self._active_capture_sources:
                    if self._stop.is_set():
                        return
                    try:
                        self.lanes[source].source.start()
                    except Exception as exc:
                        message = f"Cannot activate {source.upper()} audio device: {exc}"
                        print(f"[AUDIO ERROR] {message}")
                        if self.ui_bus is not None:
                            self.ui_bus.publish(UIEvent(source=source, stage="status", message=message))
                        # Never continue displaying a mode whose requested
                        # recording source failed to activate.
                        self._capture_error = RuntimeError(message)
                        self._stop.set()
                        return
                    self._active_capture_sources.add(source)
                    print(f"[AUDIO] {source.upper()} capture started")

    def _take_mode_and_resets(self) -> tuple[str, set[str]]:
        with self._audio_mode_lock:
            mode = self._audio_mode
            resets = set(self._pending_lane_resets)
            self._pending_lane_resets.clear()
        return mode, resets

    def _segmenter(self, source_name: str) -> StreamingVadSegmenter:
        c = self.config
        if source_name == "mic":
            # Direct microphone speech contains natural pauses, breath noise and room
            # reflections.  Give Gipformer a longer coherent context instead of
            # finalising every 0.5 s pause.  REMOTE/system keeps the proven V15.1
            # profile unchanged.
            threshold = c.vad.mic_threshold
            negative_threshold = c.vad.mic_negative_threshold
            min_silence_ms = c.vad.mic_min_silence_ms
            pre_roll_ms = c.vad.mic_pre_roll_ms
            post_roll_ms = c.vad.mic_post_roll_ms
            min_speech_ms = c.vad.mic_min_speech_ms
            partial_interval_ms = c.vad.mic_partial_interval_ms
            partial_window_s = c.vad.mic_partial_window_s
            max_utterance_s = c.vad.mic_max_utterance_s
            hard_split_overlap_ms = c.vad.mic_hard_split_overlap_ms
        else:
            threshold = c.vad.threshold
            negative_threshold = c.vad.negative_threshold
            min_silence_ms = c.vad.min_silence_ms
            pre_roll_ms = c.vad.pre_roll_ms
            post_roll_ms = c.vad.post_roll_ms
            min_speech_ms = c.vad.min_speech_ms
            partial_interval_ms = c.vad.partial_interval_ms
            partial_window_s = c.vad.partial_window_s
            max_utterance_s = c.vad.max_utterance_s
            hard_split_overlap_ms = c.vad.hard_split_overlap_ms
        vad = (self._prepared_models.take_vad(source_name)
               if self._prepared_models is not None else None)
        if vad is None:
            vad = SileroOnnxVad(c.vad.model_path, sample_rate=c.audio.target_sample_rate)
        return StreamingVadSegmenter(
            vad=vad,
            sample_rate=c.audio.target_sample_rate,
            threshold=threshold,
            negative_threshold=negative_threshold,
            min_silence_ms=min_silence_ms,
            pre_roll_ms=pre_roll_ms,
            post_roll_ms=post_roll_ms,
            min_speech_ms=min_speech_ms,
            partial_interval_ms=partial_interval_ms,
            partial_window_s=partial_window_s,
            max_utterance_s=max_utterance_s,
            hard_split_overlap_ms=hard_split_overlap_ms,
        )

    def _add_lane(self, source_name: str, language: str, device_index: int | None, threads: int) -> None:
        c = self.config
        prepared = (self._prepared_models.take_asr(source_name, language, threads)
                    if self._prepared_models is not None else None)
        if prepared is not None:
            engine, backend = prepared
        else:
            from app.runtime_memory import is_low_memory_frozen
            mode = self._get_audio_mode()
            # An inactive lane must not load its full ASR engine on an 8 GB PC.
            # For BOTH we prefer REMOTE/SenseVoice, deferring MIC until speech.
            defer = is_low_memory_frozen() and (
                not self._mode_allows(source_name, mode)
                or (mode == "both" and source_name == "mic")
            )
            if defer:
                from app.asr.deferred_engine import DeferredASREngine
                engine = DeferredASREngine(c, language=language, threads=threads)
                backend = engine.backend
                print(f"[RAM SAFE] {source_name.upper()} recognition deferred until speech")
            else:
                from app.runtime_memory import log_memory, ensure_available_memory
                log_memory(f"before session ASR {source_name}")
                ensure_available_memory("khởi tạo ASR", minimum_gib=2.0)
                engine, backend = build_asr_engine(c, language=language, threads=threads)
                log_memory(f"after session ASR {source_name}")
        capture = PyAudioCapture(
            source=source_name,
            target_sample_rate=c.audio.target_sample_rate,
            device_index=device_index,
            frames_per_buffer=c.audio.frames_per_buffer,
            queue_seconds=c.audio.queue_seconds,
            resampler_backend=c.audio.resampler_backend,
            resampler_quality=c.audio.resampler_quality,
            channel_mode=c.audio.mic_channel_mode if source_name == "mic" else c.audio.system_channel_mode,
        )
        # One inference thread per lane keeps FunASR/Gipformer state simple. Finals
        # are prioritized over previews by ASRWorker; without MT this stays realtime.
        worker = self._new_asr_worker(engine, source_name)
        self.lanes[source_name] = _Lane(capture, self._segmenter(source_name), worker, backend, language, threads)


    @staticmethod
    def _normalize_language(language: str) -> str:
        value = str(language or "").strip().lower()
        aliases = {
            "vietnamese": "vi", "vn": "vi",
            "japanese": "ja", "jp": "ja",
            "english": "en", "eng": "en",
            "automatic": "auto", "ja+en": "auto", "ja/en": "auto",
        }
        value = aliases.get(value, value)
        if value not in {"vi", "ja", "en", "auto"}:
            raise ValueError(
                f"Unsupported STT language: {language!r}. Choose vi, ja, en, or auto."
            )
        return value

    @staticmethod
    def _ja_en_script_evidence(text: str) -> tuple[str | None, int, int]:
        """Return strong script evidence plus JA/Latin character counts."""
        value = str(text or "")
        latin = sum(1 for ch in value if ch.isascii() and ch.isalpha())
        japanese = sum(
            1
            for ch in value
            if (
                "\u3040" <= ch <= "\u30ff"
                or "\u3400" <= ch <= "\u4dbf"
                or "\u4e00" <= ch <= "\u9fff"
            )
        )

        # Japanese speech may contain occasional Latin acronyms, but a normal
        # Japanese sentence still has plenty of kana/kanji.
        if japanese >= 5 and japanese >= max(3, int(latin * 0.55)):
            return "ja", japanese, latin

        # Pure English turns are normally emitted as Latin text. Requiring a
        # useful amount of Latin text avoids switching for one-word interjections.
        if latin >= 10 and japanese <= 1:
            return "en", japanese, latin

        return None, japanese, latin

    @staticmethod
    def _normalize_detected_ja_en(detected_language: str | None) -> str | None:
        detected = str(detected_language or "").strip().lower()
        detected = {
            "english": "en", "eng": "en",
            "japanese": "ja", "jp": "ja",
        }.get(detected, detected)
        return detected if detected in {"ja", "en"} else None

    def _resolve_asr_language(
        self,
        *,
        source_name: str,
        configured_language: str,
        detected_language: str | None,
        text: str,
        final: bool,
    ) -> str:
        configured = self._normalize_language(configured_language)
        if configured != "auto":
            return configured

        state = self._auto_language_state.setdefault(
            source_name,
            {"stable": None, "candidate": None, "votes": 0},
        )

        script_signal, japanese, latin = self._ja_en_script_evidence(text)
        detected = self._normalize_detected_ja_en(detected_language)
        signal = script_signal or detected

        if signal is None:
            # Weak fallback only for the first utterance before a stable language
            # exists. Once sticky state exists, ambiguous text inherits it.
            if state["stable"] in {"ja", "en"}:
                return str(state["stable"])
            signal = "en" if latin >= 3 and latin > japanese * 1.5 else "ja"

        # Partials are display previews only. Never let them flip AUTO language.
        if not final:
            return str(state["stable"] or signal)

        stable = state["stable"]
        if stable not in {"ja", "en"}:
            state["stable"] = signal
            state["candidate"] = None
            state["votes"] = 0
            return signal

        if signal == stable:
            state["candidate"] = None
            state["votes"] = 0
            return str(stable)

        # A clearly long turn in the other script can switch immediately.
        # Short code-switches such as "Please" inside a Japanese conversation do
        # not break continuity; they need repeated evidence.
        immediate = (
            (signal == "en" and latin >= 22 and japanese <= 1)
            or (signal == "ja" and japanese >= 14)
        )
        if immediate:
            state["stable"] = signal
            state["candidate"] = None
            state["votes"] = 0
            print(f"\n[AUTO LANG] {source_name}: {stable.upper()} -> {signal.upper()} (strong evidence)")
            return signal

        if state["candidate"] == signal:
            state["votes"] = int(state["votes"]) + 1
        else:
            state["candidate"] = signal
            state["votes"] = 1

        if int(state["votes"]) >= 2:
            state["stable"] = signal
            state["candidate"] = None
            state["votes"] = 0
            print(f"\n[AUTO LANG] {source_name}: {stable.upper()} -> {signal.upper()} (confirmed)")
            return signal

        # First conflicting short utterance inherits the current stable language.
        return str(stable)

    def _new_asr_worker(self, engine, source_name: str) -> ASRWorker:
        """Suppress in-flight results from a retired worker after a model swap."""
        worker: ASRWorker

        def handle_current(result: ASRResult) -> None:
            lane = self.lanes.get(source_name)
            if lane is not None and lane.worker is worker:
                self._handle_asr(result)

        worker = ASRWorker(engine, handle_current, final_engine=engine, parallel=False)
        return worker

    def _reset_lane_for_language_change(self, source_name: str) -> None:
        """Let the audio thread reset VAD; never mutate it during push()."""
        with self._audio_mode_lock:
            self._pending_lane_resets.add(source_name)
        self._speaker_generation[source_name] += 1
        self._last_speaker_probe[source_name] = -100.0
        if self.speaker_worker is not None:
            self.speaker_worker.clear_pending(source_name)
        if self.translation_worker is not None:
            self.translation_worker.clear_pending(source_name)

    @staticmethod
    def _retire_asr_worker(worker: ASRWorker) -> None:
        """Retire outside pipeline locks so callbacks can finish safely."""
        try:
            worker.clear_pending(include_finals=True)
            worker.stop(drain=False)
        except Exception as exc:
            print(f"[ASR WARN] Failed retiring old worker: {exc}")

    def set_lane_language(self, source_name: str, language: str) -> None:
        """Switch languages without blocking capture or rebuilding SenseVoice.

        JA/EN/AUTO share the same SenseVoice weights and worker. Only a genuine
        change to/from Gipformer needs a new model, constructed outside locks.
        """
        if source_name not in {"mic", "system"}:
            return
        language = self._normalize_language(language)
        with self._lane_config_lock:
            self._language_requests[source_name] += 1
            request = self._language_requests[source_name]
            lane = self.lanes[source_name]
            previous = lane.language
            if previous == language:
                return

            # Fast path: this is a language *setting*, not a new model.  Never
            # join an ASR worker or reload FunASR when JA -> AUTO (or EN -> JA).
            if lane.backend == "sensevoice" and language in {"ja", "en", "auto"}:
                engine = lane.worker.live_engine
                if not hasattr(engine, "language"):
                    raise RuntimeError("SenseVoice engine has no language setting")
                engine.language = language
                lane.language = language
                lane.worker.clear_pending(source_name, include_finals=True)
                if source_name == "mic":
                    self.config.meeting.mic_language = language
                else:
                    self.config.meeting.system_language = language
                self._auto_language_state[source_name] = {
                    "stable": previous if language == "auto" and previous in {"ja", "en"} else None,
                    "candidate": None, "votes": 0,
                }
                self._reset_lane_for_language_change(source_name)
                print(f"\n[LANG] {source_name.upper()}: {language.upper()} / SenseVoice (reused, no reload)")
                return
            threads = lane.threads

        # Slow path (VI <-> JA/EN): keep the current worker and audio running
        # while new weights load.  The UI already calls this on a daemon thread.
        try:
            engine, backend = build_asr_engine(self.config, language=language, threads=threads)
            new_worker = self._new_asr_worker(engine, source_name)
            new_worker.start()
        except Exception as exc:
            print(f"\n[LANG ERROR] {source_name}: unable to prepare {language}: {exc}")
            if self.ui_bus is not None:
                self.ui_bus.publish(UIEvent(source=source_name, stage="status", message=f"Không đổi được model: {exc}"))
            return

        with self._lane_config_lock:
            if request != self._language_requests[source_name] or self._stop.is_set():
                stale = True
                old_worker = None
            else:
                stale = False
                lane = self.lanes[source_name]
                old_worker = lane.worker
                lane.worker = new_worker
                lane.backend = backend
                lane.language = language
                if source_name == "mic":
                    self.config.meeting.mic_language = language
                else:
                    self.config.meeting.system_language = language
                self._auto_language_state[source_name] = {
                    "stable": None, "candidate": None, "votes": 0,
                }
                self._reset_lane_for_language_change(source_name)

        threading.Thread(
            target=self._retire_asr_worker,
            args=((new_worker if stale else old_worker),),
            name=f"retire-{source_name}-asr", daemon=True,
        ).start()
        if not stale:
            print(f"\n[LANG] {source_name.upper()}: {language.upper()} / {backend} (swapped)")

    def _handle_asr(self, result: ASRResult) -> None:
        if not self._mode_allows(result.source):
            return

        # Reject a late fixed-language ASR result from the model that was active
        # before a UI switch. In AUTO mode, SenseVoice's per-utterance JA/EN LID
        # is authoritative and is allowed to differ from the configured "auto".
        with self._lane_config_lock:
            current_language = self.lanes[result.source].language

        if current_language != "auto" and result.language:
            try:
                if self._normalize_language(result.language) != current_language:
                    return
            except ValueError:
                return

        if not result.text.strip():
            return

        source_language = self._resolve_asr_language(
            source_name=result.source,
            configured_language=current_language,
            detected_language=result.language,
            text=result.text,
            final=result.kind == "final",
        )
        # Make transcript/storage/UI agree on the resolved language. ASRResult is
        # mutable and this happens before TranscriptManager consumes it.
        result.language = source_language

        entry = self.transcript.handle(result)
        if result.kind == "final" and entry is not None:
            text = entry.source_text or result.text.strip()
        else:
            text = (
                self.store.cleaner.normalize_segment(
                    result.text.strip(), source_language, terminal=False
                )
                or result.text.strip()
            )

        if self.ui_bus is not None:
            latency_ms = 0.0
            if result.audio_end_monotonic > 0 and result.completed_monotonic > 0:
                latency_ms = max(
                    0.0,
                    (result.completed_monotonic - result.audio_end_monotonic) * 1000.0,
                )
            self.ui_bus.publish(UIEvent(
                source=result.source,
                utterance_id=result.utterance_id,
                stage="asr",
                final=result.kind == "final",
                source_text=text,
                source_language=source_language,
                target_language="vi",
                stt_latency_ms=latency_ms,
                speaker_id=result.speaker_id,
                speaker_overlap=result.speaker_overlap,
            ))

        # Stay final-only: speculative partial translation would increase CPU
        # use and may display unstable or misleading Japanese sentences.
        # Realtime CPU policy: translate authoritative finals only. Vietnamese
        # input is already the target language and is not sent through translation.
        if result.kind == "final" and source_language != "vi" and self.translation_worker is not None and any(ch.isalnum() for ch in text):
            self.translation_worker.submit(TranslationJob(
                source=result.source,
                utterance_id=result.utterance_id,
                text=text,
                source_language=source_language,
                target_language="vi",
                asr_completed_monotonic=result.completed_monotonic,
            ))

    def _handle_translation(self, result: TranslationResult) -> None:
        # If the user changed the lane language while this job was in-flight,
        # discard the old-language result rather than placing it under new text.
        with self._lane_config_lock:
            lane = self.lanes.get(result.source)
            if lane is None:
                return
            if lane.language == "auto":
                if result.source_language not in {"ja", "en"}:
                    return
            elif lane.language != result.source_language:
                return

        if result.translated_text.strip():
            self.store.append_translation(TranslationEntry(
                utterance_id=result.utterance_id,
                source=result.source,
                source_language=result.source_language,
                target_language="vi",
                source_text=result.source_text,
                translated_text=result.translated_text,
                inference_s=result.inference_s,
            ))
            print(
                f"    -> [VI  ] {result.translated_text}"
                f"  [MT {result.inference_s:.2f}s / wait {result.queue_wait_s:.2f}s]"
            )

        if self.ui_bus is not None:
            self.ui_bus.publish(UIEvent(
                source=result.source,
                utterance_id=result.utterance_id,
                stage="translation",
                final=True,
                source_text=result.source_text,
                translated_text=result.translated_text,
                source_language=result.source_language,
                target_language="vi",
                translation_latency_ms=max(0.0, result.inference_s * 1000.0),
                translation_wait_ms=max(0.0, result.queue_wait_s * 1000.0),
            ))

    def _handle_translation_error(self, message: str, job: TranslationJob) -> None:
        if self.ui_bus is not None:
            self.ui_bus.publish(UIEvent(
                source=job.source,
                utterance_id=job.utterance_id,
                stage="status",
                final=False,
                message=f"MT error: {message}",
            ))

    def _speaker_final_callback(
        self, task: SpeakerTask, audio: np.ndarray, start: float, end: float,
        speaker_id: str | None, overlap: bool,
    ) -> None:
        if task.source == "mic":
            return  # MIC intentionally bypasses speaker segmentation.
        # Called from the speaker worker thread; final ASR IDs must not collide
        # with VAD preview IDs. Translation/UI match using this final ID.
        if not self._mode_allows(task.source) or task.generation != self._speaker_generation[task.source]:
            return
        with self._lane_config_lock:
            if task.generation != self._speaker_generation[task.source]:
                return
            uid = self._speaker_uid[task.source]
            self._speaker_uid[task.source] += 1
            worker = self.lanes[task.source].worker
        worker.submit(ASRJob(
            kind='final', utterance_id=uid, audio=audio,
            start_s=task.start_s + start + self._timeline_offset_s,
            end_s=task.start_s + end + self._timeline_offset_s,
            source=task.source, leading_overlap_s=(task.leading_overlap_s if start <= 0.01 else 0.0),
            audio_end_monotonic=(self._session_start + task.start_s + end) if self._session_start else 0.0,
            speaker_id=speaker_id, speaker_overlap=overlap,
        ))

    def _speaker_probe_callback(self, probe: SpeakerProbe, offset_s: float) -> None:
        if probe.source == "mic":
            return
        if probe.generation != self._speaker_generation[probe.source]:
            return
        if not self._mode_allows(probe.source):
            return
        segmenter = self.lanes[probe.source].segmenter
        # The request is tied to a VAD utterance ID. Obsolete probes can never
        # split later utterances or audio from a changed source/language.
        segmenter.request_speaker_split(probe.utterance_id, probe.start_s + offset_s)

    def _submit(self, source_name: str, event) -> None:
        if source_name == 'system' and self.speaker_worker is not None and event.kind == 'partial' and self.config.speakers.early_split_enabled:
            interval = max(1.8, float(self.config.speakers.probe_interval_s))
            if event.end_s - event.start_s >= 2.6 and event.end_s - self._last_speaker_probe[source_name] >= interval:
                self._last_speaker_probe[source_name] = event.end_s
                self.speaker_worker.submit_probe(SpeakerProbe(
                    source=source_name, audio=event.audio,
                    start_s=event.start_s, utterance_id=event.utterance_id,
                    generation=self._speaker_generation[source_name],
                    submitted=time.monotonic(),
                ))
        if source_name == 'system' and self.speaker_worker is not None and event.kind == 'final':
            self.speaker_worker.submit(SpeakerTask(
                source=source_name, audio=event.audio, start_s=event.start_s,
                end_s=event.end_s, leading_overlap_s=event.leading_overlap_s,
                generation=self._speaker_generation[source_name],
            ))
            return
        with self._lane_config_lock:
            lane = self.lanes[source_name]
            worker = lane.worker
        worker.submit(ASRJob(
            kind=event.kind,
            utterance_id=(-1 - event.utterance_id if (source_name == 'system' and self.speaker_worker is not None) else event.utterance_id + self._utterance_offsets.get(source_name, 0)),
            audio=event.audio,
            start_s=event.start_s + self._timeline_offset_s,
            end_s=event.end_s + self._timeline_offset_s,
            source=source_name,
            leading_overlap_s=event.leading_overlap_s,
            audio_end_monotonic=(self._session_start + event.end_s) if self._session_start else 0.0,
        ))

    @staticmethod
    def _audio_level(samples) -> tuple[float, float]:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return -96.0, -96.0
        rms = float(np.sqrt(np.mean(x * x, dtype=np.float64) + 1e-12))
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        dbfs = 20.0 * math.log10(max(rms, 1e-9))
        peak_dbfs = 20.0 * math.log10(max(peak, 1e-9))
        return max(-96.0, dbfs), max(-96.0, peak_dbfs)

    def _publish_audio_level(self, source: str, raw, cleaned=None) -> None:
        if self.ui_bus is None:
            return
        now = time.monotonic()
        # ~12.5 FPS is visually smooth but negligible compared with ASR work.
        if now - self._last_meter_publish.get(source, 0.0) < 0.08:
            return
        self._last_meter_publish[source] = now
        raw_dbfs, peak_dbfs = self._audio_level(raw)
        clean_dbfs = None
        if cleaned is not None:
            clean_dbfs, _ = self._audio_level(cleaned)
        try:
            self.ui_bus.publish_audio_level(
                source,
                dbfs=raw_dbfs,
                peak_dbfs=peak_dbfs,
                clean_dbfs=clean_dbfs,
            )
        except Exception:
            pass

    def _clean_mic_chunk(self, samples):
        """Clean MIC only. REMOTE/system audio is intentionally untouched."""
        return self.noise_reducer.process(samples)

    def _audio_loop(self) -> None:
        try:
            boost_current_audio_thread_priority()
            self._audio_loop_inner()
        except Exception as exc:
            # Never let capture die silently.  A visible traceback makes device/storage
            # failures diagnosable instead of looking like the microphone simply stopped.
            print(f"\n[AUDIO ERROR] capture loop crashed: {exc}")
            traceback.print_exc()
            # STT-only UIEvent has only source/text/language/final. Do not use
            # the old V16 translation/status schema here. The traceback above is
            # the authoritative diagnostic and must never trigger a second error.
            if self.ui_bus is not None:
                try:
                    self.ui_bus.publish(UIEvent(
                        source="mic",
                        utterance_id=-1,
                        stage="status",
                        final=False,
                        message=f"Audio capture error: {exc}",
                    ))
                except Exception as ui_exc:
                    print(f"[UI WARN] could not publish audio error: {ui_exc}")
            self._stop.set()

    def _audio_loop_inner(self) -> None:

        def take_burst(lane: _Lane, first_timeout: float = 0.008, limit: int = 8):
            first = lane.source.read(timeout=first_timeout)
            if first is None:
                return []
            items = [first]
            for _ in range(limit - 1):
                extra = lane.source.read(timeout=0.0)
                if extra is None:
                    break
                items.append(extra)
            return items

        while not self._stop.is_set():
            mode, resets = self._take_mode_and_resets()
            system_chunks = take_burst(self.lanes["system"]) if self._mode_allows("system", mode) else []
            mic_chunks = take_burst(self.lanes["mic"]) if self._mode_allows("mic", mode) else []
            had_audio = bool(system_chunks or mic_chunks)
            for source_name in resets:
                self.lanes[source_name].segmenter.discard()

            system_enabled = self._mode_allows("system", mode)
            mic_enabled = self._mode_allows("mic", mode)
            n = max(len(system_chunks), len(mic_chunks))
            for i in range(n):
                if i < len(system_chunks):
                    audio = system_chunks[i]
                    self._publish_audio_level("system", audio)
                    # REMOTE remains unmodified. MIC is no longer compared
                    # against system loopback/audio (no echo gating).
                    if system_enabled:
                        for event in self.lanes["system"].segmenter.push(audio):
                            self._submit("system", event)
                if i < len(mic_chunks):
                    raw_mic = mic_chunks[i]
                    if not mic_enabled:
                        continue
                    audio = self._clean_mic_chunk(raw_mic)
                    self._publish_audio_level("mic", raw_mic, cleaned=audio)
                    # Do not apply microphone echo isolation in BOTH mode: it
                    # can remove quiet speech or prematurely end the utterance.
                    # Keep the existing gentle noise reducer and Silero VAD.
                    for event in self.lanes["mic"].segmenter.push(audio):
                        self._submit("mic", event)

            now = time.monotonic()
            mic_backlog = self.lanes["mic"].source.pending_chunks()
            sys_backlog = self.lanes["system"].source.pending_chunks()
            if max(mic_backlog, sys_backlog) >= 24 and now - self._last_backlog_warn >= 5.0:
                print(f"\n[AUDIO WARN] backlog mic={mic_backlog} system={sys_backlog} chunks")
                self._last_backlog_warn = now

            if now - self._last_health >= max(5.0, float(self.config.reliability.health_interval_s)):
                # Health persistence is useful, but it is never allowed to kill realtime
                # capture.  In particular, disk/journal errors used to escape this audio
                # thread at the ~60 s health interval.
                try:
                    self.store.record_health({
                        "uptime_s": max(0.0, now - self._session_start) if self._session_start else 0.0,
                        "timeline_end_s": self.store.timeline_end_s,
                        "transcript_entries": len(self.store.entries),
                        "translation_entries": len(self.store.translations),
                        "speaker_pending": self.speaker_worker.pending() if self.speaker_worker is not None else 0,
                        "speaker_early_switches": self.speaker_worker.probe_switches if self.speaker_worker is not None else 0,
                        "speaker_skipped_busy": self.speaker_worker.skipped_busy if self.speaker_worker is not None else 0,
                        "speaker_overlaps": self.speaker_worker.overlap_segments if self.speaker_worker is not None else 0,
                        "translation_pending": (
                            self.translation_worker.pending_jobs()
                            if self.translation_worker is not None else 0
                        ),
                        "dropped_asr_partials": sum(l.worker.dropped_partials for l in self.lanes.values()),
                        "mic_capture_backlog_chunks": mic_backlog,
                        "system_capture_backlog_chunks": sys_backlog,
                        "mic_capture_dropped_chunks": self.lanes["mic"].source.dropped_chunks,
                        "system_capture_dropped_chunks": self.lanes["system"].source.dropped_chunks,
                        "mic_guard_noise_chunks": self.mic_guard.stats.suppressed_low_energy_chunks,
                        "mic_guard_echo_chunks": self.mic_guard.stats.suppressed_echo_chunks,
                    })
                except Exception as exc:
                    print(f"\n[HEALTH WARN] could not persist health snapshot: {exc}")
                    traceback.print_exc()
                finally:
                    self._last_health = now
            if not had_audio:
                time.sleep(0.003)

    def _start(self) -> None:
        if self.speaker_worker is not None:
            self.speaker_worker.start()
        for lane in self.lanes.values():
            lane.worker.start()
        if self.translation_worker is not None:
            self.translation_worker.start()
        self._session_start = time.monotonic()
        self._capture_started = True
        self._sync_capture_sources()
        if self._capture_error is not None:
            raise self._capture_error

    def _shutdown(self) -> None:
        self._stop.set()
        with self._capture_switch_lock:
            self._capture_started = False
            for name in tuple(self._active_capture_sources):
                self.lanes[name].source.stop()
                self._active_capture_sources.discard(name)
        mode = self._get_audio_mode()
        for name, lane in self.lanes.items():
            if not self._mode_allows(name, mode):
                lane.segmenter.discard()
                continue
            final = lane.segmenter.flush()
            if final is not None:
                self._submit(name, final)
        # Speaker worker can still enqueue final ASR, so drain it first.
        if self.speaker_worker is not None:
            self.speaker_worker.stop(drain=True)
        # Drain ASR first: its final callbacks can enqueue translation jobs.
        for lane in self.lanes.values():
            lane.worker.stop(drain=True)
        if self.translation_worker is not None:
            self.translation_worker.stop(drain=True)

    def _print_header(self) -> None:
        c = self.config
        print("SoundText - Realtime VI/JA/EN STT + Vietnamese translation")
        print("Languages    : VI / JA / EN / AUTO(JA+EN) independently on YOU and REMOTE")
        print("Target MT    : VI (fixed); JA/EN finals only")
        print(f"YOU ASR      : {self.lanes['mic'].language.upper()} / {self.lanes['mic'].backend}")
        print(f"REMOTE ASR   : {self.lanes['system'].language.upper()} / {self.lanes['system'].backend}")
        print(f"CPU budget   : {c.asr.cpu_threads} ASR threads + {c.translation.cpu_threads} MT threads")
        print(f"Session      : {self.store.session_dir}")
        print(f"Preset       : {c.reliability.preset}")
        print(f"Audio mode   : {self._get_audio_mode().upper()} (switchable in UI)")
        print(f"Stereo mode  : mic={c.audio.mic_channel_mode} / system={c.audio.system_channel_mode}")
        print(f"Translation  : {'READY' if self.translation_available else 'UNAVAILABLE'} / {self.translation_status}")
        print(f"Speakers     : {'ONNX enabled' if self.speaker_worker is not None else 'disabled/unavailable'} (REMOTE only)")
        print(f"MT backend   : {c.translation.backend} ({c.translation.nllb_model_path})")
        print("Output       : Noi_dung/Noi_dung_cuoc_hop.txt + technical session artifacts")
        print("Runtime      : local CPU; model download is only needed during preparation")
        print("Press Ctrl+C or close the window to stop.\n")

    def request_stop(self) -> None:
        """Ask an active capture session to finish; safe to call from the UI thread."""
        self._stop.set()

    def run_capture(self) -> None:
        """Capture-only lifecycle for the Qt UI. No Tk window or auto recording.

        Every Start builds a fresh pipeline/session in a background thread.  Core
        ASR, translation, VAD and diarization workers remain exactly the same.
        """
        status = "completed"
        started = False
        try:
            if self._stop.is_set():
                status = "cancelled"
                return
            self._print_header()
            started = True
            self._start()
            if not self._stop.is_set():
                self._audio_loop()
            if self._capture_error is not None:
                raise self._capture_error
        except Exception:
            status = "failed"
            raise
        finally:
            self._stop.set()
            try:
                if started:
                    self._shutdown()
            finally:
                self.store.close(status=status)

    def run(self) -> None:
        self._print_header()
        audio_thread: threading.Thread | None = None
        status = "completed"
        try:
            self._start()
            audio_thread = threading.Thread(target=self._audio_loop, name="meeting-audio", daemon=True)
            audio_thread.start()
            if self.ui_bus is not None:
                MeetingWindow(
                    self.ui_bus,
                    title=self.config.meeting.ui_title,
                    refresh_ms=self.config.meeting.ui_refresh_ms,
                    history_items=self.config.meeting.ui_history_items,
                    initial_audio_mode=self._get_audio_mode(),
                    on_audio_mode_change=self.set_audio_mode,
                    initial_you_language=self.lanes["mic"].language,
                    initial_remote_language=self.lanes["system"].language,
                    on_you_language_change=lambda lang: threading.Thread(
                        target=self.set_lane_language, args=("mic", lang), name="switch-you-language", daemon=True
                    ).start(),
                    on_remote_language_change=lambda lang: threading.Thread(
                        target=self.set_lane_language, args=("system", lang), name="switch-remote-language", daemon=True
                    ).start(),
                    translation_available=self.translation_available,
                    translation_status=self.translation_status,
                    session_dir=str(self.store.session_dir),
                ).run()
                self._stop.set()
            else:
                while audio_thread.is_alive():
                    audio_thread.join(timeout=0.25)
        except KeyboardInterrupt:
            print("\nStopping...")
        except Exception as exc:
            status = "failed"
            print(f"\n[ERROR] STT startup/runtime failed: {exc}")
            print("Tip: select option 5 in SoundText.bat to inspect microphone and LOOP=yes system devices.")
        finally:
            self._stop.set()
            if audio_thread is not None:
                audio_thread.join(timeout=1.0)
            self._shutdown()
            print(f"Dropped live previews: {sum(l.worker.dropped_partials for l in self.lanes.values())}")
            self.store.close(status=status)
            if status == "failed" and not self.store.resumed and not self.store.entries and self.store.timeline_end_s <= 0:
                failed_dir = self.store.session_dir
                shutil.rmtree(failed_dir, ignore_errors=True)
                print(f"Removed empty failed session: {failed_dir}")
            else:
                print(f"Mic noise chunks suppressed: {self.mic_guard.stats.suppressed_low_energy_chunks}")
                print(f"Mic echo chunks suppressed: {self.mic_guard.stats.suppressed_echo_chunks}")
                print(f"Mic capture dropped chunks: {self.lanes['mic'].source.dropped_chunks}")
                print(f"System capture dropped chunks: {self.lanes['system'].source.dropped_chunks}")
                print(f"Saved: {self.store.session_dir}")
