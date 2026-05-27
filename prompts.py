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


def build_system_prompt(
    agent_prompt: str,
    system_override: str = None,
    caveman: bool = True,
) -> str:
    parts = []
    if caveman:
        parts.append(CAVEMAN_ULTRA)
    parts.append(system_override if system_override else agent_prompt)
    return "\n\n".join(parts)
