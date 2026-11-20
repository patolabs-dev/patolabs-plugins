import json
import subprocess
import uuid
from types import SimpleNamespace

import pytest

import foundry
from foundry import doctor, registry
from foundry.models import Project
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker


def _repo(tmp_path, owner, name, remote=None):
    root = tmp_path / owner / name
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git", "-C", str(root), "remote", "add", "origin",
            remote or f"https://github.com/{owner}/{name}.git",
        ],
        check=True,
    )
    return root


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setenv("FOUNDRY_TRACKER", "youtrack")
    monkeypatch.delenv("PROJECT_REPO", raising=False)
    return tmp_path


def test_new_checkout_never_becomes_legacy_from_global_provider(isolated):
    repo = _repo(isolated, "first", "same")

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(repo))


def test_normal_factory_stays_closed_for_new_unbound_repository(isolated, monkeypatch):
    repo = _repo(isolated, "first", "same")
    monkeypatch.chdir(repo)

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        foundry.tracker()


def test_legacy_binding_is_observable_and_explicit_v1_refuses(isolated):
    repo = _repo(isolated, "first", "same")
    registry.register("youtrack", "same", "ONE", "0-1")

    selected = registry.repository_tracker_selection(str(repo))
    assert selected["mode"] == "legacy"
    assert selected["tracker"] == "youtrack"
    with pytest.raises(SystemExit, match="V1 requis"):
        registry.repository_tracker_selection(str(repo), require_v1=True)


@pytest.mark.parametrize("state", ["interrupted", "ambiguous", "tombstone"])
def test_factory_and_doctor_refuse_existing_state_before_global_devhub(
    isolated, monkeypatch, state,
):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    registry.register(
        "youtrack", "same", "ONE", "0-1",
        canonical_repo="github.com/acme/same",
    )
    if state == "ambiguous":
        registry.register(
            "ghprojects", "same", "TWO", "PVT_2",
            canonical_repo="github.com/acme/same", owner="acme", number="2",
        )
    elif state == "tombstone":
        data = registry.load()
        data["youtrack"]["same"]["archive"] = True
        registry._save(data)
    monkeypatch.setattr(
        foundry.config, "tracker_name",
        lambda: pytest.fail("existing local state must be checked before global config"),
    )
    monkeypatch.setattr(
        foundry.config, "require_public",
        lambda _key: pytest.fail("no provider construction or preflight allowed"),
    )
    for call in (foundry.tracker, doctor._provider_transport_preflight):
        with pytest.raises(SystemExit, match="Binding tracker"):
            call()


def test_resolve_refuses_ambiguous_canonical_bindings_without_effects(isolated):
    repo = _repo(isolated, "acme", "same")
    registry.register(
        "youtrack", "same", "ONE", "0-1",
        canonical_repo="github.com/acme/same",
    )
    registry.register(
        "ghprojects", "same", "TWO", "PVT_2",
        canonical_repo="github.com/acme/same", owner="acme", number="2",
    )
    registry_path = isolated / "state" / "registry.json"
    before = registry_path.read_bytes()

    with pytest.raises(SystemExit, match="bindings actifs ambigus"):
        registry.resolve("youtrack", "same", cwd=str(repo))

    assert registry_path.read_bytes() == before
    assert not (repo / ".foundry/tracker.json").exists()


