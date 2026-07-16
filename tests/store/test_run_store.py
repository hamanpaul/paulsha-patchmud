"""Task 6 RED：Run store、event log、封存（spec §3、§12.1）。

鎖定：
- event append-only（重開 store 續寫 seq、既有 bytes 不動）；
- unknown ``schema_version`` 讀取 fail-closed（events 與 run.yaml 皆然）；
- ``archive_public`` 內 grep 不到任何 hidden probe bytes、但含其 sha256；
- ``archive_private`` 含 evaluator bundle（hidden bytes、reference timings、
  lockfile / 執行環境描述）。
"""

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from patchmud.store.run_store import RunStore
from patchmud.store.schemas import (
    EVENT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    StoreError,
)

# 專屬 marker：出現在 hidden 資產內；公開包 grep 不得命中
HIDDEN_MARKER = "PATCHMUD_HIDDEN_MARKER_e5f1c2"

HIDDEN_PROBE_SRC = (
    f"# {HIDDEN_MARKER}\n"
    "def test_secret_invariant():\n"
    "    assert True\n"
)
REFERENCE_PATCH_SRC = (
    "--- a/src/x.py\n"
    "+++ b/src/x.py\n"
    f"# {HIDDEN_MARKER}\n"
)
REFERENCE_TIMINGS_SRC = "hidden/test_secret.py: 10\n"


