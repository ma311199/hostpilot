from hostpilot.contracts import ContextSnapshot, Route
from hostpilot.routing import CommandRouter


def test_command_router_selects_read_and_write_tools() -> None:
    context = ContextSnapshot(request_id="request-1")
    router = CommandRouter()

    read = router.decide("/read README.md", context)
    write = router.decide("/write notes.txt\nnew content", context)

    assert read.route is Route.TOOL_CALL
    assert read.tool_name == "read_text_file"
    assert read.tool_arguments == {"path": "README.md"}
    assert write.route is Route.TOOL_CALL
    assert write.tool_name == "write_text_file"
    assert write.tool_arguments == {"path": "notes.txt", "content": "new content"}


def test_command_router_clarifies_incomplete_commands_and_keeps_chat_direct() -> None:
    context = ContextSnapshot(request_id="request-2")
    router = CommandRouter()

    missing_read_path = router.decide("/read", context)
    missing_write_content = router.decide("/write notes.txt", context)
    normal_message = router.decide("What does HostPilot do?", context)

    assert missing_read_path.route is Route.CLARIFY
    assert "相对文件路径" in missing_read_path.clarification_prompt
    assert missing_write_content.route is Route.CLARIFY
    assert normal_message.route is Route.DIRECT