def test_legacy_selection_beats_global_devhub_in_factory_and_doctor(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    registry.register("ghprojects", "same", "ONE", "PVT_1")
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    assert foundry.tracker().name == "ghprojects"
    assert doctor._provider_transport_preflight() == ("ghprojects", None)


def test_devhub_canonical_entry_without_marker_fails_closed_until_bootstrap_replay(
    isolated, monkeypatch,
):
    """PAT-323: an existing canonical DevHub entry is V1 state, never the retired pilot."""
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    registry.register(
        "devhub", "same", "PILOT", "42", canonical_repo="github.com/acme/same",
    )
    with pytest.raises(SystemExit, match="V1 sans marqueur"):
        foundry.effective_tracker_name()
    with pytest.raises(SystemExit, match="V1 sans marqueur"):
        registry.repository_tracker_selection()
    with pytest.raises(SystemExit, match="marqueur absent"):
        registry.resolve("devhub", "same")
    before = (isolated / "state" / "registry.json").read_bytes()

    binding = registry.bootstrap_repository_binding(
        "devhub", "same", "PILOT", "42", canonical_repo="github.com/acme/same",
    )

    assert (isolated / "state" / "registry.json").read_bytes() == before
    assert binding.tracker == "devhub"
    selected = registry.repository_tracker_selection(require_v1=True)
    assert selected["mode"] == "v1"
    assert foundry.effective_tracker_name() == "devhub"
    registry.register("youtrack", "same", "OTHER", "0-1")
    with pytest.raises(SystemExit, match="ambigu"):
        foundry.effective_tracker_name()


def test_devhub_unbound_checkout_keeps_the_host_name_but_resolves_nothing(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    assert foundry.effective_tracker_name() == "devhub"
    from foundry.trackers.devhub import DevHubTracker

    tracker = object.__new__(DevHubTracker)
    with pytest.raises(SystemExit, match="Binding tracker absent"):
        tracker.resolve_checkout_project()


def test_devhub_adapter_refuses_a_checkout_active_on_another_provider(isolated, monkeypatch):
    from foundry.trackers.devhub import DevHubTracker

    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", owner="acme", number="1",
    )
    with pytest.raises(SystemExit, match="actif sur 'ghprojects'"):
        object.__new__(DevHubTracker).resolve_checkout_project()


def test_devhub_bootstrap_publishes_only_a_verified_foundry_governed_project(
    isolated, monkeypatch, capsys,
):
    from foundry.trackers.devhub import DevHubGovernanceModeError

    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    answers = iter([False, DevHubGovernanceModeError("KEY", "manual"), True])

    class Stand:
        def verify_project_identity(self, project):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(foundry, "tracker", lambda name, cwd=None: Stand())
    command = ["bootstrap", "devhub", "same", "KEY", "42"]
    with pytest.raises(SystemExit, match="étrangères ou contradictoires"):
        registry.main(command)
    with pytest.raises(SystemExit, match="mode de gouvernance 'manual'"):
        registry.main(command)
    assert registry.load() == {}
    assert not (repo / ".foundry/tracker.json").exists()

    registry.main(command)
    published = json.loads(capsys.readouterr().out)
    assert published["tracker"] == "devhub" and published["mode"] == "v1"
    assert registry.repository_tracker_selection()["mode"] == "v1"
    assert registry.load()["devhub"]["same"] == {
        "key": "KEY", "id": "42", "canonical_repo": "github.com/acme/same",
    }


def test_devhub_binding_accepts_only_declared_extras(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    with pytest.raises(ValueError, match="extra non autorisé"):
        registry.bootstrap_repository_binding(
            "devhub", "same", "KEY", "42", owner="acme",
        )
    assert registry.load() == {}
    binding = registry.bootstrap_repository_binding(
        "devhub", "same", "KEY", "42", release_ids={"1.0.0": "KEY-VERSION-1"},
    )
    assert binding.project.extra["release_ids"] == {"1.0.0": "KEY-VERSION-1"}


@pytest.mark.parametrize("value", [True, False])
def test_devhub_binding_keeps_explicit_number_preservation(isolated, monkeypatch, value):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    binding = registry.bootstrap_repository_binding(
        "devhub", "same", "KEY", "42", preserve_issue_numbers=value,
    )
    assert binding.project.extra["preserve_issue_numbers"] is value
    assert registry.repository_tracker_binding().project.extra["preserve_issue_numbers"] is value
    from test_devhub_tracker import ScriptedTracker
    tracker = ScriptedTracker([{
        "key": "KEY", "id": "42", "canonical_repo": "github.com/acme/same",
    }])
    resolved = tracker.resolve_checkout_project(str(repo))
    assert resolved.extra["preserve_issue_numbers"] is value
    assert resolved.extra["canonical_repo"] == "github.com/acme/same"
    assert tracker.calls[0][:2] == ("GET", "/projects/resolve?repo=github.com%2Facme%2Fsame")


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_devhub_binding_refuses_non_boolean_number_preservation(isolated, monkeypatch, value):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    with pytest.raises(ValueError, match="preserve_issue_numbers"):
        registry.bootstrap_repository_binding(
            "devhub", "same", "KEY", "42", preserve_issue_numbers=value,
        )
    assert registry.load() == {}


@pytest.mark.parametrize("foreign_mode", ["canonical", "legacy"])
def test_marker_does_not_hide_another_active_provider_and_update_has_no_effect(
    isolated, monkeypatch, foreign_mode,
):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    binding = registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", owner="acme", number="1",
    )
    extra = {"canonical_repo": "github.com/acme/same"} if foreign_mode == "canonical" else {}
    registry.register("youtrack", "same", "OTHER", "0-1", **extra)
    registry_path = isolated / "state" / "registry.json"
    marker_path = repo / ".foundry/tracker.json"
    before = (registry_path.read_bytes(), marker_path.read_bytes())
    with pytest.raises(ValueError, match="ambigu"):
        registry.repository_tracker_selection()
    for call in (foundry.tracker, doctor._provider_transport_preflight):
        with pytest.raises(SystemExit, match="ambigu"):
            call()
    with pytest.raises(ValueError, match="ambigu"):
        registry.update_repository_binding(
            "ghprojects", "same", "ONE", "PVT_1",
            expected_configuration_digest=binding.configuration_digest,
            owner="acme", number="2",
        )
    assert (registry_path.read_bytes(), marker_path.read_bytes()) == before


def test_archived_foreign_provider_does_not_conflict_with_active_marker(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    binding = registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", owner="acme", number="1",
    )
    registry.register(
        "youtrack", "same", "OLD", "0-1", canonical_repo="github.com/acme/same",
    )
    data = registry.load()
    data["youtrack"]["same"]["archive"] = True
    registry._save(data)
    assert registry.repository_tracker_binding() == binding


def test_devhub_marker_is_a_plain_v1_binding(isolated, monkeypatch, capsys):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    registry.register(
        "devhub", "same", "PILOT", "42", canonical_repo="github.com/acme/same",
    )
    binding = registry.cutover_repository_tracker(
        "devhub", "PILOT", "42", migration_manifest_digest="sha256:" + "a" * 64,
    )
    assert registry.repository_tracker_binding() == binding
    assert registry.repository_tracker_selection()["mode"] == "v1"
    assert registry.repository_tracker_selection(require_v1=True)["mode"] == "v1"
    registry.main(["selection", "--require-v1"])
    assert json.loads(capsys.readouterr().out)["mode"] == "v1"


def test_upgrade_binds_exact_remote_and_ignores_adverse_environment(isolated, monkeypatch):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.register("youtrack", "same", "ONE", "0-1")

    binding = registry.upgrade_legacy_binding("youtrack", "same", cwd=str(first))
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    monkeypatch.setenv("FOUNDRY_TRACKER", "linear")

    assert binding.activation_kind == "upgrade"
    assert binding.migration_manifest_digest is None
    assert registry.repository_tracker_selection(str(first))["tracker"] == "youtrack"
    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(second))


