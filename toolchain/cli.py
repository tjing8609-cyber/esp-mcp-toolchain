from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esp_mcp_toolchain.tools import (  # noqa: E402
    error_tools,
    hardwork_tools,
    log_tools,
    memory_tools,
    port_tools,
    serial_tools,
)


def emit(result: dict) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("ok") is False else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esp-mcp-toolchain")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("port-list")

    port_select = sub.add_parser("port-select")
    port_select.add_argument("port")
    port_select.add_argument("--reason", default="manual")

    sub.add_parser("port-status")

    serial_capture = sub.add_parser("serial-capture")
    serial_capture.add_argument("--port")
    serial_capture.add_argument("--baudrate", type=int, default=115200)
    serial_capture.add_argument("--duration-ms", type=int, default=5000)
    serial_capture.add_argument("--session-name", default="default")
    serial_capture.add_argument("--no-stop-on-traceback", action="store_true")

    sub.add_parser("logs-latest")

    logs_get = sub.add_parser("logs-get")
    logs_get.add_argument("--run-id", required=True)
    logs_get.add_argument("--tail", type=int, default=80)

    logs_query = sub.add_parser("logs-query")
    logs_query.add_argument("--query", default="")
    logs_query.add_argument("--limit", type=int, default=20)
    logs_query.add_argument("--level")
    logs_query.add_argument("--run-id")
    logs_query.add_argument("--phase")
    logs_query.add_argument("--tool")
    logs_query.add_argument("--source")
    logs_query.add_argument("--from-ts")
    logs_query.add_argument("--to-ts")
    logs_query.add_argument("--sequence-from", type=int)
    logs_query.add_argument("--sequence-to", type=int)

    error_text = sub.add_parser("error-parse-text")
    error_text.add_argument("--text", required=True)

    sub.add_parser("hardwork-list")

    hardwork_get = sub.add_parser("hardwork-get")
    hardwork_get.add_argument("hardwork_id")

    hardwork_set = sub.add_parser("hardwork-set")
    hardwork_set.add_argument("--kind", required=True)
    hardwork_set.add_argument("--file", required=True)
    hardwork_set.add_argument("--title")
    hardwork_set.add_argument("--source", default="manual_summary")
    hardwork_set.add_argument("--confidence", type=float, default=0.8)

    memory_write = sub.add_parser("memory-write")
    memory_write.add_argument("--namespace", required=True)
    memory_write.add_argument("--key", required=True)
    memory_write.add_argument("--value", required=True)
    memory_write.add_argument("--memory-type", default="project_fact")
    memory_write.add_argument("--source", default="manual")
    memory_write.add_argument("--confidence", type=float, default=0.8)

    memory_read = sub.add_parser("memory-read")
    memory_read.add_argument("--namespace", required=True)
    memory_read.add_argument("--key", required=True)

    memory_search = sub.add_parser("memory-search")
    memory_search.add_argument("query")
    memory_search.add_argument("--limit", type=int, default=10)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "port-list":
        return emit(port_tools.esp_port_list())
    if args.command == "port-select":
        return emit(port_tools.esp_port_select(port=args.port, reason=args.reason))
    if args.command == "port-status":
        return emit(port_tools.esp_port_status())
    if args.command == "serial-capture":
        return emit(
            serial_tools.esp_serial_capture(
                port=args.port,
                baudrate=args.baudrate,
                duration_ms=args.duration_ms,
                stop_on_traceback=not args.no_stop_on_traceback,
                session_name=args.session_name,
            )
        )
    if args.command == "logs-latest":
        return emit(log_tools.esp_logs_latest())
    if args.command == "logs-get":
        return emit(log_tools.esp_logs_get(run_id=args.run_id, tail=args.tail))
    if args.command == "logs-query":
        return emit(
            log_tools.esp_logs_query(
                query=args.query,
                limit=args.limit,
                level=args.level,
                run_id=args.run_id,
                phase=args.phase,
                tool=args.tool,
                source=args.source,
                from_ts=args.from_ts,
                to_ts=args.to_ts,
                sequence_from=args.sequence_from,
                sequence_to=args.sequence_to,
            )
        )
    if args.command == "error-parse-text":
        return emit(error_tools.esp_error_parse_text(text=args.text))
    if args.command == "hardwork-list":
        return emit(hardwork_tools.hardwork_list())
    if args.command == "hardwork-get":
        return emit(hardwork_tools.hardwork_get(hardwork_id=args.hardwork_id))
    if args.command == "hardwork-set":
        content = Path(args.file).read_text(encoding="utf-8")
        return emit(
            hardwork_tools.hardwork_set(
                kind=args.kind,
                title=args.title or args.kind,
                content=content,
                source=args.source,
                confidence=args.confidence,
            )
        )
    if args.command == "memory-write":
        return emit(
            memory_tools.memory_write(
                namespace=args.namespace,
                key=args.key,
                value=args.value,
                memory_type=args.memory_type,
                source=args.source,
                confidence=args.confidence,
            )
        )
    if args.command == "memory-read":
        return emit(memory_tools.memory_read(namespace=args.namespace, key=args.key))
    if args.command == "memory-search":
        return emit(memory_tools.memory_search(query=args.query, limit=args.limit))

    return emit({"ok": False, "error_kind": "unknown_command", "message": args.command})


if __name__ == "__main__":
    raise SystemExit(main())

