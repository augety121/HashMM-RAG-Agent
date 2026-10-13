from hashmm.api.chain_executor import ToolChainExecutor


def _events(source: str):
    def tool_exec(tool, args, _ctx):
        if tool == "create_file":
            return "OK created"
        if tool == "execute_code":
            return "OK executed"
        raise AssertionError(tool)

    executor = ToolChainExecutor(tool_exec, "conv-test")
    return list(executor._chain_write_python(source))


def test_python_chain_emits_deterministic_verification_without_reviewer_claim():
    events = _events(
        "```python\n"
        "# filename: sample.py\n"
        "def answer():\n"
        "    return 42\n"
        "```"
    )
    traces = [event.data for event in events if event.event == "trace"]
    assert traces == [{
        "node": "independent_verify",
        "status": "passed",
        "tool": "python_ast",
        "artifact": "sample.py",
        "checks": [{
            "name": "syntax",
            "status": "passed",
            "detail": "AST parsed without executing the artifact",
        }],
    }]
    assert not any(event.event == "token" and "代码审查" in event.data.get("content", "")
                   for event in events)


def test_python_chain_marks_syntax_failure_as_failed_trace():
    events = _events(
        "```python\n"
        "# filename: broken.py\n"
        "def answer(:\n"
        "    return 42\n"
        "```"
    )
    traces = [event.data for event in events if event.event == "trace"]
    assert traces and traces[-1]["status"] == "failed"
    assert traces[-1]["checks"][0]["status"] == "failed"