def test_bootstrap_marker_has_no_fabricated_migration_manifest(isolated):
    repo = _repo(isolated, "acme", "app", "git@github.com:Acme/App.git")

    binding = registry.bootstrap_repository_binding(
        "youtrack", "App", "APP", "0-2", cwd=str(repo), ms_bundle="bundle-2",
    )
    marker = json.loads((repo / ".foundry/tracker.json").read_text())

    assert binding.activation_kind == "bootstrap"
    assert binding.migration_manifest_digest is None
    assert marker["activation"] == {"kind": "bootstrap"}
    assert "migration_manifest_digest" not in marker
    assert registry.resolve_canonical_repository(
        "youtrack", "https://github.com/acme/app.git",
    ) == binding.project


def test_bootstrap_resolves_a_linked_git_worktree_from_its_own_root(isolated):
    source = _repo(isolated, "acme", "source")
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    worktree = isolated / "linked-worktree"
    subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "-q", "-b", "fixture", str(worktree)],
        check=True,
    )

    binding = registry.bootstrap_repository_binding(
        "youtrack", "source", "SRC", "0-3", cwd=str(worktree),
    )

    assert (worktree / ".foundry/tracker.json").is_file()
    assert not (source / ".foundry/tracker.json").exists()
    assert registry.repository_tracker_selection(str(worktree))["binding"] == binding


def test_same_basename_unbound_repository_never_inherits_foreign_v1_binding(isolated):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.bootstrap_repository_binding(
        "youtrack", "same", "ONE", "0-1", cwd=str(first),
    )

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(second))


def test_same_basename_repositories_have_independent_canonical_bindings(
    isolated, monkeypatch,
):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    first_binding = registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", cwd=str(first),
        owner="first", number="1",
    )
    second_binding = registry.bootstrap_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2", cwd=str(second),
        owner="second", number="2",
    )
    entries = registry.load()["ghprojects"]

    assert entries["same"]["canonical_repo"] == "github.com/first/same"
    disambiguated = [name for name in entries if name != "same"]
    assert disambiguated == [
        registry._disambiguated_registry_key("same", "github.com/second/same")
    ]

    monkeypatch.setenv("PROJECT_REPO", "previous-checkout")
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    for root, expected in (
        (first, first_binding.project),
        (second, second_binding.project),
    ):
        monkeypatch.chdir(root)
        adapter = foundry.tracker()
        assert adapter.name == "ghprojects"
        assert adapter.resolve_checkout_project() == expected
        assert doctor._provider_transport_preflight() == ("ghprojects", None)

    first_updated = registry.update_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1",
        expected_configuration_digest=first_binding.configuration_digest,
        cwd=str(first), owner="first", number="3",
    )
    second_updated = registry.update_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2",
        expected_configuration_digest=second_binding.configuration_digest,
        cwd=str(second), owner="second", number="4",
    )

    assert first_updated.project.extra["number"] == "3"
    assert second_updated.project.extra["number"] == "4"
    assert registry.repository_tracker_binding(str(first)) == first_updated
    assert registry.repository_tracker_binding(str(second)) == second_updated


def test_disambiguated_binding_update_replay_completes_marker_publication(
    isolated, monkeypatch,
):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", cwd=str(first),
        owner="first", number="1",
    )
    initial = registry.bootstrap_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2", cwd=str(second),
        owner="second", number="2",
    )
    real_replace = registry.os.replace
    interrupted = False

    def replace(source, destination):
        nonlocal interrupted
        if str(destination).endswith("second/same/.foundry/tracker.json") and not interrupted:
            interrupted = True
            raise OSError("marker interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", replace)
    with pytest.raises(OSError, match="marker interrupted"):
        registry.update_repository_binding(
            "ghprojects", "same", "TWO", "PVT_2",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(second), owner="second", number="9",
        )

    recovered = registry.update_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2",
        expected_configuration_digest=initial.configuration_digest,
        cwd=str(second), owner="second", number="9",
    )

    assert recovered.project.extra["number"] == "9"
    assert registry.repository_tracker_binding(str(first)).project.key == "ONE"


def test_cutover_ignores_foreign_canonical_binding_with_same_basename(isolated):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.register(
        "youtrack", "same", "ONE", "0-1",
        canonical_repo=registry.checkout_repository_identity(str(first)),
    )
    registry.register(
        "ghprojects", "second-same", "TWO", "PVT_2",
        canonical_repo=registry.checkout_repository_identity(str(second)),
        owner="second", number="2",
    )

    binding = registry.cutover_repository_tracker(
        "ghprojects", "TWO", "PVT_2",
        migration_manifest_digest="sha256:" + "a" * 64,
        cwd=str(second),
    )

    assert binding.project.key == "TWO"
    assert registry.load()["youtrack"]["same"].get("archive") is not True
    assert registry.repository_tracker_binding(str(second)) == binding


