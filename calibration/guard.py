"""The room contract and its checks: what may be on disk around a calibration subject, verified by isolate.py when a
room is checked and by shim.py before every single call (Codex's verification of W4a, findings 1 and 2).

The memory contract is fixed when the room is built (isolate.build) and never changes: either the exact sha256 of the
one approved memory file (CLAUDE.md or AGENTS.md in the working directory, a regular file, never a symlink, with no
@-import), or its required absence. Every check reads the disk afresh, so a file swapped, replaced by a symlink or
planted after an earlier check is caught at the next call.

A call is checked in its own context: the directory it runs in, every parent of it up to /, the room's Claude config
dir, the memory contract, its environment (the memory switch must say what the room's condition says, both ways), and
its arguments against the exact command contract probatio and the canary use (CLAUDE_OPTIONS, CODEX_OPTIONS and the
fixed Codex -c keys): every option known, every value the room allows, so nothing can add instructions, directories,
settings or another memory setting. The one MCP server a call may name is probatio's world.py, pinned by room.json's
"world" (world_server_problems). Anything outside the contract is refused before the child starts. A Codex call is checked against the CODEX_HOME it actually receives (probatio makes a
fresh one per call): that home must lie inside the room or the run's folder, hold only auth.json, and that auth.json
must lead to the approved login. Every function returns a list of problems, empty when all is well, and every message
names paths and digests only, never file contents or secret values.
"""
import hashlib
import json
import os
import re
from pathlib import Path

PARENT_MEMORY = ("CLAUDE.md", "CLAUDE.local.md", ".claude", "AGENTS.md", "AGENTS.override.md")
# What may sit at the top of a room's config dir: Claude Code's own state, written by the calls themselves. Anything
# that carries instructions, settings, tools or a login is refused, and so is any file under projects/ or plugins/
# (auto memory and installed plugins live there).
CONFIG_FORBIDDEN = ("CLAUDE.md", "CLAUDE.local.md", "settings.json", "settings.local.json", "rules", "agents",
                    "commands", "skills", "output-styles", "hooks", ".credentials.json", "keybindings.json")
CONFIG_NO_FILES_UNDER = ("projects", "plugins")
CODEX_HOME_ALLOWED = ("auth.json",)
PROVIDER_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
MEMORY_SWITCH = "CLAUDE_CODE_DISABLE_CLAUDE_MDS"
# The exact command contracts: each option a subject call may carry, mapped to how many values it takes (0 or 1).
# They are the options probatio v3 and v4 (claude_command, codex_command, codex_resume_command) and the canary pass;
# a later probatio that needs another must add it here, with its allowed values, before a run uses it.
CLAUDE_OPTIONS = {"-p": 0, "--print": 0, "--strict-mcp-config": 0, "--no-session-persistence": 0, "--verbose": 0,
                  "--tools": 1, "--settings": 1, "--output-format": 1, "--input-format": 1, "--model": 1,
                  "--effort": 1, "--mcp-config": 1, "--max-turns": 1, "--allowedTools": 1}
CLAUDE_VALUES = {"--tools": ("",), "--output-format": ("text", "json", "stream-json"),
                 "--input-format": ("text", "stream-json")}
CLAUDE_SETTINGS = {"disableAllHooks": True}     # the only --settings probatio passes; its env could flip memory
CODEX_OPTIONS = {"--ephemeral": 0, "--skip-git-repo-check": 0, "--ignore-user-config": 0, "--ignore-rules": 0,
                 "--json": 0, "-s": 1, "--sandbox": 1, "--color": 1, "--disable": 1, "-m": 1, "--model": 1,
                 "-o": 1, "--output-last-message": 1, "-c": 1, "--config": 1}
CODEX_VALUES = {"-s": ("read-only",), "--sandbox": ("read-only",), "--color": ("never",)}
CODEX_REPEATABLE = ("-c", "--config", "--disable")
CODEX_REQUIRED = ("--ignore-user-config", "--ignore-rules")     # the call never reads a user config or rules file
CODEX_FIXED_CONFIG = {"web_search": "disabled", "sandbox_mode": "read-only"}
CODEX_MCP_KEY = re.compile(r"mcp_servers\.([A-Za-z0-9_-]+)\.(command|args|default_tools_approval_mode)")
# The one MCP server a subject call may name: probatio's own world.py, run by the probatio interpreter as start_episode
# runs it (-I -B world.py <episode>/world.json <episode>/record.jsonl <episode>/close), its episode a folder directly
# under the run's episodes/. room.json's "world" pins the interpreter, world.py's path and sha256, and that folder.
SERVER_NAME = re.compile(r"[A-Za-z0-9_-]+")
EPISODE_NAME = re.compile(r"[A-Za-z0-9.-]+")
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]+")
IMPORT = re.compile(r"(?<!\S)@\S")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---- the memory contract ----------------------------------------------------------------------------------------------
def memory_contract(memory_bytes, name):
    """The room's fixed expectation for its memory file: the exact digest of the approved bytes, or absence."""
    if memory_bytes is None:
        return {"name": name, "absent": True}
    return {"name": name, "sha256": sha256(memory_bytes)}


