import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server import (
    TOOL_ROUTING_SCHEMA_KEY,
    CallToolInput,
    GetToolDetailsInput,
    PythonExecuteInput,
    TOOL_ERROR_META_KEY,
    ToolathlonGym,
    _MCPProcess,
)


class ToolRoutingTest(unittest.IsolatedAsyncioTestCase):
    def test_builtin_tools_are_advertised_as_direct_task_tools(self) -> None:
        tools = {tool.name: tool for tool in ToolathlonGym.list_tools().tools}

        python_routing = tools["python_execute"].input_schema[TOOL_ROUTING_SCHEMA_KEY]
        self.assertEqual(
            python_routing,
            {
                "version": 1,
                "execution_domain": "task",
                "capabilities": [
                    "python.execute",
                    "filesystem.read",
                    "filesystem.write",
                    "system.execute",
                ],
                "invocation": {"kind": "direct"},
            },
        )

        claim_routing = tools["claim_done"].input_schema[TOOL_ROUTING_SCHEMA_KEY]
        self.assertEqual(
            claim_routing,
            {
                "version": 1,
                "execution_domain": "task",
                "capabilities": ["task.submit"],
                "invocation": {"kind": "direct"},
            },
        )

    def test_catalog_tools_preserve_existing_dispatch_semantics(self) -> None:
        environment = ToolathlonGym.__new__(ToolathlonGym)
        tools = {tool.name: tool for tool in environment.list_task_tools().tools}

        discover_routing = tools["get_tool_details"].input_schema[
            TOOL_ROUTING_SCHEMA_KEY
        ]
        self.assertEqual(discover_routing["capabilities"], ["tool.discover"])
        self.assertEqual(discover_routing["invocation"], {"kind": "direct"})

        dispatch_routing = tools["call_tool"].input_schema[TOOL_ROUTING_SCHEMA_KEY]
        self.assertEqual(dispatch_routing["capabilities"], ["tool.dispatch"])
        self.assertEqual(
            dispatch_routing["invocation"],
            {
                "kind": "dispatcher",
                "name_argument": "name",
                "arguments_argument": "arguments",
                "discovery_tool": "get_tool_details",
                "targets": [],
            },
        )
        self.assertNotIn("python_execute", tools["call_tool"].description)

    async def test_python_execute_is_not_a_catalog_target(self) -> None:
        environment = ToolathlonGym.__new__(ToolathlonGym)
        environment._task_tool_by_name = {}

        with self.assertRaisesRegex(ValueError, "Unknown catalog tool"):
            await environment.call_tool(
                CallToolInput(name="python_execute", arguments={"code": "1"})
            )
        with self.assertRaisesRegex(ValueError, "Unknown catalog tool"):
            await environment.get_tool_details(
                GetToolDetailsInput(name="python_execute")
            )

    async def test_unknown_catalog_target_raises(self) -> None:
        environment = ToolathlonGym.__new__(ToolathlonGym)
        environment._task_tool_by_name = {}

        with self.assertRaisesRegex(ValueError, "Unknown catalog tool"):
            await environment.call_tool(
                CallToolInput(name="missing.tool", arguments={})
            )
        with self.assertRaisesRegex(ValueError, "Unknown catalog tool"):
            await environment.get_tool_details(GetToolDetailsInput(name="missing.tool"))

    async def test_missing_bridge_raises_and_mcp_failure_is_typed(self) -> None:
        environment = ToolathlonGym.__new__(ToolathlonGym)
        environment._task_tool_by_name = {
            "server.tool": {
                "bare_name": "tool",
                "server": "server",
            }
        }
        environment._mcp_bridge = None

        with self.assertRaisesRegex(RuntimeError, "bridge is not running"):
            await environment.call_tool(CallToolInput(name="server.tool", arguments={}))

        environment._mcp_bridge = AsyncMock()
        environment._mcp_bridge.call_tool.side_effect = OSError("disconnected")
        result = await environment.call_tool(
            CallToolInput(name="server.tool", arguments={})
        )

        self.assertEqual(
            result.metadata[TOOL_ERROR_META_KEY],
            {
                "kind": "mcp_tool_error",
                "message": "MCP catalog tool 'server.tool' failed: disconnected",
            },
        )

    async def test_mcp_is_error_result_raises(self) -> None:
        process = _MCPProcess("server", SimpleNamespace())
        process.send_recv = AsyncMock(
            return_value={
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "invalid argument"}],
                }
            }
        )

        with self.assertRaisesRegex(RuntimeError, "invalid argument"):
            await process.call_tool("tool", {"bad": True})

    async def test_mcp_success_result_is_unchanged(self) -> None:
        process = _MCPProcess("server", SimpleNamespace())
        process.send_recv = AsyncMock(
            return_value={
                "result": {
                    "isError": False,
                    "content": [{"type": "text", "text": "ok"}],
                }
            }
        )

        self.assertEqual(await process.call_tool("tool", {}), "ok")

    async def test_python_nonzero_exit_is_typed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            environment = ToolathlonGym.__new__(ToolathlonGym)
            environment.workspace_dir = Path(tmp_dir)
            environment._pg_env = lambda: {}
            process = SimpleNamespace(
                returncode=7,
                communicate=AsyncMock(return_value=(b"partial output\n", b"boom\n")),
            )
            with patch(
                "server.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ):
                result = await environment.python_execute(
                    PythonExecuteInput(code="raise RuntimeError('boom')")
                )

        marker = result.metadata[TOOL_ERROR_META_KEY]
        self.assertEqual(marker["kind"], "nonzero_exit")
        self.assertIn("status 7", marker["message"])
        self.assertIn("boom", marker["message"])


if __name__ == "__main__":
    unittest.main()
