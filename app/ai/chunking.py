"""Segmentação com janela limitada e sobreposição explícita para indexação textual."""

import re


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> list[str]:
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError("max_chars deve ser positivo e overlap menor que a janela")
    normalized = " ".join(text.split())
    output: list[str] = []
    offset = 0
    while offset < len(normalized):
        stop = min(len(normalized), offset + max_chars)
        if stop < len(normalized):
            # Prefere frase completa somente quando o corte ainda permite avançar.
            candidates = [
                offset + match.end()
                for match in re.finditer(r"[.!?](?=\s)", normalized[offset:stop])
            ]
            viable = [
                position
                for position in candidates
                if position - offset > max(overlap, max_chars // 2)
            ]
            if viable:
                stop = viable[-1]
        fragment = normalized[offset:stop].strip()
        if fragment:
            output.append(fragment)
        if stop == len(normalized):
            return output
        offset = stop - overlap
    return output
