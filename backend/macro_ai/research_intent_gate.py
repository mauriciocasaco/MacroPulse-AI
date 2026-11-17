"""Deterministic admission rules for user-supplied Research questions."""

import re
import unicodedata
from enum import StrEnum


DISALLOWED_CODE = "research_disallowed_request"
DISALLOWED_MESSAGE = "Can't help you with that type of request."


class DisallowedResearchError(ValueError):
    def __init__(self):
        super().__init__(DISALLOWED_MESSAGE)


class ResearchIntent(StrEnum):
    ADMITTED = "admitted"
    CONTROL = "disallowed_control"
    CODE = "disallowed_code"
    EXFILTRATION = "disallowed_exfiltration"


_CONTROL = re.compile(
    r"""\b(?:system|developer|sistema|desarrollador|desenvolvedor)(?: message| mensaje| mensagem)?\s*:\s*"""
    r"""(?:you are|follow|ignore|disable|eres|sos|siga|ignora|desactiv|voce|desativ)"""
    r"""|["']role["']\s*:\s*["'](?:system|developer)["']"""
    r"|\b(?:ignore|ignora(?:d|r)?|ignoren|obvi[ae]|disregard|forget|olvid[ae]|esquec[ae]|"
    r"override|anul[ae]|sobrescreva|bypass|elud[ei]|disable|desactiv[ae]|deshabilit[ae]|desativ[ae])\s+"
    r"(?:(?:all|your|the|todas?|tus|tuas|as|os|las?|los|suas|seus|a|o)\s+)*"
    r"(?:(?:previous|prior|original|system|developer|security|safety|anteriores|previas|"
    r"originais|originales)\s+)*"
    r"(?:instructions?|instrucciones|instrucoes|polic(?:y|ies)|politicas?|"
    r"security (?:checks?|filters?)|filtros? de (?:seguridad|seguranca))\b"
    r"|\b(?:pretend|act as|assume|fing[ei]|finja|simul[ae]|actua como|aja como)\b[^.!?;\n]{0,70}"
    r"\b(?:system administrator|root user|administrador (?:del sistema|do sistema)|developer mode)\b"
    r"|\b(?:grant|conced[ae]|otorg[ae])\s+(?:me\s+)?"
    r"(?:unrestricted access|acceso irrestricto|acesso irrestrito)\b"
)

_EXFILTRATION = re.compile(
    r"\b(?:reveal|show|print|return|encode|display|dump|send|tell|list|find|read|extract|export|"
    r"revela(?:r)?|revele|muestra|mostra(?:r)?|mostre|imprim[aei]|devuelv[ae]|devolv[ae]|"
    r"envi[ae]|manda|mand[ae]|di(?:me)?|diga|list[ae]|le[ae]|leia|extra[ei]g?[ae]|exib[ae])\b"
    r"[^.!?;]{0,100}(?:"
    r"\b(?:system prompt|(?:hidden|internal|developer|system) instructions?|private reasoning|"
    r"prompt (?:interno|oculto|del sistema|do sistema)|instrucciones (?:internas|ocultas|del sistema)|"
    r"instrucoes (?:internas|ocultas|do sistema)|"
    r"internal (?:system )?configuration|configuracion interna|configuracao interna|"
    r"(?:api|signing) keys?|claves? (?:de api|de firma)|chaves? (?:de api|de assinatura)|"
    r"environment variables?|variables? de entorno|variaveis de ambiente|"
    r"connection string|cadena de conexion|string de conexao|"
    r"credentials|credenciales|credenciais|(?:administrator|administrador|internal) secrets?|"
    r"secretos? (?:internos?|del administrador)|segredos? (?:internos?|do administrador)|"
    r"private (?:questions|conversations)|preguntas privadas|perguntas privadas|"
    r"conversaciones (?:ajenas|privadas)|conversas privadas|macropulse_research_origin_keys)\b"
    r"|(?<!\w)\.env\b)"
)

_SQL = re.compile(
    r"\bselect\s+\*\s+from\s+[\w\x22\x60]+"
    r"|\bselect\s+(?:distinct\s+)?[\w.*()]+(?:\s*,\s*[\w.*()]+)*\s+from\s+"
    r"[\w.\x22\x60]+(?=\s*(?:;|$|\b(?:where|join|limit|group by|order by)\b))"
    r"|\b(?:execute|run|ejecut[ae]|execut[ae]|rod[ae])\s+select\b"
    r"|\bselect\s+(?:current_user|session_user|\d+|version\(\)|pg_[a-z_]+\([^;]*\))\s*(?:;|$)"
    r"|(?:drop|truncate)\s+(?:table|schema|database|view|role)\s+[\w\x22\x60]+"
    r"|delete\s+from\s+[\w\x22\x60]+"
    r"|update\s+[\w.\x22\x60]+\s+set\s+[\w\x22\x60]+\s*="
    r"|insert\s+into\s+[\w.\x22\x60]+\s*(?:\([^)]*\)\s*)?(?:values\s*\(|select\s+)"
    r"|(?:grant|revoke)\s+[\w ,]+\s+on\s+[\w .*,]+\s+(?:to|from)\s+\w+"
    r"|(?:create|alter)\s+(?:role|table|schema|database)\s+[\w.\x22\x60]+\s+"
    r"(?:superuser|nosuperuser|add|drop|rename|set|with|password)",
    re.ASCII,
)