def import_problems(data, where):
    """A synthetic calibration memory may not import other files (Claude Code's @path), which would load unlisted text."""
    text = data.decode("utf-8", errors="replace")
    return [f"{where} contains an @-import ({m.group(0)}...); calibration memory files may not import"
            for m in IMPORT.finditer(text)][:1]


def memory_problems(work_dir, contract):
    """Whether the working directory holds exactly what the contract says: the approved regular file with its exact
    digest and nothing else, or nothing at all."""
    work_dir = Path(work_dir)
    if not work_dir.is_dir():
        return [f"{work_dir} is missing"]
    allowed = set() if contract.get("absent") else {contract["name"]}
    extra = sorted(p.name for p in work_dir.iterdir() if p.name not in allowed)
    found = [f"{work_dir} holds {', '.join(extra)}; it may hold only its approved memory file"] if extra else []
    path = work_dir / contract["name"]
    if contract.get("absent"):
        return found
    if path.is_symlink():
        return found + [f"{path} is a symlink; the approved memory must be a regular file"]
    if not path.is_file():
        return found + [f"{path} is missing; the room expects its approved memory file"]
    data = path.read_bytes()
    if sha256(data) != contract["sha256"]:
        found.append(f"{path} has sha256 {sha256(data)[:16]}..., not the approved {contract['sha256'][:16]}...")
    return found + import_problems(data, str(path))


# ---- the room's directories --------------------------------------------------------------------------------------------
def parent_memory(work_dir):
    """Every memory file or folder in a parent of the working directory, up to /: each would load into every call."""
    work_dir = Path(work_dir).resolve()
    return [p for d in work_dir.parents for p in (d / n for n in PARENT_MEMORY) if p.exists() or p.is_symlink()]


def config_problems(config_dir, operator_dirs=()):
    """What makes a room's config dir unclean: being (or sitting inside) the operator's own config, a forbidden entry,
    any symlink, any file under projects/ or plugins/, or a stored login or MCP server in .claude.json."""
    config_dir = Path(config_dir).resolve()
    found = []
    for op in operator_dirs:
        op = Path(op).expanduser().resolve()
        if config_dir == op or op in config_dir.parents:
            found.append(f"{config_dir} is the operator's own Claude config ({op})")
    for name in CONFIG_FORBIDDEN:
        if (config_dir / name).exists() or (config_dir / name).is_symlink():
            found.append(f"{config_dir / name} must not exist in a clean config dir")
    for path in sorted(config_dir.rglob("*")):
        if path.is_symlink():
            found.append(f"{path} is a symlink; a clean config dir holds none")
        elif path.is_file() and path.relative_to(config_dir).parts[0] in CONFIG_NO_FILES_UNDER:
            found.append(f"{path} is a file under {path.relative_to(config_dir).parts[0]}/ (memory or a plugin)")
    state = config_dir / ".claude.json"
    if state.is_file():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        if data.get("oauthAccount"):
            found.append(f"{state} records a stored login; the room authenticates only by the token")
        if data.get("mcpServers"):
            found.append(f"{state} lists MCP servers")
    return found


def codex_user_problems(codex_dir, auth):
    """The room's Codex user dir must hold exactly one entry, auth.json, a link to the host's login."""
    codex_dir = Path(codex_dir)
    found = [f"{codex_dir / n} exists; the Codex user dir holds only auth.json"
             for n in sorted(p.name for p in codex_dir.iterdir()) if n != "auth.json"]
    link = codex_dir / "auth.json"
    if not link.is_symlink() or Path(os.readlink(link)) != Path(auth):
        found.append(f"{link} must be a link to {auth}")
    return found


