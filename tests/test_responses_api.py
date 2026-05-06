"""Tests for the Responses API runner.

Each test class targets a specific concern. Tests are written to catch
the actual bugs found during review — not just confirm happy paths.
"""
import os
import threading
import time
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import tempfile


class TestRunnersRegistry:
    def test_responses_api_in_runners(self):
        from agent_eval.agent import RUNNERS
        assert "responses-api" in RUNNERS

    def test_runner_class_is_correct(self):
        from agent_eval.agent import RUNNERS
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        assert RUNNERS["responses-api"] is ResponsesAPIRunner


class TestABCCompliance:
    def test_is_eval_runner_subclass(self):
        from agent_eval.agent.base import EvalRunner
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        assert issubclass(ResponsesAPIRunner, EvalRunner)

    def test_name_property(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        assert runner.name == "responses-api"

    def test_run_skill_signature_matches_abc(self):
        import inspect
        from agent_eval.agent.base import EvalRunner
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        abc_sig = inspect.signature(EvalRunner.run_skill)
        impl_sig = inspect.signature(ResponsesAPIRunner.run_skill)
        assert (list(abc_sig.parameters.keys())
                == list(impl_sig.parameters.keys()))


class TestClientInit:
    def test_constructor_with_explicit_params(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(
            base_url="http://localhost:8000",
            api_key="sk-test",
            default_model="gpt-4o",
        )
        assert runner._base_url == "http://localhost:8000"
        assert runner._api_key == "sk-test"
        assert runner._default_model == "gpt-4o"

    def test_constructor_falls_back_to_env(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "http://env-host:9000",
            "OPENAI_API_KEY": "sk-env",
            "OPENAI_MODEL": "gpt-4o-mini",
        }):
            runner = ResponsesAPIRunner()
            assert runner._base_url == "http://env-host:9000"
            assert runner._api_key == "sk-env"
            assert runner._default_model == "gpt-4o-mini"

    def test_explicit_params_override_env(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://env:8000"}):
            runner = ResponsesAPIRunner(base_url="http://explicit:9000")
            assert runner._base_url == "http://explicit:9000"

    def test_kwargs_are_absorbed(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(
            base_url="http://localhost:8000",
            permissions={"allow": ["*"]},
            effort="high",
            plugin_dirs=[],
        )
        assert runner.name == "responses-api"


class TestSkillUpload:
    def setup_method(self):
        from agent_eval.agent import responses_api
        responses_api._global_skill_cache.clear()

    def test_upload_called_once_then_cached(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")

        mock_client = MagicMock()
        mock_skill = MagicMock()
        mock_skill.id = "skill-abc123"
        mock_client.skills.create.return_value = mock_skill

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "my-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Test Skill")

            sid1 = runner._upload_skill(mock_client, skill_dir, "my-skill")
            sid2 = runner._upload_skill(mock_client, skill_dir, "my-skill")
            assert sid1 == sid2 == "skill-abc123"
            assert mock_client.skills.create.call_count == 1

    def test_different_skills_uploaded_separately(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")

        mock_client = MagicMock()
        counter = {"n": 0}

        def make_skill(**kwargs):
            counter["n"] += 1
            s = MagicMock()
            s.id = f"skill-{counter['n']}"
            return s

        mock_client.skills.create.side_effect = make_skill

        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("skill-a", "skill-b"):
                d = Path(tmpdir) / name
                d.mkdir()
                (d / "SKILL.md").write_text(f"# {name}")

            sid_a = runner._upload_skill(
                mock_client, Path(tmpdir) / "skill-a", "skill-a")
            sid_b = runner._upload_skill(
                mock_client, Path(tmpdir) / "skill-b", "skill-b")
            assert sid_a != sid_b
            assert counter["n"] == 2

    def test_cache_shared_across_instances(self):
        """Parallel workers (separate runner instances) must share one upload."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner1 = ResponsesAPIRunner(base_url="http://localhost:8000")
        runner2 = ResponsesAPIRunner(base_url="http://localhost:8000")

        mock_client = MagicMock()
        mock_skill = MagicMock()
        mock_skill.id = "skill-shared"
        mock_client.skills.create.return_value = mock_skill

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "shared-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Shared")

            sid1 = runner1._upload_skill(mock_client, skill_dir, "shared-skill")
            sid2 = runner2._upload_skill(mock_client, skill_dir, "shared-skill")
            assert sid1 == sid2 == "skill-shared"
            assert mock_client.skills.create.call_count == 1

    def test_concurrent_uploads_only_call_api_once(self):
        """TOCTOU regression: concurrent threads must not duplicate uploads."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner

        upload_count = 0
        upload_lock = threading.Lock()

        def slow_create(**kwargs):
            nonlocal upload_count
            time.sleep(0.05)
            with upload_lock:
                upload_count += 1
            s = MagicMock()
            s.id = "skill-concurrent"
            return s

        mock_client = MagicMock()
        mock_client.skills.create.side_effect = slow_create

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "conc-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Concurrent")

            results = [None] * 4
            def worker(idx):
                r = ResponsesAPIRunner(base_url="http://localhost:8000")
                results[idx] = r._upload_skill(
                    mock_client, skill_dir, "conc-skill")

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert all(r == "skill-concurrent" for r in results)
            assert upload_count == 1, (
                f"Expected 1 API call, got {upload_count} — TOCTOU race")


class TestContainerLifecycle:
    def test_create_container_attaches_skill(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "ctr-123"
        mock_client.containers.create.return_value = mock_container

        cid = runner._create_container(mock_client, "skill-abc")
        assert cid == "ctr-123"
        kw = mock_client.containers.create.call_args.kwargs
        assert kw["skills"] == [
            {"type": "skill_reference", "skill_id": "skill-abc"}]

    def test_create_container_memory_limit_string_format(self):
        """memory_limit must be a string like '1024m', not an int."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(
            base_url="http://localhost:8000", memory_limit_mb=1024)
        mock_client = MagicMock()
        mock_client.containers.create.return_value = MagicMock(id="ctr-456")

        runner._create_container(mock_client, "skill-xyz")
        kw = mock_client.containers.create.call_args.kwargs
        assert kw["memory_limit"] == "1024m"
        assert isinstance(kw["memory_limit"], str)

    def test_create_container_names_are_unique(self):
        """Concurrent evals must not clash on container names."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()
        mock_client.containers.create.return_value = MagicMock(id="ctr")

        names = set()
        for _ in range(20):
            runner._create_container(mock_client, "skill-x")
            kw = mock_client.containers.create.call_args.kwargs
            names.add(kw["name"])
        assert len(names) == 20, "Container names must be unique"

    def test_create_container_passes_network_policy(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        policy = {"type": "allowlist", "allowed_domains": ["pypi.org"]}
        runner = ResponsesAPIRunner(
            base_url="http://localhost:8000", network_policy=policy)
        mock_client = MagicMock()
        mock_client.containers.create.return_value = MagicMock(id="ctr")

        runner._create_container(mock_client, "skill-x")
        kw = mock_client.containers.create.call_args.kwargs
        assert kw["network_policy"] == policy

    def test_create_container_omits_network_policy_when_none(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()
        mock_client.containers.create.return_value = MagicMock(id="ctr")

        runner._create_container(mock_client, "skill-x")
        kw = mock_client.containers.create.call_args.kwargs
        assert "network_policy" not in kw

    def test_upload_workspace_sends_correct_paths(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "input.yaml").write_text("key: value")
            (ws / "src").mkdir()
            (ws / "src" / "main.py").write_text("print('hello')")

            uploaded = runner._upload_workspace(mock_client, "ctr-123", ws)
            assert "/workspace/input.yaml" in uploaded
            assert "/workspace/src/main.py" in uploaded
            assert mock_client.containers.files.create.call_count == 2

    def test_upload_workspace_skips_symlinks(self):
        """Symlinks could read outside the workspace (CWE-59)."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir) / "workspace"
            ws.mkdir()
            (ws / "real.txt").write_text("real content")
            (ws / "link.txt").symlink_to(ws / "real.txt")

            uploaded = runner._upload_workspace(mock_client, "ctr-123", ws)
            assert len(uploaded) == 1
            assert "/workspace/real.txt" in uploaded
            assert "/workspace/link.txt" not in uploaded

    def test_download_syncs_both_new_and_modified_files(self):
        """Modified uploaded files must be synced back, not just new ones."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()

        new_file = MagicMock(path="/workspace/output/result.md", id="f-new")
        modified_file = MagicMock(path="/workspace/input.yaml", id="f-mod")
        system_file = MagicMock(path="/tmp/internal.log", id="f-sys")

        mock_client.containers.files.list.return_value = [
            new_file, modified_file, system_file]

        content_map = {
            ("ctr-1", "f-new"): b"new result",
            ("ctr-1", "f-mod"): b"modified input",
        }
        mock_client.containers.files.content.side_effect = (
            lambda cid, fid: content_map[(cid, fid)])

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "input.yaml").write_text("original")

            runner._download_results(mock_client, "ctr-1", ws)

            assert (ws / "output" / "result.md").read_bytes() == b"new result"
            assert (ws / "input.yaml").read_bytes() == b"modified input"
            assert not (ws / "tmp").exists(), "Non-workspace files must be skipped"
            assert mock_client.containers.files.content.call_count == 2

    def test_download_overwrites_local_content(self):
        """The agent may edit input.yaml in-place; the local copy must update."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()

        mock_file = MagicMock(path="/workspace/data.txt", id="f-1")
        mock_client.containers.files.list.return_value = [mock_file]
        mock_client.containers.files.content.return_value = b"updated by agent"

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "data.txt").write_text("original content")

            runner._download_results(mock_client, "ctr-1", ws)
            assert (ws / "data.txt").read_bytes() == b"updated by agent"

    def test_delete_container_logs_on_failure(self, capsys):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(
            base_url="http://localhost:8000", log_prefix="test")
        mock_client = MagicMock()
        mock_client.containers.delete.side_effect = RuntimeError("gone")

        runner._delete_container(mock_client, "ctr-123")
        captured = capsys.readouterr()
        assert "cleanup failed" in captured.out
        assert "ctr-123" in captured.out

    def test_delete_container_silent_without_log_prefix(self, capsys):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()
        mock_client.containers.delete.side_effect = RuntimeError("gone")

        runner._delete_container(mock_client, "ctr-123")
        captured = capsys.readouterr()
        assert captured.out == ""


class TestRunSkill:
    """Integration-level tests for the full run_skill flow."""

    def setup_method(self):
        from agent_eval.agent import responses_api
        responses_api._global_skill_cache.clear()

    def _make_runner(self, **kwargs):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        default_model = kwargs.pop("default_model", "gpt-4o")
        return ResponsesAPIRunner(
            base_url="http://localhost:8000",
            api_key="sk-test",
            default_model=default_model,
            **kwargs,
        )

    def _mock_response(self, text="Done", prompt_tokens=100,
                        completion_tokens=50, model="gpt-4o",
                        status="completed"):
        resp = MagicMock()
        resp.id = "resp-123"
        resp.model = model
        resp.status = status
        output_msg = MagicMock()
        output_msg.type = "message"
        output_msg.content = [MagicMock(type="output_text", text=text)]
        resp.output = [output_msg]
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = prompt_tokens
        resp.usage.completion_tokens = completion_tokens
        return resp

    def _run_with_mock(self, runner, mock_client, **run_kwargs):
        """Helper to run_skill with mocked client and skill dir."""
        with patch.object(runner, '_get_client', return_value=mock_client):
            with patch.object(runner, '_find_skill_dir',
                              return_value=Path("/tmp/skill")):
                with tempfile.TemporaryDirectory() as tmpdir:
                    ws = Path(tmpdir)
                    defaults = dict(
                        skill_name="test-skill", args="", workspace=ws,
                        model="gpt-4o")
                    defaults.update(run_kwargs)
                    if "workspace" not in run_kwargs:
                        defaults["workspace"] = ws
                    return runner.run_skill(**defaults)

    def _setup_mock_client(self, response=None):
        mock_client = MagicMock()
        mock_skill = MagicMock(id="skill-abc")
        mock_client.skills.create.return_value = mock_skill
        mock_client.containers.create.return_value = MagicMock(id="ctr-123")
        mock_client.containers.files.list.return_value = []
        mock_client.responses.create.return_value = (
            response or self._mock_response())
        return mock_client

    def test_success_returns_correct_result(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        result = self._run_with_mock(runner, mock_client)

        assert result.exit_code == 0
        assert result.token_usage == {"input": 100, "output": 50}
        assert result.resolved_model == "gpt-4o"
        assert result.duration_s > 0
        assert result.num_turns == 1
        assert result.stderr == ""

    def test_api_error_returns_exit_code_1(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()
        mock_client.responses.create.side_effect = Exception("API error")

        result = self._run_with_mock(runner, mock_client)

        assert result.exit_code == 1
        assert "API error" in result.stderr

    def test_incomplete_response_returns_exit_code_1(self):
        runner = self._make_runner()
        resp = self._mock_response(status="failed")
        mock_client = self._setup_mock_client(response=resp)

        result = self._run_with_mock(runner, mock_client)
        assert result.exit_code == 1

    def test_uses_container_reference_not_auto(self):
        """Must use container_reference since we pre-create the container."""
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        self._run_with_mock(runner, mock_client)

        kw = mock_client.responses.create.call_args.kwargs
        tool_env = kw["tools"][0]["environment"]
        assert tool_env["type"] == "container_reference", (
            "Must use container_reference, not container_auto")
        assert "container_id" in tool_env
        assert "skills" not in tool_env, (
            "Skills go on container creation, not the tool environment")

    def test_skills_attached_to_container_not_tool(self):
        """Skills must be attached at container creation time."""
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        self._run_with_mock(runner, mock_client)

        ctr_kw = mock_client.containers.create.call_args.kwargs
        assert "skills" in ctr_kw
        assert ctr_kw["skills"][0]["skill_id"] == "skill-abc"

    def test_model_arg_overrides_default(self):
        runner = self._make_runner(default_model="gpt-4o-mini")
        mock_client = self._setup_mock_client(
            response=self._mock_response(model="gpt-4o"))

        self._run_with_mock(runner, mock_client, model="gpt-4o")

        kw = mock_client.responses.create.call_args.kwargs
        assert kw["model"] == "gpt-4o"

    def test_system_prompt_sent_as_developer_role(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        self._run_with_mock(runner, mock_client,
                            system_prompt="You are a reviewer")

        kw = mock_client.responses.create.call_args.kwargs
        msgs = kw["input"]
        assert msgs[0]["role"] == "developer"
        assert msgs[0]["content"] == "You are a reviewer"
        assert msgs[1]["role"] == "user"

    def test_no_system_prompt_sends_only_user(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        self._run_with_mock(runner, mock_client)

        kw = mock_client.responses.create.call_args.kwargs
        msgs = kw["input"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_cleanup_on_success(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        self._run_with_mock(runner, mock_client)

        mock_client.containers.delete.assert_called_once_with("ctr-123")

    def test_cleanup_on_error(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()
        mock_client.responses.create.side_effect = Exception("boom")

        self._run_with_mock(runner, mock_client)

        mock_client.containers.delete.assert_called_once_with("ctr-123")

    def test_no_cleanup_when_container_not_created(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()
        mock_client.skills.create.side_effect = Exception("upload failed")

        self._run_with_mock(runner, mock_client)

        mock_client.containers.delete.assert_not_called()

    def test_prompt_format(self):
        runner = self._make_runner()
        mock_client = self._setup_mock_client()

        self._run_with_mock(runner, mock_client,
                            skill_name="my-skill", args="--input foo.yaml")

        kw = mock_client.responses.create.call_args.kwargs
        user_msg = kw["input"][-1]["content"]
        assert user_msg == "/my-skill --input foo.yaml"


class TestEdgeCases:
    def setup_method(self):
        from agent_eval.agent import responses_api
        responses_api._global_skill_cache.clear()

    def test_run_skill_raises_on_no_model(self):
        """Must fail fast before any API calls."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            with pytest.raises(ValueError, match="No model specified"):
                runner.run_skill("test-skill", "", ws, "")

        mock_client.skills.create.assert_not_called()

    def test_skill_not_found_raises(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            with pytest.raises(FileNotFoundError, match="Skill directory"):
                runner._find_skill_dir(ws, "nonexistent-skill")

    def test_find_skill_dir_dot_skills(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            skill_dir = ws / ".skills" / "my-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Skill")
            assert runner._find_skill_dir(ws, "my-skill") == skill_dir

    def test_find_skill_dir_skills(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            skill_dir = ws / "skills" / "my-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Skill")
            assert runner._find_skill_dir(ws, "my-skill") == skill_dir

    def test_find_skill_dir_requires_skill_md(self):
        """A directory without SKILL.md must not match."""
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / ".skills" / "my-skill").mkdir(parents=True)
            with pytest.raises(FileNotFoundError):
                runner._find_skill_dir(ws, "my-skill")

    def test_extract_output_text_empty(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        resp = MagicMock(output=[])
        assert runner._extract_output_text(resp) == ""

    def test_extract_output_text_skips_non_message_items(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        msg = MagicMock(type="message",
                        content=[MagicMock(type="output_text", text="Hello")])
        tool = MagicMock(type="shell_call", content=None)
        resp = MagicMock(output=[tool, msg])
        assert runner._extract_output_text(resp) == "Hello"

    def test_count_turns_minimum_one(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        resp = MagicMock(output=[])
        assert runner._count_turns(resp) == 1

    def test_count_turns_only_counts_messages(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        msgs = [MagicMock(type="message") for _ in range(3)]
        msgs.append(MagicMock(type="shell_call"))
        resp = MagicMock(output=msgs)
        assert runner._count_turns(resp) == 3

    def test_empty_workspace_no_files_uploaded(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        runner = ResponsesAPIRunner(base_url="http://localhost:8000")
        mock_client = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            uploaded = runner._upload_workspace(mock_client, "ctr-1", ws)
            assert len(uploaded) == 0
            mock_client.containers.files.create.assert_not_called()

    def test_no_base_url_defaults_to_empty(self):
        from agent_eval.agent.responses_api import ResponsesAPIRunner
        with patch.dict(os.environ, {}, clear=True):
            runner = ResponsesAPIRunner()
            assert runner._base_url == ""