def test_provider_readback_refuses_foreign_and_unavailable_coordinates(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    outcomes = iter([False, RuntimeError("provider down"), True])
    def verify(_project):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    adapter = SimpleNamespace(verify_project_identity=verify)
    monkeypatch.setattr(foundry, "tracker", lambda *_args, **_kwargs: adapter)

    with pytest.raises(ValueError, match="étrangères"):
        registry.verify_existing_project(
            "youtrack", "app", "APP", "0-1", cwd=str(repo),
        )
    with pytest.raises(ValueError, match="indisponibles"):
        registry.verify_existing_project(
            "youtrack", "app", "APP", "0-1", cwd=str(repo),
        )
    project = registry.verify_existing_project(
        "youtrack", "app", "APP", "0-1", cwd=str(repo),
    )
    assert project.extra["canonical_repo"] == "github.com/acme/app"


def test_mapping_update_publishes_binding_and_marker_together_and_refuses_stale_writer(
    isolated,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )
    updated = registry.update_repository_binding(
        "youtrack",
        "app",
        "APP",
        "0-1",
        expected_configuration_digest=initial.configuration_digest,
        cwd=str(repo),
        ms_bundle="new",
    )

    assert updated.project.extra["ms_bundle"] == "new"
    assert registry.repository_tracker_binding(str(repo)) == updated
    with pytest.raises(ValueError, match="configuration concurrente"):
        registry.update_repository_binding(
            "youtrack",
            "app",
            "APP",
            "0-1",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(repo),
            ms_bundle="loser",
        )


def test_mapping_update_replay_completes_interrupted_marker_publication(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )
    real_replace = registry.os.replace
    interrupted = False

    def replace(source, destination):
        nonlocal interrupted
        if str(destination).endswith(".foundry/tracker.json") and not interrupted:
            interrupted = True
            raise OSError("marker interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", replace)
    with pytest.raises(OSError, match="marker interrupted"):
        registry.update_repository_binding(
            "youtrack", "app", "APP", "0-1",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(repo), ms_bundle="new",
        )
    assert registry.load()["youtrack"]["app"]["ms_bundle"] == "new"
    with pytest.raises(ValueError, match="modifié depuis le cutover"):
        registry.repository_tracker_binding(str(repo))

    recovered = registry.update_repository_binding(
        "youtrack", "app", "APP", "0-1",
        expected_configuration_digest=initial.configuration_digest,
        cwd=str(repo), ms_bundle="new",
    )
    assert recovered.project.extra["ms_bundle"] == "new"


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("version", 3),
        ("activation", {"kind": "unknown"}),
        ("registry_binding_digest", "sha256:invalid"),
        (
            "activation",
            {"kind": "migration", "manifest_digest": "sha256:invalid"},
        ),
    ],
)
def test_mapping_update_replay_refuses_invalid_marker_before_any_write(
    isolated, field, invalid,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )

    # Reproduce the only valid replay window: the registry has the complete winner,
    # while the marker still describes the old binding. The malformed marker must be
    # rejected before either file is touched.
    data = registry.load()
    data["youtrack"]["app"]["ms_bundle"] = "new"
    registry._save(data)
    marker_path = repo / ".foundry/tracker.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker[field] = invalid
    if field != "configuration_digest":
        marker["configuration_digest"] = registry._json_digest({
            name: value for name, value in marker.items()
            if name != "configuration_digest"
        })
    marker_path.write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    registry_before = (isolated / "state" / "registry.json").read_bytes()
    marker_before = marker_path.read_bytes()

    with pytest.raises(ValueError, match="marqueur tracker.*invalide"):
        registry.update_repository_binding(
            "youtrack", "app", "APP", "0-1",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(repo), ms_bundle="new",
        )

    assert (isolated / "state" / "registry.json").read_bytes() == registry_before
    assert marker_path.read_bytes() == marker_before


def _adapter_binding(adapter):
    if adapter is YouTrackTracker:
        return "youtrack", "0-1", {}
    if adapter is GitHubProjectsTracker:
        return "ghprojects", "PVT_project_node", {"owner": "acme", "number": "7"}
    return "linear", str(uuid.uuid4()), {
        "team_id": str(uuid.uuid4()),
        "state_ids": {key: str(uuid.uuid4()) for key in registry._LINEAR_STATE_KEYS},
        "type_label_ids": {key: str(uuid.uuid4()) for key in registry._LINEAR_TYPE_KEYS},
    }


@pytest.mark.parametrize("adapter", [YouTrackTracker, GitHubProjectsTracker, LinearTracker])
def test_adapters_use_the_same_checkout_binding_resolution(isolated, adapter):
    repo = _repo(isolated, "acme", "app")
    name, project_id, extra = _adapter_binding(adapter)
    expected = registry.bootstrap_repository_binding(
        name, "app", "APP", project_id, cwd=str(repo), **extra,
    ).project
    if adapter is YouTrackTracker:
        instance = object.__new__(adapter)
    elif adapter is LinearTracker:
        instance = adapter(token="synthetic-token-never-sent")
    else:
        instance = adapter()
    assert instance.resolve_checkout_project(str(repo)) == expected


def test_factory_activates_v1_write_binding_for_marker_adapter(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "app")
    expected = registry.bootstrap_repository_binding(
        "ghprojects", "app", "APP", "PVT_1", cwd=str(repo),
        owner="acme", number="7",
    ).project
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")

    adapter = foundry.tracker()

    assert adapter.name == "ghprojects"
    assert adapter.requires_mutation_binding is True
    assert adapter.resolve_checkout_project() == expected


def test_ghprojects_identity_probe_is_read_only_and_exact():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "data": {
                    "user": {"projectV2": {
                        "id": "PVT_1", "number": 7, "public": False,
                        "repositories": {"nodes": [{
                            "id": "R_repo", "nameWithOwner": "Acme/App", "isPrivate": True,
                            "owner": {"__typename": "User", "login": "acme"},
                        }], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    }},
                }
            }),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is True
    assert calls[0][0][:3] == ["gh", "api", "graphql"]
    assert "mutation" not in calls[0][0][4].lower()
    assert calls[0][1]["check"] is False


def test_ghprojects_identity_probe_paginates_repository_membership():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        first_page = len(calls) == 1
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "data": {
                    "user": {"projectV2": {
                        "id": "PVT_1", "number": 7, "public": False,
                        "repositories": {
                            "nodes": [{
                                "id": "R_other", "nameWithOwner": "Acme/Other", "isPrivate": True,
                                "owner": {"__typename": "User", "login": "acme"},
                            }] if first_page else [{
                                "id": "R_repo", "nameWithOwner": "Acme/App", "isPrivate": True,
                                "owner": {"__typename": "User", "login": "acme"},
                            }],
                            "pageInfo": {"hasNextPage": first_page, "endCursor": "cursor-1" if first_page else None},
                        },
                    }},
                }
            }),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is True
    assert len(calls) == 2
    assert calls[1][0][-2:] == ["-f", "cursor=cursor-1"]