_EXECUTION = re.compile(
    r"(?:^|[.!?;:\n]|\b(?:and|then|before|please|y|luego|despues|e|depois|"
    r"can you|could you|puedes|voce pode)\s+)\s*(?:(?:first|also|ahora|agora|por favor)\s+)*"
    r"\b(?:run|execute|eval(?:uate)?|invoke|write|generate|provide|create|"
    r"ejecut[ae]|ejecutar|corre|escrib[aei]|gener[ae]|cre[ae]|"
    r"execut[ae]|executar|rod[ae]|escrev[ae]|ger[ae]|cri[ae]|forneca)\s+"
    r"(?:(?:a|an|the|this|some|un|una|el|la|este|esse|um|uma|o)\s+)*"
    r"(?:(?:shell|terminal)\s+)?"
    r"(?:(?:command|comando)\s+(?=(?:curl|wget|sudo|kubectl|docker|ssh|chmod|rm|psql)\b))?"
    r"(?:sql|python|javascript|typescript|bash|powershell|cmd(?:\.exe)?|"
    r"(?:command|comando|script|code|codigo)\b(?=\s*[:\x60]|\s+(?:to|for|para|que|in|em|en)\b)|"
    r"curl|wget|sudo|kubectl|docker|ssh|chmod|rm|psql|node)\b"
    r"|\b(?:decode|decodific[ae]|descifr[ae]|descriptograf[ae])\b[^!?;\n]{0,180}"
    r"\b(?:execute|run|execut[ae]|ejecut[ae]|rod[ae])\b"
    r"|\b(?:restart|stop|shut down|delete|remove|modify|upload|download|open|"
    r"reinici[ae]|apaga|borr[ae]|elimin[ae]|modific[ae]|apague|exclu[ai]|"
    r"abre|abra|sub[ei]|baix[ae])\s+(?:(?:the|all|that|el|la|los|as|o|os|um|un|este)\s+)*"
    r"(?:backend|servers?|files?|databases?|tools?|services?|servidor(?:es)?|archivos?|arquivos?|"
    r"base de datos|banco de dados|servicios?|servicos?)\b"
)

_CODE = re.compile(
    r"\b(?:const|let|var|export)\s+[$a-z_][\w$]*\s*=(?!=)"
    r"|\b(?:define|declare|set|assign|defin[aei]|declar[ae]|asign[ae]|atribu[ai])\s+"
    r"(?:(?:a|the|la|una|uma)\s+)?vari(?:able|avel)\s+[$a-z_][\w$]*\s*=(?!=)"
    r"|(?:^|[.!?;:\n\x60])\s*[a-z_]\w*\s*=(?!=)\s*"
    r"""(?:[+-]?\d+(?:\.\d+)?|true|false|null|none|"[^"\n]*"|'[^'\n]*')\s*(?=$|[;!\n\x60]|\.(?:\s|$))"""
    r"|\b(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*:"
    r"|(?:^|[;:\n\x60])\s*(?:import\s+[a-z_][\w.]*|from\s+[a-z_][\w.]*\s+import\s+"
    r"[a-z_][\w.]*)\s*(?=[;\n\x60]|$)"
    r"|\b(?:print|exec|eval|__import__)\s*\([^)\n]+\)"
    r"|\b(?:process\.env|os\.(?:environ|getenv|system))\b"
    r"|(?:^|[!?;:\n\x60])\s*(?:\$\s+)?(?:"
    r"rm\s+-[\w-]+\s+\S+|curl\s+(?:-\S|https?://)|get-childitem\s+\S+|"
    r"remove-item\s+\S+|(?:sudo|chmod|kubectl|docker|ssh|psql)\s+[-\w./])",
    re.ASCII,
)


def _detection_copy(question: str) -> str:
    characters = (
        char for char in question
        if unicodedata.category(char) != "Cf" and char not in "\u034f\ufe0e\ufe0f"
    )
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", "".join(characters)).casefold())
    # Strip accents for multilingual rules and preserve command boundaries.
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^\S\n]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def classify_research_intent(question: str) -> ResearchIntent:
    text = _detection_copy(question)
    if _CONTROL.search(text):
        return ResearchIntent.CONTROL
    if _EXFILTRATION.search(text):
        return ResearchIntent.EXFILTRATION
    if any(pattern.search(text) for pattern in (_SQL, _EXECUTION, _CODE)):
        return ResearchIntent.CODE
    return ResearchIntent.ADMITTED


def require_admitted_research(question: str) -> None:
    if classify_research_intent(question) != ResearchIntent.ADMITTED:
        raise DisallowedResearchError
