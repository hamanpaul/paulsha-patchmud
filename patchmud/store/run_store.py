"""Run store：run 目錄、append-only event log、兩級封存（spec §3、§12.1）。

- ``events.jsonl`` append-only；``seq`` 由 store 配發、讀取驗證連續性。
- ``run.yaml`` / ``result.yaml`` 一次寫入即不可變。
- ``archive_private``：run 目錄 + content-addressed evaluator bundle
  （hidden bytes、reference timings、card rubric、執行環境描述）。
- ``archive_public``：同上，但 hidden 資產 bytes 以 content hash 佔位。
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import tarfile
from importlib import metadata
from pathlib import Path

import yaml

from patchmud.store.schemas import (
    BUNDLE_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    StoreError,
    validate_new_event,
    validate_new_result,
    validate_run_config,
    validate_run_record,
    validate_stored_event,
)

__all__ = ["RunStore"]

_EVENTS_FILE = "events.jsonl"
_RUN_FILE = "run.yaml"
_RESULT_FILE = "result.yaml"
_BUNDLE_DIR = "evaluator_bundle"
_HIDDEN_DIR = "hidden"
_CARD_FILE = "card.yaml"

# 環境描述取樣的套件（lockfile 描述；缺席者略過）
_ENV_PACKAGES = ("pytest", "ruff", "PyYAML")

_PLACEHOLDER_TEMPLATE = (
    "PatchMUD archive-public placeholder\n"
    "content withheld (hidden asset)\n"
    "sha256: {digest}\n"
)


class RunStore:
    """單一 run 的落盤介面；一切讀取 fail-closed。"""

    def __init__(self, run_dir: Path, next_seq: int) -> None:
        self.run_dir = Path(run_dir)
        self._next_seq = next_seq

    # -- 建立與重開 --------------------------------------------------------

    @classmethod
    def create(cls, run_config: dict, runs_root: Path) -> "RunStore":
        validate_run_config(run_config)
        encounter_dir = Path(run_config["encounter_dir"])
        if not encounter_dir.is_dir():
            raise StoreError(f"encounter_dir 不存在：{encounter_dir}")

        run_dir = Path(runs_root) / run_config["run_id"]
        if run_dir.exists():
            raise StoreError(f"run 目錄已存在，拒絕覆寫：{run_dir}")
        run_dir.mkdir(parents=True)

        record = {"schema_version": RUN_SCHEMA_VERSION, **run_config}
        (run_dir / _RUN_FILE).write_text(
            yaml.safe_dump(record, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        return cls(run_dir, next_seq=1)

    @classmethod
    def open(cls, run_dir: Path) -> "RunStore":
        run_dir = Path(run_dir)
        run_yaml = run_dir / _RUN_FILE
        if not run_yaml.is_file():
            raise StoreError(f"run.yaml 不存在：{run_dir}")
        validate_run_record(yaml.safe_load(run_yaml.read_text(encoding="utf-8")))

        store = cls(run_dir, next_seq=1)
        # 重開即全量驗證既有 event log；續寫 seq 由此決定
        store._next_seq = len(store.load_events()) + 1
        return store

    # -- event log ---------------------------------------------------------

    def append_event(self, event: dict) -> None:
        validate_new_event(event)
        record = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "seq": self._next_seq,
            **event,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        except TypeError as exc:
            raise StoreError(f"event 無法 JSON 序列化：{exc}") from exc
        with (self.run_dir / _EVENTS_FILE).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._next_seq += 1

    def load_events(self) -> list[dict]:
        log = self.run_dir / _EVENTS_FILE
        if not log.is_file():
            return []
        events: list[dict] = []
        for lineno, line in enumerate(
            log.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StoreError(
                    f"events.jsonl 第 {lineno} 行非合法 JSON：{exc}"
                ) from exc
            validate_stored_event(event, expected_seq=len(events) + 1)
            events.append(event)
        return events

    # -- result ------------------------------------------------------------

    def write_result(self, result: dict) -> None:
        validate_new_result(result)
        dest = self.run_dir / _RESULT_FILE
        if dest.exists():
            raise StoreError(f"result.yaml 已存在，不可覆寫：{dest}")
        record = {"schema_version": RESULT_SCHEMA_VERSION, **result}
        dest.write_text(
            yaml.safe_dump(record, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )

    # -- 封存 ---------------------------------------------------------------

    def archive_private(self, dest_tar: Path) -> None:
        """私有封存：run 目錄 + evaluator bundle（hidden 原始 bytes）。"""
        self._archive(Path(dest_tar), redact_hidden=False)

    def archive_public(self, dest_tar: Path) -> None:
        """公開發布：同私有，但 hidden 資產以 content hash 佔位（§12.1）。"""
        self._archive(Path(dest_tar), redact_hidden=True)

    def _archive(self, dest_tar: Path, redact_hidden: bool) -> None:
        run_id = self.run_dir.name
        entries: list[tuple[str, bytes]] = []

        # run 目錄完整入包（read-only：封存絕不寫回 run 目錄）
        for path in sorted(self.run_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(self.run_dir).as_posix()
                entries.append((f"{run_id}/{rel}", path.read_bytes()))

        bundle = self._build_bundle(redact_hidden=redact_hidden)
        entries.extend(
            (f"{run_id}/{_BUNDLE_DIR}/{rel}", data) for rel, data in bundle
        )

        with tarfile.open(dest_tar, "w") as tar:
            for name, data in entries:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mtime = 0  # 決定性封存：固定 metadata
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))

    def _build_bundle(self, redact_hidden: bool) -> list[tuple[str, bytes]]:
        """組 evaluator bundle：回傳 (bundle 內相對路徑, bytes) 排序清單。"""
        encounter_dir = self._encounter_dir()
        hidden_dir = encounter_dir / _HIDDEN_DIR
        if not hidden_dir.is_dir():
            raise StoreError(f"encounter 缺 hidden/ 目錄：{encounter_dir}")
        card = encounter_dir / _CARD_FILE
        if not card.is_file():
            raise StoreError(f"encounter 缺 card.yaml：{encounter_dir}")

        # 原始 bytes（manifest 一律記真實內容的 sha256）
        raw: list[tuple[str, bytes]] = [(_CARD_FILE, card.read_bytes())]
        for path in sorted(hidden_dir.rglob("*")):
            if path.is_file():
                rel = f"{_HIDDEN_DIR}/{path.relative_to(hidden_dir).as_posix()}"
                raw.append((rel, path.read_bytes()))

        env_bytes = self._environment_yaml()
        raw.append(("environment.yaml", env_bytes))
        raw.sort(key=lambda item: item[0])

        manifest_files = [
            {"path": rel, "sha256": hashlib.sha256(data).hexdigest()}
            for rel, data in raw
        ]
        bundle_digest = hashlib.sha256(
            json.dumps(manifest_files, sort_keys=True).encode("utf-8")
        ).hexdigest()
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_digest": bundle_digest,
            "files": manifest_files,
        }
        manifest_bytes = yaml.safe_dump(
            manifest, sort_keys=True, allow_unicode=True
        ).encode("utf-8")

        out: list[tuple[str, bytes]] = []
        for rel, data in raw:
            if redact_hidden and rel.startswith(f"{_HIDDEN_DIR}/"):
                digest = hashlib.sha256(data).hexdigest()
                data = _PLACEHOLDER_TEMPLATE.format(digest=digest).encode("utf-8")
            out.append((rel, data))
        out.append(("manifest.yaml", manifest_bytes))
        out.sort(key=lambda item: item[0])
        return out

    def _encounter_dir(self) -> Path:
        record = yaml.safe_load(
            (self.run_dir / _RUN_FILE).read_text(encoding="utf-8")
        )
        validate_run_record(record)
        encounter_dir = Path(record["encounter_dir"])
        if not encounter_dir.is_dir():
            raise StoreError(f"encounter_dir 不存在：{encounter_dir}")
        return encounter_dir

    @staticmethod
    def _environment_yaml() -> bytes:
        """執行環境 / lockfile 描述（spec §12.1 evaluator bundle 成分）。"""
        packages: dict[str, str] = {}
        for name in _ENV_PACKAGES:
            try:
                packages[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                continue
        description = {
            "python_version": platform.python_version(),
            "packages": packages,
        }
        digest = hashlib.sha256(
            json.dumps(description, sort_keys=True).encode("utf-8")
        ).hexdigest()
        record = {"schema_version": BUNDLE_SCHEMA_VERSION, **description}
        record["environment_digest"] = digest
        return yaml.safe_dump(record, sort_keys=True, allow_unicode=True).encode(
            "utf-8"
        )