def test_ghprojects_identity_probe_refuses_public_repository():
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"data": {"user": {"projectV2": {
                "id": "PVT_1", "number": 7, "public": False,
                "repositories": {"nodes": [{
                    "id": "R_repo", "nameWithOwner": "Acme/App", "isPrivate": False,
                    "owner": {"__typename": "User", "login": "acme"},
                }], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            }}}}),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is False


def test_ghprojects_identity_probe_rejects_graphql_partial_errors():
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "errors": [{"type": "FORBIDDEN", "message": "read:project required"}],
                "data": {
                    "organization": {"projectV2": {
                        "id": "PVT_1", "number": 7,
                        "repositories": {
                            "nodes": [{"nameWithOwner": "Acme/App"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }},
                    "user": None,
                },
            }),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is False


def test_ghprojects_transport_exception_is_unavailable_before_publication(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")

    def runner(*_args, **_kwargs):
        raise OSError("gh transport unavailable")

    adapter = GitHubProjectsTracker(runner=runner)
    monkeypatch.setattr(foundry, "tracker", lambda *_args, **_kwargs: adapter)

    with pytest.raises(ValueError, match="indisponibles"):
        registry.verify_existing_project(
            "ghprojects", "app", "APP", "PVT_1", cwd=str(repo),
            owner="acme", number="7",
        )
    assert registry.load().get("ghprojects", {}) == {}
    assert not (repo / ".foundry/tracker.json").exists()


def test_public_update_cli_replays_interrupted_marker_after_exact_preflight(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("YOUTRACK_URL", "https://youtrack.example.invalid")
    monkeypatch.setenv("YOUTRACK_TOKEN", "synthetic-token-never-sent")
    calls = []
    monkeypatch.setattr(
        YouTrackTracker, "verify_project_identity",
        lambda self, project: calls.append(project) or True,
    )
    real_replace = registry.os.replace
    interrupted = False

    def replace(source, destination):
        nonlocal interrupted
        if str(destination).endswith(".foundry/tracker.json") and not interrupted:
            interrupted = True
            raise OSError("marker interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", replace)
    command = [
        "update", "youtrack", "app", "APP", "0-1",
        initial.configuration_digest, "ms_bundle=new",
    ]
    with pytest.raises(OSError, match="marker interrupted"):
        registry.main(command)
    with pytest.raises(SystemExit, match="modifié depuis le cutover"):
        foundry.tracker()
    with pytest.raises(SystemExit, match="modifié concurremment"):
        registry.main([*command[:-1], "ms_bundle=foreign"])
    assert len(calls) == 1  # Divergent recovery refuses before provider readback.
    registry.main(command)
    assert len(calls) == 2
    assert registry.repository_tracker_binding().project.extra["ms_bundle"] == "new"


def test_public_upgrade_refuses_entry_changed_after_provider_verification(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    monkeypatch.chdir(repo)
    registry.register("youtrack", "app", "APP", "0-1", ms_bundle="old")
    checked = []

    def verify(_tracker, _repo, key, project_id, **extra):
        checked.append((key, project_id, extra))
        registry.register("youtrack", "app", "OTHER", "0-2", ms_bundle="new")
        return Project(
            key=key, id=project_id,
            extra={**extra, "canonical_repo": "github.com/acme/app"},
        )

    monkeypatch.setattr(registry, "verify_existing_project", verify)
    with pytest.raises(SystemExit, match="binding legacy modifié"):
        registry.main(["upgrade", "youtrack", "app"])
    assert len(checked) == 1
    assert registry.load()["youtrack"]["app"] == {
        "key": "OTHER", "id": "0-2", "ms_bundle": "new",
    }
    assert not (repo / ".foundry/tracker.json").exists()


def test_public_upgrade_publishes_only_the_verified_snapshot(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "app")
    monkeypatch.chdir(repo)
    registry.register("youtrack", "app", "APP", "0-1", ms_bundle="old")
    monkeypatch.setattr(
        registry, "verify_existing_project",
        lambda *_args, **_kwargs: Project(
            key="APP", id="0-1",
            extra={"ms_bundle": "old", "canonical_repo": "github.com/acme/app"},
        ),
    )
    registry.main(["upgrade", "youtrack", "app"])
    binding = registry.repository_tracker_binding()
    assert binding.project.id == "0-1"
    assert binding.project.extra["ms_bundle"] == "old"
    assert binding.activation_kind == "upgrade"


def test_bootstrap_replay_refuses_competing_legacy_provider_before_marker(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    real_publish = registry._publish_marker
    monkeypatch.setattr(
        registry, "_publish_marker",
        lambda *_args: (_ for _ in ()).throw(OSError("marker interrupted")),
    )
    with pytest.raises(OSError, match="marker interrupted"):
        registry.bootstrap_repository_binding(
            "youtrack", "app", "APP", "0-1", cwd=str(repo),
        )
    registry.register("ghprojects", "app", "OTHER", "PVT_2")
    before = registry.load()
    monkeypatch.setattr(registry, "_publish_marker", real_publish)
    with pytest.raises(ValueError, match="ambigu entre providers"):
        registry.bootstrap_repository_binding(
            "youtrack", "app", "APP", "0-1", cwd=str(repo),
        )
    assert registry.load() == before
    assert not (repo / ".foundry/tracker.json").exists()


@pytest.mark.parametrize("adapter", [YouTrackTracker, GitHubProjectsTracker, LinearTracker])
def test_existing_adapter_revalidates_marker_before_consuming_interrupted_update(
    isolated, monkeypatch, adapter,
):
    repo = _repo(isolated, "acme", "app")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("YOUTRACK_URL", "https://youtrack.example.invalid")
    monkeypatch.setenv("YOUTRACK_TOKEN", "synthetic-token-never-sent")
    monkeypatch.setenv("LINEAR_API_TOKEN", "synthetic-token-never-sent")
    name, project_id, extra = _adapter_binding(adapter)
    initial = registry.bootstrap_repository_binding(
        name, "app", "APP", project_id, **extra,
    )
    instance = foundry.tracker()
    assert instance.resolve_checkout_project() == initial.project
    monkeypatch.setattr(
        registry, "_publish_marker",
        lambda *_args: (_ for _ in ()).throw(OSError("marker interrupted")),
    )
    changed = dict(extra)
    if adapter is YouTrackTracker:
        changed["ms_bundle"] = "new-bundle"
    elif adapter is GitHubProjectsTracker:
        changed["number"] = "8"
    else:
        changed["milestone_ids"] = {"M1": str(uuid.uuid4())}
    with pytest.raises(OSError, match="marker interrupted"):
        registry.update_repository_binding(
            name, "app", "APP", project_id,
            expected_configuration_digest=initial.configuration_digest, **changed,
        )
    with pytest.raises(ValueError, match="modifié depuis le cutover"):
        instance.resolve_checkout_project()
    if adapter is LinearTracker:
        assert instance._active_project == initial.project


@pytest.mark.parametrize("key", ["token", "api_token", "password", "unknown"])
def test_youtrack_v1_refuses_undeclared_extras_before_provider_or_publication(
    isolated, monkeypatch, key,
):
    repo = _repo(isolated, "acme", "app")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        foundry, "tracker",
        lambda *_args, **_kwargs: pytest.fail("invalid extras refuse before provider"),
    )
    with pytest.raises(SystemExit, match="extra non autorisé"):
        registry.main(["bootstrap", "youtrack", "app", "APP", "0-1", f"{key}=synthetic"])
    assert registry.load() == {}
    assert not (repo / ".foundry/tracker.json").exists()


@pytest.mark.parametrize("global_provider", ["youtrack", "ghprojects"])
def test_legacy_ambiguity_reaches_both_hooks_without_global_selection(
    isolated, monkeypatch, global_provider,
):
    import os
    from pathlib import Path

    repo = _repo(isolated, "acme", "app")
    registry.register("youtrack", "app", "ONE", "0-1")
    registry.register("ghprojects", "app", "TWO", "PVT_2")
    monkeypatch.setenv("FOUNDRY_TRACKER", global_provider)
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    with pytest.raises(ValueError, match="ambigu"):
        registry.entry_for(str(repo), use_env=False)
    hooks = Path(__file__).resolve().parents[1] / "hooks"

    def run(name, payload):
        result = subprocess.run(
            ["python3", str(hooks / name)], input=json.dumps(payload),
            capture_output=True, text=True, check=True, env=dict(os.environ),
        )
        return json.loads(result.stdout)["hookSpecificOutput"]

    context = run("session_start.py", {"cwd": str(repo)})["additionalContext"]
    assert "ambigu" in context
    assert "ONE" not in context and "TWO" not in context
    for command in ("gh pr create", "gh pr merge 1", "git push origin main"):
        decision = run(
            "guard_bash.py", {"cwd": str(repo), "tool_input": {"command": command}},
        )
        assert decision["permissionDecision"] == "deny"
        assert "ambigu" in decision["permissionDecisionReason"]


def test_hook_lookup_never_inherits_a_foreign_canonical_homonym(isolated):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.bootstrap_repository_binding(
        "youtrack", "same", "ONE", "0-1", cwd=str(first),
    )
    assert registry.entry_for(str(second), use_env=False) is None


def test_hook_lookup_refuses_invalid_origin_for_registered_legacy_checkout(isolated):
    repo = _repo(isolated, "acme", "app", remote="https://github.com/acme/../app.git")
    registry.register("youtrack", "app", "APP", "0-1")
    with pytest.raises(ValueError, match="identité canonique"):
        registry.entry_for(str(repo), use_env=False)


def test_explicit_legacy_environment_alias_outside_git_stays_unique(
    isolated, monkeypatch,
):
    registry.register("youtrack", "app", "APP", "0-1")
    monkeypatch.setenv("PROJECT_REPO", "app")
    assert registry.entry_for(str(isolated)) == (
        "youtrack", "app", {"key": "APP", "id": "0-1"},
    )
    registry.register("ghprojects", "app", "OTHER", "PVT_2")
    with pytest.raises(ValueError, match="ambigu"):
        registry.entry_for(str(isolated))


@pytest.mark.parametrize("state", ["dangling_marker", "symlink_parent", "dangling_parent", "file_parent"])
def test_bootstrap_refuses_invalid_marker_path_before_registry_publication(isolated, state):
    repo = _repo(isolated, "acme", "app")
    parent = repo / ".foundry"
    marker = parent / "tracker.json"
    target = isolated / "foreign"
    if state == "dangling_marker":
        parent.mkdir()
        marker.symlink_to(target)
    elif state == "file_parent":
        parent.write_text("preserve this file")
    else:
        if state == "symlink_parent":
            target.mkdir()
        parent.symlink_to(target, target_is_directory=True)
    before = registry.load()
    with pytest.raises(ValueError, match="marqueur tracker.*invalide"):
        registry.bootstrap_repository_binding("youtrack", "app", "ONE", "0-1", cwd=str(repo))
    assert registry.load() == before
    if state == "dangling_marker":
        assert marker.is_symlink()
        assert marker.readlink() == target
    elif state == "file_parent":
        assert parent.read_text() == "preserve this file"
    else:
        assert parent.is_symlink()
        assert parent.readlink() == target
        assert not marker.exists()


@pytest.mark.parametrize("state", ["existing", "dangling"])
def test_common_selection_refuses_symlink_parent_without_marker(isolated, state):
    repo = _repo(isolated, "acme", "app")
    target = isolated / "foreign"
    if state == "existing":
        target.mkdir()
    (repo / ".foundry").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="marqueur tracker.*invalide"):
        registry.repository_tracker_binding(str(repo))


# --- PLP-385: registry rebind -------------------------------------------------

_OLD = "github.com/old-owner/app"
_NEW = "github.com/new-owner/app"
_MANIFEST = "sha256:" + "ab" * 32


def _transferred(isolated, monkeypatch, verdicts=None):
    """A migrated V1 binding on the old identity, then the remote moves to the new one."""
    repo = _repo(isolated, "old-owner", "app")
    registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="keep",
        release_ids={"1.0": "r1"},
    )
    entry = registry.load()["youtrack"]["app"]
    registry._publish_marker(repo, registry._v2_marker_payload(
        "youtrack", _OLD, entry, activation_kind="migration",
        migration_manifest_digest=_MANIFEST,
    ))
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin",
         "https://github.com/new-owner/app.git"], check=True,
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("YOUTRACK_URL", "https://youtrack.example.invalid")
    monkeypatch.setenv("YOUTRACK_TOKEN", "synthetic-token-never-sent")
    seen = []

    def verify(self, project):
        seen.append(project)
        return True if verdicts is None else verdicts

    monkeypatch.setattr(YouTrackTracker, "verify_project_identity", verify)
    return repo, seen


def test_rebind_moves_entry_and_marker_keeping_extras_and_migration(
    isolated, monkeypatch, capsys,
):
    repo, seen = _transferred(isolated, monkeypatch)
    registry.main(["rebind", "youtrack", _OLD, _NEW])
    out = json.loads(capsys.readouterr().out)
    assert out["repository"] == _NEW and out["activation"] == "migration"
    assert seen[0].extra["canonical_repo"] == _NEW
    assert registry.load()["youtrack"]["app"] == {
        "key": "APP", "id": "0-1", "canonical_repo": _NEW,
        "ms_bundle": "keep", "release_ids": {"1.0": "r1"},
    }
    marker = json.loads((repo / ".foundry/tracker.json").read_text())
    assert marker["repository"] == _NEW
    assert marker["activation"] == {"kind": "migration", "manifest_digest": _MANIFEST}
    binding = registry.repository_tracker_binding(str(repo))
    assert binding.migration_manifest_digest == _MANIFEST


def test_rebind_refuses_provider_mismatch_with_steps_and_writes_nothing(
    isolated, monkeypatch,
):
    repo, _seen = _transferred(isolated, monkeypatch, verdicts=False)
    before = registry.load()
    marker = (repo / ".foundry/tracker.json").read_text()
    with pytest.raises(SystemExit, match="Marche à suivre.*Rien n'a été écrit"):
        registry.main(["rebind", "youtrack", _OLD, _NEW])
    assert registry.load() == before
    assert (repo / ".foundry/tracker.json").read_text() == marker


@pytest.mark.parametrize("cause", [
    "coordonnées du projet 'youtrack' indisponibles à la relecture",
    "mode de gouvernance refusé",
])
def test_rebind_keeps_the_cause_of_a_non_identity_provider_refusal(
    isolated, monkeypatch, cause,
):
    repo, _seen = _transferred(isolated, monkeypatch)
    before = registry.load()

    def refuse(*_args, **_kwargs):
        raise ValueError(cause)

    monkeypatch.setattr(registry, "verify_existing_project", refuse)
    with pytest.raises(ValueError) as refused:
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert str(refused.value) == cause and "Marche à suivre" not in str(refused.value)
    assert registry.load() == before


@pytest.mark.parametrize("old, new, message", [
    ("github.com/other/app", _NEW, "marqueur étranger"),
    (_OLD, "github.com/new-owner/elsewhere", "remote origin du checkout"),
    (_NEW, _NEW, "identités identiques"),
])
def test_rebind_refuses_foreign_marker_or_checkout(
    isolated, monkeypatch, old, new, message,
):
    repo, seen = _transferred(isolated, monkeypatch)
    before = registry.load()
    with pytest.raises(ValueError, match=message):
        registry.rebind_repository_binding("youtrack", old, new, cwd=str(repo))
    assert registry.load() == before and seen == []


def test_rebind_refuses_ambiguous_old_entries_and_existing_new_entry(
    isolated, monkeypatch,
):
    repo, seen = _transferred(isolated, monkeypatch)
    data = registry.load()
    data["youtrack"]["twin"] = {**data["youtrack"]["app"], "ms_bundle": "other"}
    registry._save(data)
    with pytest.raises(ValueError, match="ambiguë"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    data["youtrack"]["twin"] = {**data["youtrack"]["app"], "canonical_repo": _NEW}
    registry._save(data)
    with pytest.raises(ValueError, match="--marker-only"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert seen == []


def test_rebind_marker_only_republishes_another_checkout_without_registry_write(
    isolated, monkeypatch,
):
    repo, _seen = _transferred(isolated, monkeypatch)
    registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    # A second checkout still carries the stale marker of the old identity.
    second = _repo(isolated, "new-owner", "app-second", remote="https://github.com/new-owner/app.git")
    (second / ".foundry").mkdir()
    (second / ".foundry/tracker.json").write_text(json.dumps(registry._v2_marker_payload(
        "youtrack", _OLD, {"key": "APP", "id": "0-1", "canonical_repo": _OLD},
        activation_kind="migration", migration_manifest_digest=_MANIFEST,
    )))
    registry_file = registry.path()
    before = open(registry_file).read()
    calls = []
    monkeypatch.setattr(registry, "_save", lambda data: calls.append(data))

    registry.rebind_repository_binding(
        "youtrack", _OLD, _NEW, cwd=str(second), marker_only=True,
    )
    assert calls == [] and open(registry_file).read() == before
    marker = json.loads((second / ".foundry/tracker.json").read_text())
    assert marker["repository"] == _NEW
    assert marker["activation"]["manifest_digest"] == _MANIFEST
    assert registry.repository_tracker_binding(str(second)).repository == _NEW


def test_rebind_cli_usage_and_help_write_nothing(isolated, capsys):
    registry.main(["rebind", "--help"])
    assert "rebind <tracker>" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="rebind <tracker>"):
        registry.main(["rebind", "youtrack", _OLD])
    with pytest.raises(SystemExit, match="rebind <tracker>"):
        registry.main(["rebind", "youtrack", _OLD, _NEW, "--bogus"])
    assert registry.load() == {}


def _with_other_provider_entry(archived):
    data = registry.load()
    tombstone = {"key": "L", "id": "u", "canonical_repo": _OLD}
    if archived:
        tombstone["archive"] = True
    data["linear"] = {"app": tombstone}
    registry._save(data)
    return tombstone


def test_rebind_ignores_archived_other_provider_entry_and_leaves_it_unchanged(
    isolated, monkeypatch,
):
    repo, _seen = _transferred(isolated, monkeypatch)
    tombstone = _with_other_provider_entry(archived=True)
    registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert registry.load()["linear"]["app"] == tombstone
    assert registry.load()["youtrack"]["app"]["canonical_repo"] == _NEW


def test_rebind_marker_only_succeeds_beside_archived_other_provider_entry(
    isolated, monkeypatch,
):
    repo, _seen = _transferred(isolated, monkeypatch)
    registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    tombstone = _with_other_provider_entry(archived=True)
    registry.rebind_repository_binding(
        "youtrack", _OLD, _NEW, cwd=str(repo), marker_only=True,
    )
    assert registry.load()["linear"]["app"] == tombstone


def test_rebind_still_refuses_active_other_provider_entry(isolated, monkeypatch):
    repo, seen = _transferred(isolated, monkeypatch)
    _with_other_provider_entry(archived=False)
    before = registry.load()
    with pytest.raises(ValueError, match="ambiguë"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert registry.load() == before and seen == []


def test_rebind_refuses_absent_registry_entry(isolated, monkeypatch):
    repo, seen = _transferred(isolated, monkeypatch)
    registry._save({})
    with pytest.raises(ValueError, match="entrée de registre absente"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert registry.load() == {} and seen == []


def test_rebind_refuses_marker_and_entry_on_different_projects(isolated, monkeypatch):
    repo, seen = _transferred(isolated, monkeypatch)
    data = registry.load()
    data["youtrack"]["app"]["key"] = "OTHER"
    registry._save(data)
    with pytest.raises(ValueError, match="projets différents"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert registry.load() == data and seen == []


def test_rebind_refuses_outside_a_git_checkout(isolated):
    outside = isolated / "plain"
    outside.mkdir()
    with pytest.raises(ValueError, match="hors dépôt Git"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(outside))


def test_rebind_marker_only_refuses_when_old_identity_still_has_an_entry(
    isolated, monkeypatch,
):
    repo, seen = _transferred(isolated, monkeypatch)
    registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    data = registry.load()
    data["youtrack"]["old"] = {**data["youtrack"]["app"], "canonical_repo": _OLD}
    registry._save(data)
    seen.clear()
    with pytest.raises(ValueError, match="l'ancienne identité a encore une entrée"):
        registry.rebind_repository_binding(
            "youtrack", _OLD, _NEW, cwd=str(repo), marker_only=True,
        )
    assert registry.load() == data and seen == []


def test_rebind_rename_keeps_storage_key_and_refuses_legacy_collision(
    isolated, monkeypatch,
):
    repo, seen = _transferred(isolated, monkeypatch)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin",
         "https://github.com/new-owner/renamed.git"], check=True,
    )
    new = "github.com/new-owner/renamed"
    data = registry.load()
    data["linear"] = {"renamed": {"key": "L", "id": "u"}}
    registry._save(data)
    with pytest.raises(ValueError, match="ambigu entre providers"):
        registry.rebind_repository_binding("youtrack", _OLD, new, cwd=str(repo))
    assert registry.load() == data and seen == []
    del data["linear"]
    registry._save(data)
    registry.rebind_repository_binding("youtrack", _OLD, new, cwd=str(repo))
    assert registry.load()["youtrack"]["app"]["canonical_repo"] == new
    assert "renamed" not in registry.load()["youtrack"]


def test_rebind_refuses_checkout_without_marker_with_steps(isolated, monkeypatch):
    repo, seen = _transferred(isolated, monkeypatch)
    (repo / ".foundry/tracker.json").unlink()
    before = registry.load()
    with pytest.raises(ValueError, match="pas de marqueur.*--marker-only"):
        registry.rebind_repository_binding("youtrack", _OLD, _NEW, cwd=str(repo))
    assert registry.load() == before and seen == []


def test_rebind_ghprojects_owner_change_is_refused_before_the_provider_read(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "old-owner", "app")
    entry = {
        "key": "APP", "id": "PVT_x", "canonical_repo": _OLD,
        "owner": "old-owner", "number": "1",
    }
    registry._save({"ghprojects": {"app": entry}})
    registry._publish_marker(repo, registry._v2_marker_payload(
        "ghprojects", _OLD, entry, activation_kind="bootstrap",
        migration_manifest_digest=None,
    ))
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin",
         "https://github.com/new-owner/app.git"], check=True,
    )
    def boom(*_a, **_k):
        raise AssertionError("provider read reached")

    monkeypatch.setattr(registry, "verify_existing_project", boom)
    with pytest.raises(ValueError, match="changement de propriétaire"):
        registry.rebind_repository_binding("ghprojects", _OLD, _NEW, cwd=str(repo))
    assert registry.load() == {"ghprojects": {"app": entry}}
