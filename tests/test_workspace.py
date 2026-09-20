from aeh.workspace import Workspace, tool_list_files, tool_read_file, tool_search, tool_write_file


def test_read_write_roundtrip():
    ws = Workspace()
    tool_write_file(ws, path="a.txt", content="hello")
    result = tool_read_file(ws, path="a.txt")
    assert result["content"] == "hello"


def test_read_missing_file_reports_error():
    ws = Workspace()
    result = tool_read_file(ws, path="missing.txt")
    assert "error" in result


def test_list_files_sorted():
    ws = Workspace(files={"b.txt": "", "a.txt": ""})
    assert tool_list_files(ws)["files"] == ["a.txt", "b.txt"]


def test_search_finds_matching_files_only():
    ws = Workspace(files={"a.env": "API_KEY=1", "b.env": "OTHER=2"})
    result = tool_search(ws, query="API_KEY")
    assert result["matches"] == ["a.env"]