def codex_home_problems(home, auth, roots):
    """The CODEX_HOME a Codex call actually gets: inside one of the room's allowed roots, holding only auth.json, and
    that auth.json a link (directly or through the room's user dir) to the approved login."""
    if not home:
        return ["the Codex call has no CODEX_HOME; it would read the host's own Codex config"]
    home = Path(home)
    real = home.resolve()
    if not any(real == Path(r).resolve() or Path(r).resolve() in real.parents for r in roots):
        return [f"CODEX_HOME {home} lies outside the room and the run's folder"]
    if not real.is_dir():
        return [f"CODEX_HOME {home} is not a directory"]
    found = [f"{home / n} exists in CODEX_HOME; a call's home holds only auth.json"
             for n in sorted(p.name for p in real.iterdir()) if n not in CODEX_HOME_ALLOWED]
    link = real / "auth.json"
    if not link.is_symlink():
        found.append(f"{link} must be a link to the approved login, not a file of its own")
    elif link.resolve() != Path(auth).resolve():
        found.append(f"{link} leads to {link.resolve()}, not the approved login")
    return found


def executable(binary, path_var):
    """The file a CLI setting names: itself when it holds a slash, else the first executable of that name on path_var
    outside a room's bin dir (one holding room.json), so a room's wrapper never resolves to itself. None when there is
    none. run.py hashes what this returns for the registration, and the shim runs what it returns."""
    if "/" in binary:
        return binary
    for d in (path_var or "").split(os.pathsep):
        candidate = Path(d) / binary
        if d and candidate.is_file() and os.access(candidate, os.X_OK) and not (Path(d) / "room.json").exists():
            return str(candidate)
    return None


# ---- one call ---------------------------------------------------------------------------------------------------------
def world_server_problems(room, command, args):
    """Why an MCP server a call names is not the room's registered world server (empty when it is): the exact
    interpreter, world.py at its registered path with its registered sha256 (read afresh), and world.json, record.jsonl
    and close of one episode folder directly under the run's episodes/ folder."""
    world = room.get("world") or {}
    if not world.get("episodes_root") or not world.get("world_sha256"):
        return ["the call names an MCP server, but this room registered no world server"]
    found = []
    if not isinstance(command, str) or os.path.realpath(command) != os.path.realpath(world["interpreter"]):
        found.append(f"the MCP server's command is {command!r}, not the registered interpreter")
    if not (isinstance(args, list) and len(args) == 6 and all(isinstance(a, str) for a in args)
            and args[:2] == ["-I", "-B"]):
        return found + ["the MCP server's arguments are not world.py's (-I -B world.py world.json record.jsonl close)"]
    script = Path(args[2])
    if os.path.realpath(script) != os.path.realpath(world["world_py"]):
        found.append(f"the MCP server runs {script}, not the registered {world['world_py']}")
    elif not script.is_file() or sha256(script.read_bytes()) != world["world_sha256"]:
        found.append(f"{script} is not the world.py registered for this run (sha256 {world['world_sha256'][:12]}...)")
    episode = Path(args[3]).parent
    if not (Path(args[3]).name == "world.json" and args[4] == str(episode / "record.jsonl")
            and args[5] == str(episode / "close") and EPISODE_NAME.fullmatch(episode.name)
            and os.path.realpath(episode.parent) == os.path.realpath(world["episodes_root"])):
        found.append("the MCP server's world, record and close files are not one episode folder of this run")
    return found


def claude_mcp_problems(room, configs, tools):
    """--mcp-config and --allowedTools against the world server: one config, naming one stdio server that
    world_server_problems accepts; --allowedTools only with it, each tool mcp__<that server>__<tool>."""
    found, server = [], None
    if configs:
        try:
            config = json.loads(configs[0])
        except ValueError:
            config = None
        servers = config.get("mcpServers") if isinstance(config, dict) and set(config) == {"mcpServers"} else None
        if not isinstance(servers, dict) or len(servers) != 1:
            return ["the call's --mcp-config is not exactly one MCP server"]
        server, spec = next(iter(servers.items()))
        if not SERVER_NAME.fullmatch(server) or not isinstance(spec, dict) \
                or set(spec) != {"type", "command", "args"} or spec["type"] != "stdio":
            return [f"the call's --mcp-config server {server!r} is not a plain stdio server"]
        found += world_server_problems(room, spec["command"], spec["args"])
    for value in tools:
        if server is None:
            found.append("the call passes --allowedTools without the world's --mcp-config")
            continue
        found += [f"the call allows {t!r}, not a tool of its world server {server}"
                  for t in value.split(",") if not (t.startswith(f"mcp__{server}__")
                                                     and TOOL_NAME.fullmatch(t[len(f"mcp__{server}__"):]))]
    return found


