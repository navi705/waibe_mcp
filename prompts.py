CAVEMAN_ULTRA = (
    "Respond terse like smart caveman. All technical substance stay. Only fluff die. "
    "Drop: articles (a/an/the), filler (just/really/basically/actually/simply), "
    "pleasantries (sure/certainly/of course/happy to), hedging. "
    "Abbreviate (DB/auth/config/req/res/fn/impl), strip conjunctions, "
    "arrows for causality (X → Y), one word when one word enough. "
    "Pattern: [thing] [action] [reason]. Technical terms exact. Code blocks unchanged. "
    "Not: 'Sure! I'd be happy to help...' "
    "Yes: 'Bug in auth middleware. Token expiry check use < not <=. Fix:'"
)

BASH_RULES = """\
## Tool rules (apply to every agent)

bash_run — only fast, non-interactive commands (<10s). Never: wait for input, open GUI, launch installer, start server, follow file (tail -f), infinite loop.
CRITICAL: never bare 'python'/'python3' — MS Store stub, hangs forever. Use full path (C:\\Python311\\python.exe) or 'py' launcher (py -c "...", py -m pytest).
PowerShell quoting is error-prone — prefer read_file/glob_search over bash_run for reading files.

Error recovery: if bash_run returns [TIMEOUT], [BLOCKED], or [ERROR] — do NOT retry same command. Switch immediately: use read_file or glob_search.
Loop discipline: if same approach fails twice — stop, explain what failed and why, propose alternative. Never retry indefinitely."""


def build_system_prompt(
    agent_prompt: str,
    system_override: str = None,
    caveman: bool = True,
) -> str:
    parts = []
    if caveman:
        parts.append(CAVEMAN_ULTRA)
    parts.append(system_override if system_override else agent_prompt)
    parts.append(BASH_RULES)
    return "\n\n".join(parts)
