"""local_ai.classify_complexity. See ../../capabilities/local_ai/classify_complexity.md.

Ported from reference-repos/Linkr/Lynkr's native/src/lib.rs `analyze_complexity_native` (Rust,
regex-pattern-based, no ML/embeddings) -- NOT from knn-router.js, which is a separate, optional
layer requiring a persisted embedding index warmed up from real production telemetry (hnswlib,
nomic-embed-text). That layer is deliberately not ported: it needs infrastructure (an embedding
model, an online-learned index) this project doesn't have deployed, and building automatic model
routing on top of it is explicitly against current policy (local-ai-operating-rules.md #6: "Do
not build automatic model routing yet"). This capability only SCORES -- like registry.find, it
never routes or executes anything itself, so it doesn't touch that policy at all.
"""
from __future__ import annotations

import re

from registry import tool

_FLAGS = re.IGNORECASE

GREETING_RE = re.compile(
    r"^(hi|hello|hey|thanks?|bye|goodbye|good morning|good evening|good afternoon|good night|"
    r"howdy|greetings|welcome)\b", _FLAGS)
YES_NO_RE = re.compile(
    r"^(yes|no|ok|okay|sure|y|n|yep|nope|yea|nah|affirmative|negative|roger|copy)\s*[.!?]*$", _FLAGS)
SIMPLE_QUESTION_RE = re.compile(
    r"^(what|where|when|who|how|why|which|is|are|do|does|can|could|will|would|should)\b.{0,80}[?]?\s*$",
    _FLAGS)
TECHNICAL_RE = re.compile(
    r"\b(function|class|module|import|export|async|await|promise|api|database|server|client|"
    r"component|interface|struct|enum|trait|impl|const|let|var|def|return|throw|catch|try|if|"
    r"else|for|while|loop|match|switch|case)\b", _FLAGS)
SECURITY_RE = re.compile(
    r"\b(security|audit|vulnerab|exploit|injection|xss|csrf|auth|encrypt|decrypt|certificate|"
    r"tls|ssl|oauth|jwt|token|permission|privilege|sanitize|escape|hash|salt)\b", _FLAGS)
ARCHITECTURE_RE = re.compile(
    r"\b(architect|design|pattern|microservice|monolith|scale|distributed|event.?driven|cqrs|"
    r"saga|domain.?driven|hexagonal|clean.?arch|solid|dry|kiss)\b", _FLAGS)
REFACTOR_RE = re.compile(
    r"\b(refactor|restructure|reorganize|rewrite|rearchitect|decompos|extract|consolidat|"
    r"simplif|clean.?up|tech.?debt)\b", _FLAGS)
MULTI_FILE_RE = re.compile(
    r"\b(all files|every file|entire|codebase|project.?wide|across.?the|multiple files|"
    r"several files|many files)\b", _FLAGS)
CONCURRENCY_RE = re.compile(
    r"\b(async|await|concurrent|parallel|thread|mutex|lock|deadlock|race.?condition|"
    r"semaphore|channel|atomic|worker|pool)\b", _FLAGS)
PERFORMANCE_RE = re.compile(
    r"\b(performance|optimize|bottleneck|profil|benchmark|latency|throughput|cache|"
    r"memory.?leak|cpu|heap|gc|garbage)\b", _FLAGS)
DATABASE_RE = re.compile(
    r"\b(database|sql|query|migration|schema|index|transaction|join|aggregate|stored.?proc|"
    r"trigger|view|orm|sequelize|prisma|knex|typeorm)\b", _FLAGS)
REASONING_RE = re.compile(
    r"\b(step.?by.?step|think.*through|analyz|compar|trade.?off|pros?.?and?.?cons|evaluat|"
    r"assess|consider|weigh|reason|logic|deduc)\b", _FLAGS)