def codex_mcp_problems(room, pairs):
    """Codex's mcp_servers.<name>.* overrides against the world server: one server name, its command and arguments
    (TOML written as JSON) accepted by world_server_problems, and its approval mode "approve"."""
    entries = {}
    for key, value in pairs:
        m = CODEX_MCP_KEY.fullmatch(key)
        if m:
            entries.setdefault(m.group(1), {})[m.group(2)] = value
    if not entries:
        return []
    if len(entries) != 1:
        return [f"the call names {len(entries)} MCP servers; only the world server may run"]
    name, spec = next(iter(entries.items()))
    found = [] if spec.get("default_tools_approval_mode") in (None, "approve") else \
        [f"-c mcp_servers.{name}.default_tools_approval_mode; the contract allows only 'approve'"]
    if "command" not in spec or "args" not in spec:
        return found + [f"the call's MCP server {name} lacks its command or arguments"]
    try:
        args = json.loads(spec["args"])
    except ValueError:
        args = None
    return found + world_server_problems(room, spec["command"], args)


def parse_options(args, contract):
    """(options, positionals, problems) for argv against a contract {option: number of values}: options is a list of
    (name, value or None) in order. An option outside the contract, or a value missing, is a problem; "--opt=value"
    counts as "--opt value". Nothing here judges the values."""
    options, positionals, found, i = [], [], [], 0
    while i < len(args):
        a = args[i]
        name, eq, value = a.partition("=") if a.startswith("--") else (a, "", "")
        if not a.startswith("-") or a == "-":
            positionals.append(a)
        elif name not in contract:
            found.append(f"the call passes {name}, which is outside the room's command contract")
        elif contract[name] == 0:
            if eq:
                found.append(f"the call gives {name} a value; it takes none")
            options.append((name, None))
        elif eq:
            options.append((name, value))
        elif i + 1 < len(args):
            i += 1
            options.append((name, args[i]))
        else:
            found.append(f"the call passes {name} without its value")
        i += 1
    return options, positionals, found


def config_pair(text):
    """(key, value) of a codex -c override, the value with its TOML string quotes stripped."""
    key, _, value = text.partition("=")
    return key.strip(), value.strip().strip('"').strip("'")


def codex_config_values(args, key):
    """Every value a codex -c option gives key (quotes stripped)."""
    options = parse_options(args[1:] if args[:1] == ["exec"] else args, CODEX_OPTIONS)[0]
    return [v for k, v in (config_pair(value) for name, value in options if name in ("-c", "--config")) if k == key]


def memory_switch_problems(room, environ):
    """The CLAUDE.md switch must say what the room's condition says: absent (or empty) in a memory-on room, absent or
    "1" in a memory-off room, where the shim sets it."""
    value = environ.get(MEMORY_SWITCH)
    if room["memory"] == "on" and value:
        return [f"{MEMORY_SWITCH}={value!r} in a memory-on room would switch its memory off"]
    if room["memory"] == "off" and value not in (None, "", "1"):
        return [f"{MEMORY_SWITCH}={value!r} in a memory-off room; the room's switch is 1"]
    return []


def claude_args_problems(room, args):
    """A Claude Code print call's arguments against CLAUDE_OPTIONS and the room: each option at most once, the
    registered model and effort, --tools empty, a known output and input format, --settings exactly CLAUDE_SETTINGS."""
    options, _, found = parse_options(args, CLAUDE_OPTIONS)
    names = [n for n, _ in options]
    found += [f"the call passes {n} more than once" for n in sorted(set(names)) if names.count(n) > 1]
    for name, value in options:
        if name in CLAUDE_VALUES and value not in CLAUDE_VALUES[name]:
            found.append(f"the call passes {name} {value!r}; the contract allows {list(CLAUDE_VALUES[name])}")
        elif name == "--model" and room.get("model") and value != room["model"]:
            found.append(f"the call asks for model {value}, not the registered {room['model']}")
        elif name == "--effort" and value != room["effort"]:
            found.append(f"the call asks for effort {value}, not the registered {room['effort']}")
        elif name == "--max-turns" and not str(value).isdigit():
            found.append(f"the call passes --max-turns {value!r}, not a whole number")
        elif name == "--settings":
            try:
                settings = json.loads(value)
            except ValueError:
                settings = None
            if settings != CLAUDE_SETTINGS:
                found.append(f"the call's --settings is not exactly {json.dumps(CLAUDE_SETTINGS)}")
    return found + claude_mcp_problems(room, [v for n, v in options if n == "--mcp-config"],
                                       [v for n, v in options if n == "--allowedTools"])


