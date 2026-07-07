from esp_mcp_toolchain.server import handle_request


def test_tools_list():
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response["result"]["tools"]