FORCE_CLOUD_PATTERNS = [
    re.compile(r"\bsecurity\s+(audit|review)\b", _FLAGS),
    re.compile(r"\barchitect(ure)?\s+(design|review)\b", _FLAGS),
    re.compile(r"\b(complete|full|entire)\s+codebase\s+refactor", _FLAGS),
    re.compile(r"\bcode\s+review\b", _FLAGS),
    re.compile(r"\bpr\s+review\b", _FLAGS),
    re.compile(r"\bcomplex\s+debug", _FLAGS),
    re.compile(r"\bproduction\s+(incident|outage|issue)\b", _FLAGS),
    # Added 2026-09-24, diverging from the original Lynkr port -- found live via a stress-test
    # battery that the literal patterns above missed two dangerous, very natural phrasings:
    # "please review our auth system for vulnerabilities" (score 14, "local") and "help me debug
    # this race condition deadlock in production" (score 8, "local"). Both are exactly the kind
    # of high-stakes task that should never read as trivial. Broader, keyword-based rather than
    # exact-phrase, on purpose -- the exact-phrase style above is precisely what missed these.
    re.compile(r"\b(review|audit|check|assess)\w*\b.{0,60}\bvulnerab", _FLAGS),
    re.compile(r"\bdeadlock\b", _FLAGS),
    re.compile(r"\brace.?condition\b", _FLAGS),
]
FORCE_LOCAL_PATTERNS = [
    re.compile(r"^(hi|hello|hey|thanks?|bye|goodbye)\s*[.!?]*$", _FLAGS),
    re.compile(r"^what\s+time\s+is\s+it", _FLAGS),
    re.compile(r"^(yes|no|ok|okay|sure|y|n)\s*[.!?]*$", _FLAGS),
    re.compile(r"^(help|commands?|menu)\s*[.!?]*$", _FLAGS),
]


def _score_band(value: int, bands: list[tuple[int, int]]) -> int:
    for threshold, score in bands:
        if value < threshold:
            return score
    return bands[-1][1]


@tool(name="classify_complexity", category="local_ai", doc="local_ai/classify_complexity.md")
def classify_complexity(text: str, token_estimate: int = 0, tool_count: int = 0) -> dict:
    """Score a request's complexity 0-100 using a fast, deterministic, offline heuristic — no
    model call, no embeddings, no network. Advisory only: like registry.find, this never routes
    or executes anything itself, it just reports a number for a human (or a future,
    not-yet-built routing layer) to act on.

    Args:
        text: the request text to score.
        token_estimate: rough token count of the full context (prompt + history), if known.
        tool_count: number of tools available/likely to be invoked, if known.

    Returns:
        {"score": 0-100, "force_local": bool, "force_cloud": bool,
         "token_score", "tool_score", "task_type_score", "code_complexity_score",
         "reasoning_score"}. force_local short-circuits everything else to score=0 (a plain
        greeting/ack/time-check). force_cloud floors the score at 76 (matches patterns that
        should never be treated as trivial regardless of length, e.g. "security audit").
        Higher score = more complex = a better candidate for escalation once this project
        actually builds an escalation policy (not yet — see local-ai-operating-rules.md #6).

    Raises:
        Nothing — an empty/whitespace-only text scores as a force_local greeting-shaped miss,
        same as any other non-matching short string; not treated as an error.
    """
    content = text or ""

    if any(p.search(content) for p in FORCE_LOCAL_PATTERNS):
        return {
            "score": 0, "force_local": True, "force_cloud": False,
            "token_score": 0, "tool_score": 0, "task_type_score": 0,
            "code_complexity_score": 0, "reasoning_score": 0,
        }

    force_cloud = any(p.search(content) for p in FORCE_CLOUD_PATTERNS)

    token_score = _score_band(token_estimate, [(500, 0), (1000, 4), (2000, 8), (4000, 12), (8000, 16), (0, 20)])
    tool_score = _score_band(tool_count, [(1, 0), (4, 4), (7, 8), (11, 12), (16, 16), (0, 20)])

    if GREETING_RE.search(content) or YES_NO_RE.search(content):
        task_type_score = 0
    elif SIMPLE_QUESTION_RE.search(content):
        task_type_score = 3
    elif REFACTOR_RE.search(content):
        task_type_score = 16
    elif MULTI_FILE_RE.search(content):
        task_type_score = 22
    elif force_cloud:
        task_type_score = 25
    elif TECHNICAL_RE.search(content):
        task_type_score = 10
    else:
        task_type_score = 5

    code_score = 0
    if MULTI_FILE_RE.search(content):
        code_score += 5
    if ARCHITECTURE_RE.search(content):
        code_score += 5
    if SECURITY_RE.search(content):
        code_score += 4
    if CONCURRENCY_RE.search(content):
        code_score += 3
    if PERFORMANCE_RE.search(content):
        code_score += 3
    if DATABASE_RE.search(content):
        code_score += 3
    code_complexity_score = min(code_score, 20)

    reasoning_score = 4 if REASONING_RE.search(content) else 0

    total = min(token_score + tool_score + task_type_score + code_complexity_score + reasoning_score, 100)
    score = max(total, 76) if force_cloud else total

    return {
        "score": score,
        "force_local": False,
        "force_cloud": force_cloud,
        "token_score": token_score,
        "tool_score": tool_score,
        "task_type_score": task_type_score,
        "code_complexity_score": code_complexity_score,
        "reasoning_score": reasoning_score,
    }
