"""Optional current-news research using the OpenAI Responses API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI, OpenAIError

from quantlab.config import settings


@dataclass(frozen=True)
class NewsResearchResult:
    text: str
    sources: tuple[tuple[str, str], ...]
    model: str
    call_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


def _citation_sources(response: Any) -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    for output_item in getattr(response, "output", ()):
        if getattr(output_item, "type", None) != "message":
            continue
        for content in getattr(output_item, "content", ()):
            if getattr(content, "type", None) != "output_text":
                continue
            for citation in getattr(content, "annotations", ()):
                if getattr(citation, "type", None) != "url_citation":
                    continue
                url = getattr(citation, "url", "")
                title = getattr(citation, "title", "") or url
                if url and (title, url) not in sources:
                    sources.append((title, url))
    return tuple(sources)


def research_news(ticker: str) -> NewsResearchResult:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add a rotated key to the local .env file.")

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        response = client.responses.create(
            model=settings.openai_model,
            tools=[{"type": "web_search"}],
            instructions=(
                "Research recent, publicly available news that could affect the requested ticker. "
                "Separate sourced facts from interpretation. Give dates and source links when available. "
                "Do not make a price target or a buy/sell recommendation. State uncertainty and missing context."
            ),
            input=f"Find and summarize relevant recent news and risks for ticker {ticker}. Focus on the last 30 days.",
        )
    except OpenAIError as exc:
        # Avoid echoing arbitrary SDK error text, which can contain request metadata.
        raise RuntimeError(f"OpenAI news request failed ({type(exc).__name__}).") from None

    usage = getattr(response, "usage", None)
    return NewsResearchResult(
        text=response.output_text.strip(),
        sources=_citation_sources(response),
        model=settings.openai_model,
        call_count=1,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def format_news_note(ticker: str, result: NewsResearchResult) -> str:
    lines = [f"# {ticker} 新闻研究", "", result.text]
    if result.sources:
        lines.extend(["", "## 来源", ""])
        lines.extend(f"- [{title}]({url})" for title, url in result.sources)
    lines.extend(
        [
            "",
            "## 调用记录",
            "",
            f"- 模型：`{result.model}`",
            f"- API 调用次数：{result.call_count}",
            f"- Token：输入 {result.input_tokens if result.input_tokens is not None else '未知'}，"
            f"输出 {result.output_tokens if result.output_tokens is not None else '未知'}，"
            f"总计 {result.total_tokens if result.total_tokens is not None else '未知'}",
        ]
    )
    return "\n".join(lines) + "\n"