@pytest.fixture
def encounter(tmp_path):
    """合成 encounter：store 只把 encounter 檔案當 bytes，毋需完整 deck schema。"""
    enc = tmp_path / "enc"
    (enc / "hidden").mkdir(parents=True)
    (enc / "repo" / "src").mkdir(parents=True)
    (enc / "card.yaml").write_text(
        "schema_version: 1\nissue_id: store-fixture\n", encoding="utf-8"
    )
    (enc / "hidden" / "test_secret.py").write_text(HIDDEN_PROBE_SRC, encoding="utf-8")
    (enc / "hidden" / "reference.patch").write_text(
        REFERENCE_PATCH_SRC, encoding="utf-8"
    )
    (enc / "hidden" / "reference_timings.yaml").write_text(
        REFERENCE_TIMINGS_SRC, encoding="utf-8"
    )
    (enc / "repo" / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    return enc


def make_config(encounter, **overrides):
    config = {
        "run_id": "run-0001",
        "frozen_sha": "f" * 40,
        "pricing_hash": "sha256:" + "a" * 64,
        "harness_prompt_version": "hp-v1",
        "schedule_ref": "schedule.yaml#0",
        "encounter_dir": str(encounter),
    }
    config.update(overrides)
    return config


@pytest.fixture
def store(tmp_path, encounter):
    return RunStore.create(make_config(encounter), runs_root=tmp_path / "runs")


# ---------------------------------------------------------------------------
# create / run.yaml
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_run_dir_and_run_yaml(self, tmp_path, encounter):
        store = RunStore.create(make_config(encounter), runs_root=tmp_path / "runs")
        run_dir = tmp_path / "runs" / "run-0001"
        assert store.run_dir == run_dir
        data = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
        assert data["schema_version"] == RUN_SCHEMA_VERSION
        assert data["run_id"] == "run-0001"
        assert data["frozen_sha"] == "f" * 40
        assert data["pricing_hash"] == "sha256:" + "a" * 64
        assert data["harness_prompt_version"] == "hp-v1"
        assert data["schedule_ref"] == "schedule.yaml#0"
        assert data["encounter_dir"] == str(encounter)

    @pytest.mark.parametrize(
        "missing",
        [
            "run_id",
            "frozen_sha",
            "pricing_hash",
            "harness_prompt_version",
            "schedule_ref",
            "encounter_dir",
        ],
    )
    def test_missing_required_key_fail_closed(self, tmp_path, encounter, missing):
        config = make_config(encounter)
        del config[missing]
        with pytest.raises(StoreError):
            RunStore.create(config, runs_root=tmp_path / "runs")

    def test_existing_run_dir_rejected(self, tmp_path, encounter):
        RunStore.create(make_config(encounter), runs_root=tmp_path / "runs")
        with pytest.raises(StoreError):
            RunStore.create(make_config(encounter), runs_root=tmp_path / "runs")

    def test_missing_encounter_dir_fail_closed(self, tmp_path, encounter):
        config = make_config(encounter, encounter_dir=str(tmp_path / "nowhere"))
        with pytest.raises(StoreError):
            RunStore.create(config, runs_root=tmp_path / "runs")

    def test_create_rejects_foreign_schema_version(self, tmp_path, encounter):
        """caller 自帶外來 schema_version 不得靜默覆寫 store 版本戳（fail-early）。

        否則 run.yaml 寫入當下零錯誤，事後 open / archive 全部 StoreError，
        整場 run 永久不可讀不可封存（fail-late 資料喪失）。
        """
        config = make_config(encounter, schema_version=999)
        with pytest.raises(StoreError):
            RunStore.create(config, runs_root=tmp_path / "runs")
        # fail-early：不得留下半成品 run 目錄
        assert not (tmp_path / "runs" / "run-0001").exists()

    def test_create_accepts_matching_schema_version(self, tmp_path, encounter):
        """與 store 版本一致的 schema_version 無害，比照 validate_new_event。"""
        config = make_config(encounter, schema_version=RUN_SCHEMA_VERSION)
        store = RunStore.create(config, runs_root=tmp_path / "runs")
        data = yaml.safe_load((store.run_dir / "run.yaml").read_text(encoding="utf-8"))
        assert data["schema_version"] == RUN_SCHEMA_VERSION
        RunStore.open(store.run_dir)  # 寫出的 run.yaml 必須可重開


# ---------------------------------------------------------------------------
# event log：append-only、自動 seq、fail-closed 讀取
# ---------------------------------------------------------------------------


class TestEventLog:
    def test_append_assigns_monotonic_seq_and_schema_version(self, store):
        store.append_event({"type": "turn", "turn": 1})
        store.append_event({"type": "turn", "turn": 2})
        store.append_event({"type": "final"})
        events = store.load_events()
        assert [e["seq"] for e in events] == [1, 2, 3]
        assert [e["type"] for e in events] == ["turn", "turn", "final"]
        assert all(e["schema_version"] == EVENT_SCHEMA_VERSION for e in events)
        assert events[0]["turn"] == 1

    def test_reopen_continues_seq_without_touching_prior_bytes(self, store):
        store.append_event({"type": "turn", "turn": 1})
        store.append_event({"type": "turn", "turn": 2})
        log = store.run_dir / "events.jsonl"
        before = log.read_bytes()

        reopened = RunStore.open(store.run_dir)
        reopened.append_event({"type": "final"})

        after = log.read_bytes()
        assert after.startswith(before)  # append-only：舊 bytes 一字不動
        events = reopened.load_events()
        assert [e["seq"] for e in events] == [1, 2, 3]

    def test_append_rejects_caller_supplied_seq(self, store):
        with pytest.raises(StoreError):
            store.append_event({"type": "turn", "seq": 7})

    def test_append_rejects_missing_type(self, store):
        with pytest.raises(StoreError):
            store.append_event({"turn": 1})

    def test_append_rejects_foreign_schema_version(self, store):
        with pytest.raises(StoreError):
            store.append_event({"type": "turn", "schema_version": 999})

    def test_append_rejects_non_serializable_event(self, store):
        with pytest.raises(StoreError):
            store.append_event({"type": "turn", "payload": object()})

    def test_load_events_empty_log(self, store):
        assert store.load_events() == []

    def test_load_events_unknown_schema_version_fail_closed(self, store):
        store.append_event({"type": "turn", "turn": 1})
        log = store.run_dir / "events.jsonl"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema_version": 999, "seq": 2, "type": "x"}) + "\n")
        with pytest.raises(StoreError):
            store.load_events()
        # 重開 store 也必須 fail-closed，不能靜默續寫
        with pytest.raises(StoreError):
            RunStore.open(store.run_dir)

    def test_load_events_seq_gap_fail_closed(self, store):
        store.append_event({"type": "turn", "turn": 1})
        store.append_event({"type": "turn", "turn": 2})
        store.append_event({"type": "final"})
        log = store.run_dir / "events.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        log.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
        with pytest.raises(StoreError):
            store.load_events()

    def test_load_events_malformed_json_fail_closed(self, store):
        log = store.run_dir / "events.jsonl"
        log.write_text("not-json\n", encoding="utf-8")
        with pytest.raises(StoreError):
            store.load_events()


# ---------------------------------------------------------------------------
# open：run.yaml fail-closed
# ---------------------------------------------------------------------------


class TestOpen:
    def test_open_missing_run_yaml(self, tmp_path):
        (tmp_path / "empty-run").mkdir()
        with pytest.raises(StoreError):
            RunStore.open(tmp_path / "empty-run")

    def test_open_unknown_run_schema_version_fail_closed(self, store):
        run_yaml = store.run_dir / "run.yaml"
        data = yaml.safe_load(run_yaml.read_text(encoding="utf-8"))
        data["schema_version"] = 999
        run_yaml.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(StoreError):
            RunStore.open(store.run_dir)


# ---------------------------------------------------------------------------
# result.yaml：一次寫入、不可覆寫
# ---------------------------------------------------------------------------


class TestWriteResult:
    def test_write_result_round_trip(self, store):
        store.write_result({"power_total": 72, "clear": True})
        data = yaml.safe_load(
            (store.run_dir / "result.yaml").read_text(encoding="utf-8")
        )
        assert data["power_total"] == 72
        assert data["clear"] is True
        assert isinstance(data["schema_version"], int)

    def test_write_result_immutable(self, store):
        store.write_result({"power_total": 72})
        with pytest.raises(StoreError):
            store.write_result({"power_total": 99})

    def test_write_result_rejects_foreign_schema_version(self, store):
        """caller 自帶外來 schema_version 必須拒收，不得毒化一次性 result.yaml。

        result.yaml 寫入即不可覆寫；若外來版本落盤，replay fail-closed 必拒
        且無法修復，並會被 archive 靜默打包。
        """
        with pytest.raises(StoreError):
            store.write_result({"schema_version": 999, "power_total": 72})
        # 拒收不得消耗一次性寫入額度：合法 result 仍可落盤
        assert not (store.run_dir / "result.yaml").exists()
        store.write_result({"power_total": 72})
        data = yaml.safe_load(
            (store.run_dir / "result.yaml").read_text(encoding="utf-8")
        )
        assert data["schema_version"] == RESULT_SCHEMA_VERSION
        assert data["power_total"] == 72

    def test_write_result_accepts_matching_schema_version(self, store):
        """與 store 版本一致的 schema_version 無害，比照 validate_new_event。"""
        store.write_result({"schema_version": RESULT_SCHEMA_VERSION, "power_total": 5})
        data = yaml.safe_load(
            (store.run_dir / "result.yaml").read_text(encoding="utf-8")
        )
        assert data["schema_version"] == RESULT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 封存：archive_private / archive_public（spec §12.1）
# ---------------------------------------------------------------------------


def _tar_members(tar_path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            if member.isfile():
                fh = tar.extractfile(member)
                assert fh is not None
                out[member.name] = fh.read()
    return out


def _populated(store: RunStore) -> RunStore:
    store.append_event({"type": "turn", "turn": 1})
    store.write_result({"power_total": 72})
    return store


class TestArchivePrivate:
    def test_contains_run_dir_and_evaluator_bundle(self, store, tmp_path):
        _populated(store)
        dest = tmp_path / "private.tar"
        store.archive_private(dest)
        members = _tar_members(dest)

        # run 目錄完整入包
        assert "run-0001/run.yaml" in members
        assert "run-0001/events.jsonl" in members
        assert "run-0001/result.yaml" in members

        # evaluator bundle：hidden 原始 bytes、reference timings、card（rubric）
        bundle = "run-0001/evaluator_bundle"
        assert members[f"{bundle}/hidden/test_secret.py"] == HIDDEN_PROBE_SRC.encode()
        assert (
            members[f"{bundle}/hidden/reference.patch"]
            == REFERENCE_PATCH_SRC.encode()
        )
        assert (
            members[f"{bundle}/hidden/reference_timings.yaml"]
            == REFERENCE_TIMINGS_SRC.encode()
        )
        assert f"{bundle}/card.yaml" in members

    def test_bundle_environment_and_manifest(self, store, tmp_path):
        _populated(store)
        dest = tmp_path / "private.tar"
        store.archive_private(dest)
        members = _tar_members(dest)
        bundle = "run-0001/evaluator_bundle"

        env = yaml.safe_load(members[f"{bundle}/environment.yaml"])
        major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert env["python_version"].startswith(major_minor)
        assert "pytest" in env["packages"]  # lockfile / 環境描述
        assert env["environment_digest"]

        manifest = yaml.safe_load(members[f"{bundle}/manifest.yaml"])
        by_path = {f["path"]: f["sha256"] for f in manifest["files"]}
        expected = hashlib.sha256(HIDDEN_PROBE_SRC.encode()).hexdigest()
        assert by_path["hidden/test_secret.py"] == expected
        assert manifest["bundle_digest"]


class TestArchivePublic:
    def test_no_hidden_bytes_but_content_hashes_present(self, store, tmp_path):
        _populated(store)
        dest = tmp_path / "public.tar"
        store.archive_public(dest)
        members = _tar_members(dest)

        blob = b"\n".join(members.values())
        assert HIDDEN_MARKER.encode() not in blob  # grep 不到任何 hidden bytes

        for src in (HIDDEN_PROBE_SRC, REFERENCE_PATCH_SRC, REFERENCE_TIMINGS_SRC):
            digest = hashlib.sha256(src.encode()).hexdigest()
            assert digest.encode() in blob  # 但含其 sha256

    def test_hidden_paths_become_placeholders(self, store, tmp_path):
        _populated(store)
        dest = tmp_path / "public.tar"
        store.archive_public(dest)
        members = _tar_members(dest)
        bundle = "run-0001/evaluator_bundle"

        placeholder = members[f"{bundle}/hidden/test_secret.py"]
        expected = hashlib.sha256(HIDDEN_PROBE_SRC.encode()).hexdigest()
        assert expected.encode() in placeholder
        assert HIDDEN_MARKER.encode() not in placeholder

        # run 目錄與非 hidden bundle 檔照常入包
        assert "run-0001/events.jsonl" in members
        assert f"{bundle}/card.yaml" in members

    def test_archives_do_not_mutate_run_dir(self, store, tmp_path):
        _populated(store)
        snapshot = {
            p.relative_to(store.run_dir): p.read_bytes()
            for p in sorted(store.run_dir.rglob("*"))
            if p.is_file()
        }
        store.archive_private(tmp_path / "private.tar")
        store.archive_public(tmp_path / "public.tar")
        after = {
            p.relative_to(store.run_dir): p.read_bytes()
            for p in sorted(store.run_dir.rglob("*"))
            if p.is_file()
        }
        assert after == snapshot


class TestArchiveDeterminism:
    def test_private_archive_bit_identical_across_calls(self, store, tmp_path):
        _populated(store)
        a, b = tmp_path / "a.tar", tmp_path / "b.tar"
        store.archive_private(a)
        store.archive_private(b)
        assert a.read_bytes() == b.read_bytes()