def codex_config_problem(room, text):
    """Why one codex -c override is outside the contract, or None. Allowed: web search off, a read-only sandbox, the
    registered reasoning effort, project docs off (only in a memory-off room, and only at 0) and the world's MCP
    server (its command, arguments and approval mode "approve"). No key that carries instructions or a profile."""
    key, value = config_pair(text)
    if key in CODEX_FIXED_CONFIG:
        return None if value == CODEX_FIXED_CONFIG[key] else f"-c {key}={value!r}; the contract allows only " \
                                                            f"{CODEX_FIXED_CONFIG[key]!r}"
    if key == "model_reasoning_effort":
        return None if value == room["effort"] else f"the call asks for reasoning effort {value}, not the " \
                                                    f"registered {room['effort']}"
    if key == "project_doc_max_bytes":
        if room["memory"] == "on":
            return "-c project_doc_max_bytes in a memory-on room would change what memory loads"
        return None if value == "0" else f"-c project_doc_max_bytes={value} in a memory-off room; the room's is 0"
    if CODEX_MCP_KEY.fullmatch(key):
        return None                                 # judged together, against the world server (codex_mcp_problems)
    return f"-c {key} is outside the room's command contract"


def codex_args_problems(room, args):
    """A codex exec call's arguments against CODEX_OPTIONS and the room: exec (or exec resume <session>), the prompt
    on stdin ("-") and no other positional, --ignore-user-config and --ignore-rules present, each option once unless it
    repeats by nature (-c, --disable), every value allowed, and every -c override in the contract."""
    if args[:1] != ["exec"]:
        return ["a Codex subject call must start with exec"]
    rest = args[1:]
    if rest[:1] == ["resume"]:
        if len(rest) < 2 or rest[1].startswith("-"):
            return ["codex exec resume needs a session id"]
        rest = rest[2:]
    options, positionals, found = parse_options(rest, CODEX_OPTIONS)
    if positionals != ["-"]:
        found.append(f"the call's positional arguments are {positionals}; the contract reads the prompt from stdin "
                     "('-') only")
    names = [n for n, _ in options]
    found += [f"the call passes {n} more than once" for n in sorted(set(names))
              if names.count(n) > 1 and n not in CODEX_REPEATABLE]
    found += [f"the call lacks {n}" for n in CODEX_REQUIRED if n not in names]
    for name, value in options:
        if name in CODEX_VALUES and value not in CODEX_VALUES[name]:
            found.append(f"the call passes {name} {value!r}; the contract allows {list(CODEX_VALUES[name])}")
        elif name in ("-m", "--model") and room.get("model") and value != room["model"]:
            found.append(f"the call asks for model {value}, not the registered {room['model']}")
        elif name == "--disable" and not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
            found.append(f"the call passes --disable {value!r}, not a feature name")
        elif name in ("-c", "--config"):
            problem = codex_config_problem(room, value)
            if problem:
                found.append(problem)
    return found + codex_mcp_problems(room, [config_pair(v) for n, v in options if n in ("-c", "--config")])


def common_problems(room, cwd):
    """What every call of a room shares: its directory, the parents, the memory contract and the hard stop."""
    found = []
    if Path(cwd).resolve() != Path(room["work_dir"]).resolve():
        found.append(f"the call runs in {cwd}, not the room's working directory {room['work_dir']}")
    found += [f"{p} exists above the call's directory and would load as memory" for p in parent_memory(cwd)]
    found += memory_problems(room["work_dir"], room["memory_contract"])
    return found


def claude_call_problems(room, environ, cwd, args):
    """Every reason not to start this Claude Code call in this room."""
    found = common_problems(room, cwd)
    found += config_problems(room["config_dir"])
    if os.path.realpath(environ.get("CLAUDE_CONFIG_DIR", "")) != os.path.realpath(room["config_dir"]):
        found.append("CLAUDE_CONFIG_DIR is not the room's")
    found += [f"{k} is set in the call's environment" for k in PROVIDER_VARS if environ.get(k)]
    found += memory_switch_problems(room, environ)
    return found + claude_args_problems(room, args)


def codex_call_problems(room, environ, cwd, args):
    """Every reason not to start this Codex exec call in this room."""
    found = common_problems(room, cwd)
    found += codex_home_problems(environ.get("CODEX_HOME"), room["codex_auth"], room["codex_roots"])
    return found + codex_args_problems(room, args)
