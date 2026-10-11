"""MCP stdio transport: one JSON-RPC message per line; stdout is protocol only."""

import json
import sys
from . import __version__, experiment

OBJ = {"type": "object"}
TOOLS = [
    (
        "rocketlab_doctor",
        "Inspect capabilities, budgets and native session readiness.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        True,
    ),
    (
        "rocketlab_presets",
        "List synthetic/demo-compatible starter experiment definitions.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        True,
    ),
    (
        "rocketlab_validate",
        "Validate a versioned experiment without running it.",
        {
            "type": "object",
            "properties": {"experiment": OBJ},
            "required": ["experiment"],
            "additionalProperties": False,
        },
        True,
    ),
    (
        "rocketlab_run",
        "Run one bounded experiment; demo is synthetic, native requires an armed offline plugin.",
        {
            "type": "object",
            "properties": {
                "experiment": OBJ,
                "backend": {
                    "type": "string",
                    "enum": ["demo", "native"],
                    "default": "demo",
                },
            },
            "required": ["experiment"],
            "additionalProperties": False,
        },
        False,
    ),
    (
        "rocketlab_jobs",
        "List recent experiment jobs.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        True,
    ),
    (
        "rocketlab_status",
        "Read a job status.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        True,
    ),
    (
        "rocketlab_result",
        "Read a bounded decimated preview and integrity report; never an exact acceptance claim.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        True,
    ),
    (
        "rocketlab_cancel",
        "Cancel this service's active job; native cancellation attempts neutralization.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        False,
    ),
]


class Server:
    def __init__(self, service):
        self.service = service
        self.initialized = False

    def dispatch(self, message):
        ident = message.get("id")
        method = message.get("method")
        params = message.get("params", {})

        def error(code, text):
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": code, "message": text},
            }

        if (
            message.get("jsonrpc") != "2.0"
            or not isinstance(method, str)
            or not isinstance(params, dict)
        ):
            return error(-32600, "Invalid request")
        if method == "notifications/initialized":
            return None
        if "id" not in message:
            return None
        if method == "initialize":
            self.initialized = True
            requested = params.get("protocolVersion")
            version = (
                requested
                if requested in ("2024-11-05", "2025-03-26", "2025-06-18")
                else "2025-06-18"
            )
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "RocketLaboratory", "version": __version__},
                "instructions": "Native timing and hidden-state restoration are unqualified. Demo output is synthetic. Run offline only. No arbitrary console commands.",
            }
        elif method == "ping":
            result = {}
        elif not self.initialized:
            return error(-32000, "Initialize first")
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": desc,
                        "inputSchema": schema,
                        "annotations": {
                            "readOnlyHint": read,
                            "openWorldHint": not read,
                        },
                    }
                    for name, desc, schema, read in TOOLS
                ]
            }
        elif method == "tools/call":
            try:
                name = params["name"]
                args = params.get("arguments", {})
                entry = next((entry for entry in TOOLS if entry[0] == name), None)
                if not entry:
                    raise ValueError("Unknown tool")
                schema = entry[2]
                if (
                    not isinstance(args, dict)
                    or set(args) - schema["properties"].keys()
                    or set(schema.get("required", [])) - args.keys()
                ):
                    raise ValueError("Unexpected or missing arguments")
                if name == "rocketlab_doctor":
                    value = self.service.doctor()
                elif name == "rocketlab_presets":
                    value = experiment.presets()
                elif name == "rocketlab_validate":
                    value = {
                        "valid": True,
                        "experiment": experiment.validate(args["experiment"]),
                    }
                elif name == "rocketlab_run":
                    value = self.service.submit(
                        args["experiment"], args.get("backend", "demo")
                    )
                elif name == "rocketlab_jobs":
                    value = self.service.list()
                elif name == "rocketlab_status":
                    value = self.service.status(args["job_id"])
                elif name == "rocketlab_result":
                    value = self.service.result(args["job_id"])
                else:
                    value = self.service.cancel(args["job_id"])
                result = {
                    "content": [
                        {"type": "text", "text": json.dumps(value, allow_nan=False)}
                    ],
                    "isError": False,
                }
            except (KeyError, ValueError, RuntimeError, OSError, TypeError) as error_:
                result = {
                    "content": [{"type": "text", "text": str(error_)}],
                    "isError": True,
                }
        else:
            return error(-32601, "Method not found")
        return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve(service, reader=None, writer=None):
    reader = reader or sys.stdin
    writer = writer or sys.stdout
    server = Server(service)
    for line in iter(lambda: reader.readline(experiment.MAX_JSON + 1), ""):
        if (
            len(line) > experiment.MAX_JSON
            or len(line.encode("utf-8")) > experiment.MAX_JSON
        ):
            raise ValueError("MCP message size budget exceeded")
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Message must be an object")
            response = server.dispatch(message)
        except (ValueError, TypeError) as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        if response is not None:
            writer.write(json.dumps(response, allow_nan=False) + "\n")
            writer.flush()
